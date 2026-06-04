"""Result-parser dispatch + tentative artifact writes + per-state schema layer."""

from __future__ import annotations

from typing import Any

from orchestra.executor._executor_base import _ExecutorMixinBase
from orchestra.executor._executor_common import (
    FanOutSnapshot,
    _coerce_to_text,
    _extract_last_json_object,
    _JsonExtractError,
)
from orchestra.executor.criteria import (
    DecisionConsistencyResult,
    check_decision_consistency,
)
from orchestra.payloads import payload_name_from_invocation, write_payload
from orchestra.schema import Invalid, Valid
from orchestra.spine import (
    ArtifactDecl,
    Envelope,
    ErrorRecord,
    PreparedInvocation,
    StateDecl,
)


class _SchemaMixin(_ExecutorMixinBase):
    """Mixin: Result-parser dispatch + tentative artifact writes + per-state schema layer."""

    def _read_artifacts(
        self,
        state: StateDecl,
        *,
        snapshot: FanOutSnapshot | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for r in state.reads:
            if r in self._external:
                out[r] = {"value": self._external[r], "__version_id": ""}
                continue
            if snapshot is not None:
                # Slice A: fan-out children consume the captured
                # snapshot exclusively. Artifacts not in the snapshot
                # (sibling writes that landed mid-fan-out) are not
                # visible to the child, matching the plan's
                # sibling-visibility rule.
                if r in snapshot.artifacts:
                    out[r] = {
                        "value": snapshot.artifacts[r],
                        "__version_id": "snapshot",
                    }
                else:
                    out[r] = {"value": None, "__version_id": ""}
                continue
            art = self._store.read_latest(r)
            if art is not None:
                out[r] = {"value": art.value, "__version_id": art.version_id}
            else:
                out[r] = {"value": None, "__version_id": ""}
        return out

    def _write_payload(self, invocation_id: str, payload: dict[str, Any]) -> str:
        """Persist a payload to disk with fsync.

        Caller treats this as a durability boundary: the payload file
        exists on disk by the time this returns. The log record that
        references it is written after, ensuring no log record points
        at a missing payload. The on-disk encoding is shared with the
        replay-side loader in ``orchestra.payloads`` so resume's
        envelope-hydration path consumes the same format.

        The filename is derived from ``invocation_id`` rather than
        from the log writer's mutable ``seq`` counter so two fan-out
        children completing concurrently cannot collide on the same
        payload file. Each invocation's invocation_id is unique within
        a run.
        """
        return write_payload(
            self._payloads_dir,
            payload_name_from_invocation(invocation_id),
            payload,
        )

    def _dispatch_parsers(
        self,
        state: StateDecl,
        prepared: PreparedInvocation,
        payload: dict[str, Any],
        attempt: int,
        invocation_id: str | None = None,
    ) -> list[str]:
        """Run applicable parsers and produce tentative writes.

        Returns the list of tentative handles that ``commit_tentative``
        will promote on success.

        Writes whose target is handled by the schema layer (the
        schema-backed json artifact and any of its declared extraction
        targets) are filtered out before the parser dispatch: the
        schema layer commits them directly in the same transaction
        as the json write, after this method returns.
        """
        parser_writes = tuple(
            w for w in state.writes if w.name not in self._schema_handled_artifacts
        )
        write_types = tuple(w.type for w in parser_writes)
        parsers = self._registry.parsers_for(backing=state.actor.kind, artifact_types=write_types)
        if not parsers:
            return []
        envelope_for_parser = Envelope(
            state_id=state.name,
            attempt=attempt,
            actor_binding={},
            status="ok",
            outcome="complete",
            started_at="",
            ended_at="",
            duration_ms=0,
            inputs_read=[],
            artifacts_written=[],
            payload={
                **payload,
                "_declared_writes": [{"name": w.name, "type": w.type} for w in parser_writes],
            },
            error=None,
        )
        handles: list[str] = []
        for parser in parsers:
            self._log.write(
                "parser_run",
                state_id=state.name,
                attempt=attempt,
                fields={"parser": parser.name},
            )
            try:
                pairs = parser.fn(envelope_for_parser, self._store)
                for name, value in pairs:
                    handle = self._store.tentative_write(
                        name,
                        value,
                        written_by=f"{state.name}#{attempt}",
                        invocation_id=invocation_id,
                    )
                    handles.append(handle)
            except Exception:
                # Discard handles staged so far before re-raising. The
                # caller will discard the empty list and set status to
                # error.
                if handles:
                    self._store.discard_tentative(handles)
                    handles.clear()
                raise
        return handles

    def _artifact_writes_record(
        self,
        state: StateDecl,
        prepared: PreparedInvocation | None,
        payload: dict[str, Any],
        committed_ids: list[str],
    ) -> list[dict[str, str]]:
        """Pair committed version IDs with their artifact names by
        re-running the parsers in their declared order. Parsers are
        pure, so re-running is safe and cheap.

        The ``schema_handle_names`` field on the payload (set by
        ``_apply_schema_layer`` when a schema-backed write fires) lists
        the schema-layer-committed artifact names in their declared
        order; those names are appended after any parser-produced
        names so the (name, version_id) pairing matches the order of
        ``commit_tentative``'s input.
        """
        parser_writes = tuple(
            w for w in state.writes if w.name not in self._schema_handled_artifacts
        )
        write_types = tuple(w.type for w in parser_writes)
        parsers = self._registry.parsers_for(backing=state.actor.kind, artifact_types=write_types)
        envelope_for_parser = Envelope(
            state_id=state.name,
            attempt=0,
            actor_binding={},
            status="ok",
            outcome="complete",
            started_at="",
            ended_at="",
            duration_ms=0,
            inputs_read=[],
            artifacts_written=[],
            payload={
                **payload,
                "_declared_writes": [{"name": w.name, "type": w.type} for w in parser_writes],
            },
            error=None,
        )
        names: list[str] = []
        for parser in parsers:
            for name, _ in parser.fn(envelope_for_parser, self._store):
                names.append(name)
        schema_names = payload.get("_schema_handle_names")
        if isinstance(schema_names, list):
            for n in schema_names:
                if isinstance(n, str):
                    names.append(n)
        out: list[dict[str, str]] = []
        for n, vid in zip(names, committed_ids, strict=False):
            out.append({"artifact": n, "version_id": vid})
        return out

    def _state_schema_artifact(self, state: StateDecl) -> ArtifactDecl | None:
        """Return the schema-backed json artifact this state writes,
        or ``None`` when the state has no schema-backed write.

        At most one schema-backed write per state (enforced by the
        validator), so this returns either the single matching
        ArtifactDecl or None.
        """
        for w in state.writes:
            if w.name in self._schema_artifacts:
                return self._schema_artifacts[w.name]
        return None

    def _apply_schema_layer(
        self,
        state: StateDecl,
        payload: dict[str, Any],
        attempt: int,
        invocation_id: str,
        existing_handles: list[str],
        payload_ref: str | None,
    ) -> tuple[list[str], str | None, ErrorRecord | None]:
        """Schema-verdict 1.4: parse the model output as JSON, validate
        against the artifact's schema, tentative-write the json
        artifact and any declared extractions atomically (returned to
        the caller as additional handles), and emit a
        ``schema_validation`` log record.

        Returns ``(new_handles, decision_outcome, error_record)``:

        - ``new_handles``: tentative handles to append to the existing
          parser handles. Empty when validation failed or no schema
          applies.
        - ``decision_outcome``: the schema's decision string when
          validation passes; ``None`` otherwise.
        - ``error_record``: an ``ErrorRecord`` carrying ``reason`` in
          ``detail`` (``"json_parse"`` or ``"schema_violation"``) when
          validation fails; ``None`` on success or when no schema
          applies.

        On any failure the caller is expected to discard
        ``existing_handles`` and route to the state's ``error``
        outcome.
        """
        artifact = self._state_schema_artifact(state)
        if artifact is None:
            return [], None, None
        spec = self._schema_specs[artifact.name]
        raw_output = payload.get("output")
        if not isinstance(raw_output, str):
            err = ErrorRecord(
                kind="actor_failure",
                message=(
                    "schema-backed state requires a string 'output' "
                    f"field on the model payload, got {type(raw_output).__name__}"
                ),
                detail={
                    "reason": "json_parse",
                    "phase": "schema_validation",
                },
            )
            self._log.write(
                "schema_validation",
                state_id=state.name,
                attempt=attempt,
                fields={
                    "artifact": artifact.name,
                    "outcome": "parse_error",
                    "decision": None,
                    "validation_errors": ["payload.output is not a string"],
                    "payload_ref": payload_ref,
                    "invocation_id": invocation_id,
                },
            )
            return [], None, err
        try:
            parsed = _extract_last_json_object(raw_output)
        except _JsonExtractError as exc:
            err = ErrorRecord(
                kind="actor_failure",
                message=f"json parse error: {exc}",
                detail={
                    "reason": "json_parse",
                    "phase": "schema_validation",
                    "exception": "JsonExtractError",
                },
            )
            self._log.write(
                "schema_validation",
                state_id=state.name,
                attempt=attempt,
                fields={
                    "artifact": artifact.name,
                    "outcome": "parse_error",
                    "decision": None,
                    "validation_errors": [str(exc)],
                    "payload_ref": payload_ref,
                    "invocation_id": invocation_id,
                },
            )
            return [], None, err
        result = spec.validate(parsed)
        if isinstance(result, Invalid):
            err = ErrorRecord(
                kind="actor_failure",
                message="schema validation failed",
                detail={
                    "reason": "schema_violation",
                    "phase": "schema_validation",
                    "errors": list(result.errors),
                },
            )
            self._log.write(
                "schema_validation",
                state_id=state.name,
                attempt=attempt,
                fields={
                    "artifact": artifact.name,
                    "outcome": "schema_error",
                    "decision": None,
                    "validation_errors": list(result.errors),
                    "payload_ref": payload_ref,
                    "invocation_id": invocation_id,
                },
            )
            return [], None, err
        assert isinstance(result, Valid)
        decision = result.decision
        new_handles: list[str] = []
        handle_names: list[str] = []
        try:
            json_handle = self._store.tentative_write(
                artifact.name,
                parsed,
                written_by=f"{state.name}#{attempt}",
                invocation_id=invocation_id,
            )
            new_handles.append(json_handle)
            handle_names.append(artifact.name)
            for ext in artifact.extractions:
                if ext.source_field not in parsed:
                    # Optional source field omitted from the validated
                    # object: skip the extraction; the target retains
                    # its prior value (or its declared ``initial``).
                    continue
                value = parsed[ext.source_field]
                text_value = _coerce_to_text(value)
                ext_handle = self._store.tentative_write(
                    ext.target,
                    text_value,
                    written_by=f"{state.name}#{attempt}",
                    invocation_id=invocation_id,
                )
                new_handles.append(ext_handle)
                handle_names.append(ext.target)
        except Exception:
            if new_handles:
                self._store.discard_tentative(new_handles)
            raise
        # Pass extraction handle names through to _artifact_writes_record
        # via the payload's side-channel slot. _strip_internal drops
        # this before the envelope is finalized.
        payload["_schema_handle_names"] = handle_names
        self._log.write(
            "schema_validation",
            state_id=state.name,
            attempt=attempt,
            fields={
                "artifact": artifact.name,
                "outcome": "valid",
                "decision": decision,
                "validation_errors": [],
                "payload_ref": payload_ref,
                "invocation_id": invocation_id,
            },
        )
        # F2.5a decision-consistency invariant. The schema check passes
        # the SHAPE of criteria_compliance; this check enforces the
        # SEMANTICS (id coverage, accept-with-noncompliant, and the
        # iterate-only non_accept_with_full_compliance invariant). On
        # violation: discard the tentatives, log a decision_consistency
        # event with the failure reason, return an ErrorRecord so the
        # state exits via the error outcome.
        if self._criteria:
            compliance_raw = parsed.get("criteria_compliance")
            compliance: list[dict[str, Any]] = (
                list(compliance_raw) if isinstance(compliance_raw, list) else []
            )
            consistency: DecisionConsistencyResult = check_decision_consistency(
                decision=decision,
                criteria_compliance=compliance,
                configured=self._criteria,
                mode=self._decision_consistency_mode,
            )
            if not consistency.ok:
                if new_handles:
                    self._store.discard_tentative(new_handles)
                self._log.write(
                    "decision_consistency",
                    state_id=state.name,
                    attempt=attempt,
                    fields={
                        "artifact": artifact.name,
                        "outcome": "violation",
                        "decision": decision,
                        "reason": consistency.reason,
                        "missing_ids": list(consistency.missing_ids),
                        "extra_ids": list(consistency.extra_ids),
                        "duplicate_ids": list(consistency.duplicate_ids),
                        "noncompliant_required_ids": list(consistency.noncompliant_required_ids),
                        "mode": self._decision_consistency_mode.value,
                        "payload_ref": payload_ref,
                        "invocation_id": invocation_id,
                    },
                )
                err = ErrorRecord(
                    kind="actor_failure",
                    message=(f"decision-consistency violation: {consistency.reason}"),
                    detail={
                        "reason": "decision_consistency_violation",
                        "phase": "decision_consistency",
                        "consistency_reason": consistency.reason,
                    },
                )
                # Strip the schema-handle side-channel since we are not
                # returning the schema-write handles to the caller.
                payload.pop("_schema_handle_names", None)
                return [], None, err
            self._log.write(
                "decision_consistency",
                state_id=state.name,
                attempt=attempt,
                fields={
                    "artifact": artifact.name,
                    "outcome": "ok",
                    "decision": decision,
                    "mode": self._decision_consistency_mode.value,
                    "payload_ref": payload_ref,
                    "invocation_id": invocation_id,
                },
            )
        return new_handles, decision, None

    def _discard_stale_tentatives(self, state_name: str) -> None:
        """Discard tentatives left over from any earlier attempt on
        this state.

        On entry to attempt N, any tentatives written by attempts
        1..N-1 are stale: either they were committed in a prior run
        (in which case the rows are no longer tentative and are
        unaffected) or they were left tentative by a crash (in which
        case we discard them). The store's discard is by handle; we
        do not have those handles in memory after a process restart,
        so we discard by the underlying tentative rows tagged with
        the relevant ``written_by``.
        """
        # Slice 1 implementation: query tentative rows whose
        # written_by starts with f"{state_name}#" and discard them
        # directly via SQL. The store does not yet expose a public
        # API for this, so we reach into its connection here. The
        # store-level RLock and isolation_level=None contract
        # require explicit BEGIN IMMEDIATE / commit / rollback on
        # the connection (not via a cursor).
        conn = self._store._conn
        prefix = f"{state_name}#"
        with self._store.lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT seq FROM versions
                    WHERE is_tentative = 1 AND written_by LIKE ?
                    """,
                    (prefix + "%",),
                )
                rows = cur.fetchall()
                seqs = [r[0] for r in rows]
                # FK ordering: ``tentative_handles.seq`` references
                # ``versions.seq``. Under ``PRAGMA foreign_keys=ON``
                # the parent must be deleted AFTER the child,
                # otherwise the child delete fails with an
                # IntegrityError. ``store.discard_tentative`` does
                # the same thing (handle row first, then version
                # row); mirror that order here.
                for seq in seqs:
                    cur.execute("DELETE FROM tentative_handles WHERE seq = ?", (seq,))
                    cur.execute("DELETE FROM versions WHERE seq = ?", (seq,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
