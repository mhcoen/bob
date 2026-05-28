"""Fan-out parent/child execution, cancellation, resume."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Literal

from orchestra.adapters.base import Adapter
from orchestra.errors import ExecutorError
from orchestra.executor._executor_common import (
    FanOutSnapshot,
    _CancellationRegistry,
    _envelope_to_view,
    _error_to_dict,
    _now_iso,
)
from orchestra.spine import (
    Envelope,
    ErrorRecord,
    PreparedInvocation,
    StateDecl,
)
from orchestra.visibility import make_invocation_id


class _FanOutMixin:
    """Mixin: Fan-out parent/child execution, cancellation, resume."""

    # ----- fan-out execution -------------------------------------

    def _run_fan_out_group(
        self,
        parent_state: StateDecl,
        parent_envelope: Envelope,
        transition: Any,
    ) -> str:
        """Run a fan_out group and return the next state target.

        The parent state's ``state_exit`` is already durable. This
        method walks the fan-out lifecycle:

        1. Capture an immutable snapshot of pre-fan-out envelopes and
           the visible artifact store (via ``read_latest``) under the
           ``LogWriter -> ArtifactStore`` ordering rule.
        2. Append ``fan_out_start`` inside the same critical section.
        3. Submit one future per child state through a
           ``ThreadPoolExecutor``. Each child's worker mints a new
           invocation_id and runs the full per-state sequence ending
           at a durable ``state_exit``.
        4. Drain via ``as_completed``. On the first child error,
           cancel pending futures and continue to drain the running
           ones.
        5. Determine the aggregate outcome (any error -> error_target).
        6. Run the post-fan-out cleanup pass: purge committed
           ``state_invocation`` rows whose producing invocation is
           not ``success``.
        7. Append ``fan_out_end`` carrying the routing decision.
        8. Return the target state name.
        """
        # Step 1 + 2: snapshot capture and fan_out_start under the
        # LogWriter-then-store lock-ordering rule.
        #
        # Cleanup 2: fan_out_start is appended while BOTH locks are
        # held (LogWriter outer, store inner). Releasing the store
        # lock before the log write would let another thread mutate
        # the store between snapshot capture and the durable
        # fan_out_start record; per the spec the snapshot and the
        # log record sit in one critical section.
        with self._log.lock:
            with self._store.lock:
                snapshot_envelopes = {
                    name: _envelope_to_view(env) for name, env in self._envelopes.items()
                }
                snapshot_artifacts: dict[str, Any] = {}
                for art in self._wf.artifacts:
                    v = self._store.read_latest(art.name)
                    if v is not None:
                        snapshot_artifacts[art.name] = v.value
                # Pass-6 fix: capture attempts and retries at
                # fan_out_start so child guards see deterministic
                # sibling-counter values, not whatever the live
                # _attempts/_retries dicts hold by the time a worker
                # thread evaluates its transition.
                with self._attempt_lock:
                    snapshot_attempts = dict(self._attempts)
                    snapshot_retries = dict(self._retries)
                self._log.write(
                    "fan_out_start",
                    state_id=parent_state.name,
                    attempt=parent_envelope.attempt,
                    fields={
                        "parent_state": parent_state.name,
                        "children": list(transition.fan_out),
                        "join_target": transition.target,
                        "error_target": transition.error_target,
                    },
                )
        # Surface the parallel-group start to the progress reporter
        # AFTER the durable log write but BEFORE worker dispatch so
        # the user sees the parallel header before any per-child
        # state_enter line. children carries each child's state name
        # plus its declared role; the api wrapper enriches each pair
        # with the resolved adapter and model.
        children_with_roles: tuple[tuple[str, str | None], ...] = tuple(
            (child_name, self._wf.state(child_name).role) for child_name in transition.fan_out
        )
        self._emit_progress("fan_out_start", parent_state, children=children_with_roles)

        registry = _CancellationRegistry()
        for child_name in transition.fan_out:
            registry.register_pending(child_name)

        # Cleanup 1: do NOT pre-seed ``_attempts``. The reentrant
        # ``_execute_state_body`` increments per-state at
        # ``state_enter`` time. Reset ``_retries`` for first entry;
        # the per-entry retry budget starts at 0 here.
        with self._attempt_lock:
            for child_name in transition.fan_out:
                self._retries[child_name] = 0

        futures: dict[str, Future[Envelope]] = {}
        executor = ThreadPoolExecutor(
            max_workers=max(1, len(transition.fan_out)),
            thread_name_prefix="orchestra-fan-out",
        )
        stop_fan_out_watchdog = self._start_progress_watchdog(
            "fan_out_progress",
            parent_state,
            self._fan_out_progress_interval_seconds,
        )
        try:
            for child_name in transition.fan_out:
                fut = executor.submit(
                    self._fan_out_child_worker,
                    child_name,
                    registry,
                    snapshot_envelopes,
                    snapshot_artifacts,
                    snapshot_attempts,
                    snapshot_retries,
                )
                futures[child_name] = fut

            # Drain. On first error, cancel pending futures.
            child_outcomes: dict[str, str] = {}
            child_invocation_ids: dict[str, str] = {}
            group_errored = False
            from concurrent.futures import as_completed

            for fut in as_completed(futures.values()):
                # Map future back to child name.
                child_name = next(name for name, f in futures.items() if f is fut)
                try:
                    envelope = fut.result()
                except Exception as exc:
                    # The worker should have written its own
                    # state_exit; defensive fallback for unexpected
                    # crashes records the error here.
                    child_outcomes[child_name] = "error"
                    self._log.write(
                        "fan_out_child_crash",
                        state_id=parent_state.name,
                        fields={
                            "child": child_name,
                            "error": str(exc),
                        },
                    )
                    group_errored = True
                    registry.request_cancel_all(futures)
                    continue
                # Re-audit P2: use ``envelope.attempt`` (the FINAL
                # attempt after any child-local retries) rather than
                # ``per_child_attempt[child_name]`` (which captures
                # only the initial attempt). The state_exit and
                # artifact commits are keyed to the final attempt;
                # the aggregate must agree so downstream tooling can
                # correlate the per-child invocation_ids back to
                # the durable records.
                inv_id = make_invocation_id(self._run_id, child_name, envelope.attempt)
                child_invocation_ids[child_name] = inv_id
                outcome = "success" if envelope.status == "ok" else "error"
                child_outcomes[child_name] = outcome
                if outcome == "error" and not group_errored:
                    group_errored = True
                    registry.request_cancel_all(futures)
        finally:
            executor.shutdown(wait=True)
            stop_fan_out_watchdog()

        # Aggregate outcome.
        aggregate: Literal["success", "error"] = "error" if group_errored else "success"
        target = transition.error_target if aggregate == "error" else transition.target
        # Cleanup pass: purge state_invocation rows whose producing
        # invocation is not success. Idempotent.
        purged = self._store.purge_invisible_state_invocation_versions()
        # fan_out_end log record.
        self._log.write(
            "fan_out_end",
            state_id=parent_state.name,
            attempt=parent_envelope.attempt,
            fields={
                "parent_state": parent_state.name,
                "aggregate": aggregate,
                "per_child_outcome": child_outcomes,
                "child_invocation_ids": child_invocation_ids,
                "target": target,
                "purged_versions": purged,
            },
        )
        # Close the parallel block in the progress reporter. The
        # reporter computes parallel wall-clock from the maximum of
        # the per-child state_exit elapsed_seconds it observed, so
        # the executor does not need to compute it here.
        self._emit_progress("fan_out_end", parent_state)
        return str(target)

    def _fan_out_child_worker(
        self,
        child_name: str,
        registry: _CancellationRegistry,
        snapshot_envelopes: dict[str, dict[str, Any]],
        snapshot_artifacts: dict[str, Any],
        snapshot_attempts: dict[str, int],
        snapshot_retries: dict[str, int],
    ) -> Envelope:
        """Run one fan-out child to a durable ``state_exit``.

        Workers do NOT mutate ``self._current_state``,
        ``self._step_count``, or write outgoing transition records;
        the fan-out controller owns those. Artifact reads and prompt
        template substitutions consume the captured snapshot, never
        the live store, so a sibling write that lands mid-fan-out is
        invisible.

        Cleanup 1: workers no longer accept a pre-minted ``attempt``.
        ``_execute_state_body`` increments ``_attempts[child_name]``
        and mints the invocation_id at ``state_enter`` time. The
        envelope's ``attempt`` field is the source of truth for the
        retry loop and any downstream invocation_id reconstruction.

        Slice A child-local retry: when the per-state body returns
        an error or timeout envelope and the child's state declares
        ``on error retry max N then T``, the worker increments
        ``retries[child_name]`` under ``_attempt_lock`` and re-calls
        the body. The body's next invocation increments
        ``_attempts[child_name]`` again, producing a monotonic
        per-state attempt counter. The per-entry budget rule applies:
        ``retries[child_name]`` starts at 0 on first entry (controller
        resets it in ``_run_fan_out_group``). The
        fresh-budget-on-replay rule is separate and handled by the
        resume path.
        """
        snapshot = FanOutSnapshot(
            envelopes=dict(snapshot_envelopes),
            artifacts=dict(snapshot_artifacts),
            attempts=dict(snapshot_attempts),
            retries=dict(snapshot_retries),
        )
        state = self._wf.state(child_name)
        if state.actor.kind == "transform":
            # Slice B fix: transform children have no adapter to look
            # up, prepare, or cancel. ``adapter_for("transform")``
            # would raise because transform is not an actor backing.
            # Skip the cancellation registry's prepare-side hooks
            # (there is no in-flight adapter call) and dispatch
            # directly to ``_execute_state_body`` which routes to
            # ``_execute_transform_body``. Transforms do not retry.
            if registry.is_cancelled(child_name):
                envelope = self._write_cancelled_state_exit(child_name)
                registry.mark_done(child_name)
                return envelope
            envelope = self._execute_state_body(
                child_name,
                snapshot=snapshot,
                suppress_actor_progress=True,
            )
            registry.mark_done(child_name)
            return envelope
        adapter = self._registry.adapter_for(state.actor.kind)
        while True:
            if registry.is_cancelled(child_name):
                return self._write_cancelled_state_exit(child_name)

            def _on_prepared(
                prepared: PreparedInvocation,
                invocation_id: str,
                _name: str = child_name,
                _adapter: Adapter = adapter,
            ) -> None:
                registry.mark_started(_name, invocation_id, prepared, _adapter)

            def _is_cancelled_after_register(
                _name: str = child_name,
            ) -> bool:
                return registry.is_cancelled(_name)

            envelope = self._execute_state_body(
                child_name,
                snapshot=snapshot,
                on_prepared=_on_prepared,
                is_cancelled_after_register=_is_cancelled_after_register,
                suppress_actor_progress=True,
            )
            # Pass-5 fix #3: route the child outcome through the same
            # first-match selection the linear path uses, then retry
            # only if the selected transition is itself a retry
            # transition with budget remaining. The pre-fix code
            # scanned every transition for one with the right outcome
            # AND retry_max set, ignoring declaration order and
            # picking up a retry transition declared AFTER a non-retry
            # transition for the same outcome (e.g. `on error => stop`
            # followed by `on error retry max 1 then stop`). That
            # routes the child to retry when the workflow author
            # explicitly chose `=> stop` first.
            selected = self._select_transition_decl(state, envelope, snapshot=snapshot)
            should_retry = False
            if selected is not None and selected.retry_max is not None:
                with self._attempt_lock:
                    used = self._retries.get(child_name, 0)
                    if used < selected.retry_max:
                        self._retries[child_name] = used + 1
                        should_retry = True
            if not should_retry:
                registry.mark_done(child_name)
                return envelope

    def close_fan_out_pending_transition(
        self,
        *,
        parent_state_name: str,
        parent_attempt: int,
        target: str,
    ) -> str:
        """Replay rule for a crash between ``fan_out_end`` and the
        parent ``transition``: the routing decision is durable in the
        ``fan_out_end`` record but the transition log entry was
        never written. Resume must close the missing transition
        without re-running the fan-out group. The parent envelope
        is consulted only for ``outcome``; everything else comes
        from the durable ``fan_out_end`` plus ``parent_attempt``
        threaded from the matching ``fan_out_start``.
        """
        with self._envelope_lock:
            envelope = self._envelopes.get(parent_state_name)
        if envelope is None:
            raise ExecutorError(
                "close_fan_out_pending_transition: no envelope "
                f"reconstructed for parent {parent_state_name!r}"
            )
        return self._close_pending_transition(
            state_id=parent_state_name,
            attempt=parent_attempt,
            outcome=envelope.outcome,
            target=target,
        )

    def _close_resumed_fan_out_transition(
        self,
        parent_state_name: str,
        parent_attempt: int | None,
        target: str,
    ) -> str:
        """Round-3 fix: ``resume_fan_out`` ends with the parent's
        durable ``transition`` record so the resumed log mirrors the
        live path's record sequence and step-count tracking. Falls
        back gracefully when ``parent_attempt`` is missing (older
        log fixtures may have been produced before parent_attempt
        threading landed)."""
        if parent_attempt is None:
            # Defensive: shouldn't occur once parent_attempt threading
            # is in place, but keep current_state advancement so the
            # caller can continue without a transition record.
            self._current_state = target
            return target
        with self._envelope_lock:
            parent_env = self._envelopes.get(parent_state_name)
        outcome = parent_env.outcome if parent_env is not None else "complete"
        return self._close_pending_transition(
            state_id=parent_state_name,
            attempt=parent_attempt,
            outcome=outcome,
            target=target,
        )

        # Loop: the next iteration's ``_execute_state_body``
        # will increment ``_attempts[child_name]`` again.

    def _write_cancelled_state_exit(self, child_name: str) -> Envelope:
        """Emit a state_enter/state_exit pair for a cancelled child
        that never invoked its adapter, so replay sees a complete
        durable record for the invocation.

        Cleanup 1: ``_attempts[child_name]`` is minted HERE so
        cancellation that fires before the body runs still produces
        a monotonic per-state attempt counter. Mirrors
        ``_execute_state_body``'s minting discipline."""
        with self._attempt_lock:
            self._attempts[child_name] = self._attempts.get(child_name, 0) + 1
            attempt = self._attempts[child_name]
        invocation_id = make_invocation_id(self._run_id, child_name, attempt)
        self._visibility_index.insert_pending(invocation_id)
        self._log.write(
            "state_enter",
            state_id=child_name,
            attempt=attempt,
            fields={
                "attempts": {child_name: attempt},
                "retries": {child_name: 0},
                "invocation_id": invocation_id,
                "cancelled": True,
            },
        )
        envelope = Envelope(
            state_id=child_name,
            attempt=attempt,
            actor_binding={},
            status="error",
            outcome="cancelled",
            started_at=_now_iso(),
            ended_at=_now_iso(),
            duration_ms=0,
            inputs_read=[],
            artifacts_written=[],
            payload={},
            error=ErrorRecord(
                kind="cancelled",
                message="cancelled by fan-out controller before adapter invoke",
            ),
        )
        with self._envelope_lock:
            self._envelopes[child_name] = envelope
        # state_exit FIRST, then visibility update (Slice A).
        self._log.write(
            "state_exit",
            state_id=child_name,
            attempt=attempt,
            fields={
                "status": "error",
                "outcome": "cancelled",
                "duration_ms": 0,
                "inputs_read": [],
                "artifacts_written": [],
                "error": _error_to_dict(envelope.error),
                "payload_ref": None,
                "invocation_id": invocation_id,
            },
        )
        self._visibility_index.mark_error(invocation_id)
        self._emit_state_exit_hook(self._wf.state(child_name), envelope, None)
        return envelope

    def resume_fan_out(
        self,
        parent_state_name: str,
        children: list[str],
        join_target: str,
        error_target: str,
        completed_children: dict[str, Envelope],
        parent_attempt: int | None = None,
    ) -> str:
        """Resume an open fan-out group after a crash.

        The original ``fan_out_start`` is durable in the log but no
        ``fan_out_end`` was written, so replay surfaces the group in
        ``ReplayState.open_fan_out``. The resume entry point applies
        the rebuilt VisibilityIndex (BEFORE constructing the
        Executor, see ``cli.cmd_resume``), then dispatches here.

        Children with a durable ``state_exit`` are passed in via
        ``completed_children``. Their outcomes feed into the
        aggregate; their committed artifacts remain visible (the
        VisibilityIndex was rebuilt from the log to reflect this).
        Children listed in ``children`` but not in
        ``completed_children`` are pending: they get a fresh
        ``invocation_id`` (a new ``attempt_seq`` minted under
        ``_attempt_lock``) and re-run. Per the plan's
        fresh-budget-on-replay rule, each re-entered child's
        ``retries`` counter resets to 0.

        After the group finishes, this method writes ``fan_out_end``
        and advances ``_current_state`` to the chosen target so the
        caller can continue with ``run_to_completion``.
        """
        parent_state = self._wf.state(parent_state_name)

        # Re-audit RA-B1: reconstruct the snapshot from currently-
        # visible artifacts and envelopes, but EXCLUDE every
        # completed fan-out sibling from this group. The
        # VisibilityIndex (rebuilt from the log) marks completed
        # siblings as ``success`` so their committed artifacts are
        # visible to ``read_latest`` per the visibility rule. Without
        # filtering, a pending child re-entered after the crash
        # would see its completed siblings' outputs, violating the
        # sibling-visibility rule the snapshot machinery exists to
        # enforce. Other states completed before the fan-out
        # (e.g. the parent that produced the framing artifact)
        # remain visible.
        excluded_sibling_names = set(completed_children.keys())
        excluded_sibling_inv_ids = {
            make_invocation_id(self._run_id, name, env.attempt)
            for name, env in completed_children.items()
        }
        with self._log.lock:
            with self._store.lock:
                snapshot_envelopes = {
                    name: _envelope_to_view(env)
                    for name, env in self._envelopes.items()
                    if name not in excluded_sibling_names
                }
                snapshot_artifacts: dict[str, Any] = {}
                for art in self._wf.artifacts:
                    v = self._store.read_latest(art.name)
                    if v is None:
                        continue
                    if v.invocation_id in excluded_sibling_inv_ids:
                        # A completed fan-out sibling produced this
                        # version. Hide it from the pending child
                        # so the sibling-visibility rule is honored
                        # across resume.
                        continue
                    snapshot_artifacts[art.name] = v.value
                # Pass-6 fix: same counter snapshot as the live path,
                # excluding the completed siblings' counters so a
                # pending child's guard cannot read sibling state via
                # attempts.<sibling> or retries.<sibling>.
                with self._attempt_lock:
                    snapshot_attempts = {
                        n: v for n, v in self._attempts.items() if n not in excluded_sibling_names
                    }
                    snapshot_retries = {
                        n: v for n, v in self._retries.items() if n not in excluded_sibling_names
                    }
            self._log.write(
                "fan_out_resume",
                state_id=parent_state.name,
                attempt=parent_attempt,
                fields={
                    "parent_state": parent_state.name,
                    "children": list(children),
                    "completed": list(completed_children.keys()),
                    "pending": [c for c in children if c not in completed_children],
                    "join_target": join_target,
                    "error_target": error_target,
                },
            )

        pending_children = [c for c in children if c not in completed_children]

        # Seed outcomes from already-completed children; these were
        # committed and durable before the crash.
        child_outcomes: dict[str, str] = {}
        child_invocation_ids: dict[str, str] = {}
        group_errored = False
        for name, env in completed_children.items():
            outcome = "success" if env.status == "ok" else "error"
            child_outcomes[name] = outcome
            child_invocation_ids[name] = make_invocation_id(self._run_id, name, env.attempt)
            if outcome == "error":
                group_errored = True

        # Re-audit RA-B2: if any completed child already errored, the
        # group has already failed. The cancellation race rule says
        # the routing decision is fixed once any child errors. The
        # controller does NOT submit pending children: that would
        # create new fan-out child invocations during replay of an
        # already-failed group, which is the opposite of the
        # contract. Pending children are flagged ``not_launched`` in
        # the per-child outcome map so the log records they were
        # never launched, the cleanup pass runs, and ``fan_out_end``
        # routes to ``error_target``.
        if group_errored:
            for child_name in pending_children:
                child_outcomes[child_name] = "not_launched"
            aggregate: Literal["success", "error"] = "error"
            target = error_target
            purged = self._store.purge_invisible_state_invocation_versions()
            self._log.write(
                "fan_out_end",
                state_id=parent_state.name,
                attempt=parent_attempt,
                fields={
                    "parent_state": parent_state.name,
                    "aggregate": aggregate,
                    "per_child_outcome": child_outcomes,
                    "child_invocation_ids": child_invocation_ids,
                    "target": target,
                    "purged_versions": purged,
                },
            )
            return self._close_resumed_fan_out_transition(parent_state.name, parent_attempt, target)

        registry = _CancellationRegistry()
        for child_name in pending_children:
            registry.register_pending(child_name)

        # Cleanup 1: do NOT pre-seed ``_attempts`` for pending
        # children. The reentrant ``_execute_state_body`` increments
        # per-state at ``state_enter`` time, so a never-entered
        # pending child gets ``attempt_seq=1`` on its first entry
        # rather than an inflated counter from controller pre-seed.
        # Per the plan's fresh-budget-on-replay rule, ``retries``
        # resets to 0 for each re-entered child.
        with self._attempt_lock:
            for child_name in pending_children:
                self._retries[child_name] = 0

        if pending_children:
            futures: dict[str, Future[Envelope]] = {}
            executor = ThreadPoolExecutor(
                max_workers=max(1, len(pending_children)),
                thread_name_prefix="orchestra-fan-out-resume",
            )
            try:
                for child_name in pending_children:
                    fut = executor.submit(
                        self._fan_out_child_worker,
                        child_name,
                        registry,
                        snapshot_envelopes,
                        snapshot_artifacts,
                        snapshot_attempts,
                        snapshot_retries,
                    )
                    futures[child_name] = fut

                from concurrent.futures import as_completed

                for fut in as_completed(futures.values()):
                    child_name = next(name for name, f in futures.items() if f is fut)
                    try:
                        envelope = fut.result()
                    except Exception as exc:
                        child_outcomes[child_name] = "error"
                        self._log.write(
                            "fan_out_child_crash",
                            state_id=parent_state.name,
                            fields={
                                "child": child_name,
                                "error": str(exc),
                            },
                        )
                        group_errored = True
                        registry.request_cancel_all(futures)
                        continue
                    # Re-audit P2: use ``envelope.attempt`` so the
                    # aggregate's per-child invocation_id matches
                    # the final attempt's durable state_exit /
                    # artifact commits when child-local retries
                    # promoted the seq.
                    inv_id = make_invocation_id(
                        self._run_id,
                        child_name,
                        envelope.attempt,
                    )
                    child_invocation_ids[child_name] = inv_id
                    outcome = "success" if envelope.status == "ok" else "error"
                    child_outcomes[child_name] = outcome
                    if outcome == "error" and not group_errored:
                        group_errored = True
                        registry.request_cancel_all(futures)
            finally:
                executor.shutdown(wait=True)

        aggregate = "error" if group_errored else "success"
        target = error_target if aggregate == "error" else join_target
        purged = self._store.purge_invisible_state_invocation_versions()
        self._log.write(
            "fan_out_end",
            state_id=parent_state.name,
            attempt=parent_attempt,
            fields={
                "parent_state": parent_state.name,
                "aggregate": aggregate,
                "per_child_outcome": child_outcomes,
                "child_invocation_ids": child_invocation_ids,
                "target": target,
                "purged_versions": purged,
            },
        )
        return self._close_resumed_fan_out_transition(parent_state.name, parent_attempt, target)
