"""Command-line entry point for the runner.

Two surfaces:

- ``orchestra run`` and ``orchestra resume`` are the named subparsers
  for direct workflow execution. They take a workflow path or run id
  and pass through the underlying executor.
- ``orchestra <verb> <words...>`` is the verb-style surface. The
  CLI loads ``~/.orchestra/config.json`` at startup, finds the
  ``verbs`` section, and dispatches: if ``argv[1]`` matches a
  configured verb, ``argv[2:]`` is joined with spaces and passed as
  the workflow's ``query`` input. ``orchestra help`` lists all
  configured verbs; ``orchestra help <verb>`` shows the workflow that
  verb runs and the role bindings it requires.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from orchestra.adapters._subprocess import get_current_activity
from orchestra.api import run_verb
from orchestra.config import (
    ConfigError,
    OrchestraConfig,
    global_config_path,
    load_config,
)
from orchestra.errors import OrchestraError
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path
from orchestra.loader.parser import parse_workflow
from orchestra.log import LogWriter
from orchestra.progress import ProgressCallback, stderr_reporter
from orchestra.registry.registry import with_core
from orchestra.resume import replay_log, run_resume_hooks
from orchestra.spine import NO_INITIAL, ExternalInputDecl, Workflow
from orchestra.store import ArtifactStore

# Direct execution surface (``orchestra run`` / ``orchestra resume``)
# uses ``with_core``, which only registers the mock model, mock human,
# and mock shell backings plus the identity result parser. Workflows
# whose states call out to ``actor agent`` (Claude Code agent, Codex
# agent) or ``actor transform`` (e.g., the anonymize_outputs transform
# anonymous reviewers depends on) cannot run through this path because
# their backings and transform implementations are wired up in the
# verb/library API path, not here. Direct execution is therefore
# documented and enforced as a text/mock/shell-only surface; verb
# workflows are how a user invokes agent or transform pipelines.
_UNSUPPORTED_DIRECT_KINDS: frozenset[str] = frozenset({"agent", "transform"})

_TERMINAL_TARGETS = {"done", "stop"}


def _data_root() -> Path:
    return Path.home() / ".orchestra" / "runs"


def _workflow_digest(path: Path) -> str:
    """Return the sha256 hex digest of a workflow file's bytes.

    Resume integrity: ``cmd_run`` records the digest in the run_start
    record so ``cmd_resume`` can refuse to replay against a workflow
    file whose semantics drifted between the original run and the
    resume invocation. A pure byte digest is the smallest signal that
    closes the silent-divergence path; full source snapshotting is
    out of scope for this commit.
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        if art.source_kind is not None:
            qualifiers["source"] = {
                "kind": art.source_kind,
                "value": art.source_value,
            }
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _parse_external_input(decl: ExternalInputDecl, raw: str) -> Any:
    """Parse an external input string into the declared type.

    Slice 1 supports the primitive types listed in the grammar
    (text, json, integer, decimal, boolean) plus the inline artifact
    types passed through as text.
    """
    t = decl.type
    if t == "text" or t == "string":
        return raw
    if t == "json":
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--input {decl.name}={raw!r}: invalid JSON ({exc})") from exc
    if t == "integer":
        try:
            return int(raw)
        except ValueError as exc:
            raise SystemExit(f"--input {decl.name}={raw!r}: not an integer ({exc})") from exc
    if t == "decimal":
        try:
            return float(raw)
        except ValueError as exc:
            raise SystemExit(f"--input {decl.name}={raw!r}: not a decimal ({exc})") from exc
    if t == "boolean":
        if raw.lower() in ("true", "1", "yes"):
            return True
        if raw.lower() in ("false", "0", "no"):
            return False
        raise SystemExit(f"--input {decl.name}={raw!r}: not a boolean (expected true/false)")
    # Pass-through for unknown types; slice 2+ may add structured
    # artifact-type inputs.
    return raw


def _reject_unsupported_direct_workflow(
    workflow_path: Path,
) -> int | None:
    """Refuse direct execution of workflows whose states require an
    actor backing or transform that ``with_core`` does not register.

    Parses the workflow up front (without running validation, which
    would also fail but with a generic "unknown actor backing"
    message) and surfaces a targeted error naming the offending state
    and kind. Returns the exit code on rejection, ``None`` when the
    workflow is supported and the caller should proceed with
    ``load_workflow``.
    """
    try:
        with open(workflow_path, encoding="utf-8") as fh:
            source = fh.read()
        workflow = parse_workflow(source, Path(workflow_path))
    except OrchestraError as exc:
        print(f"orchestra: {exc}", file=sys.stderr)
        return 2
    offenders = [
        (state.name, state.actor.kind)
        for state in workflow.states
        if state.actor.kind in _UNSUPPORTED_DIRECT_KINDS
    ]
    if not offenders:
        return None
    print(
        "orchestra run does not support agent or transform workflows.",
        file=sys.stderr,
    )
    print(
        "Direct execution covers text, mock model, human, and shell "
        "states only. Use the verb/library surface (orchestra <verb> "
        "or orchestra.run_workflow) for workflows that call out to "
        "an agent or a registered transform.",
        file=sys.stderr,
    )
    print("Unsupported states in this workflow:", file=sys.stderr)
    for name, kind in offenders:
        print(f"  - {name} (actor {kind})", file=sys.stderr)
    return 2


def cmd_run(args: argparse.Namespace) -> int:
    rejection = _reject_unsupported_direct_workflow(Path(args.workflow))
    if rejection is not None:
        return rejection
    registry = with_core()
    workflow = load_workflow(args.workflow, registry)

    raw_inputs: dict[str, str] = {}
    for entry in args.input or []:
        if "=" not in entry:
            print(f"--input expects key=value, got {entry!r}", file=sys.stderr)
            return 2
        k, v = entry.split("=", 1)
        raw_inputs[k] = v

    declared = {ext.name: ext for ext in workflow.external_inputs}
    for name in raw_inputs:
        if name not in declared:
            print(
                f"--input {name}: not a declared external input",
                file=sys.stderr,
            )
            return 2
    for ext in workflow.external_inputs:
        if ext.name not in raw_inputs:
            print(
                f"missing required external input: {ext.name}",
                file=sys.stderr,
            )
            return 2

    external: dict[str, Any] = {
        name: _parse_external_input(declared[name], raw) for name, raw in raw_inputs.items()
    }

    run_id = new_run_id()
    run_root = Path(args.data_root) if args.data_root else _data_root()
    run_dir = run_root / run_id
    # Pass-8 fix #2: run directories carry prompt snapshots, log
    # files, and SQLite stores that may contain credentials and
    # proprietary content. Force 0700 on the run-root tree and the
    # per-run directory so the default umask cannot leave them
    # world-readable.
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        run_root.chmod(0o700)
    except OSError:
        pass
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        run_dir.chmod(0o700)
    except OSError:
        pass
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    from orchestra.prompt_snapshot import snapshot_prompt_sources

    workflow, prompt_snapshot_manifest = snapshot_prompt_sources(workflow, run_dir)
    log.write(
        "run_start",
        fields={
            "workflow_path": str(Path(args.workflow).resolve()),
            "workflow_digest": _workflow_digest(Path(args.workflow)),
            "prompt_snapshot_manifest": prompt_snapshot_manifest,
            "workflow_name": workflow.name,
            "spec_version": workflow.spec_version,
            "profiles": list(workflow.profiles),
            "external_inputs": external,
            "max_total_steps": workflow.max_total_steps,
        },
    )
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs=external,
    )
    terminal: str | None = None
    try:
        terminal = executor.run_to_completion()
    finally:
        log.write(
            "run_end",
            fields={"terminal": terminal if terminal is not None else "aborted"},
        )
        log.close()
        store.close()
    print(f"run {run_id} finished: {terminal}")
    print(f"run dir: {run_dir}")
    return 0 if terminal == "done" else 1


def cmd_resume(args: argparse.Namespace) -> int:
    run_dir = (Path(args.data_root) if args.data_root else _data_root()) / args.run_id
    if not run_dir.exists():
        print(f"no such run: {run_dir}", file=sys.stderr)
        return 2

    log_path = run_dir / "log.jsonl"
    replay = replay_log(str(log_path))

    from orchestra.log import LogReader

    records = LogReader(log_path).read_all()
    if not records:
        print("log is empty", file=sys.stderr)
        return 2
    run_start = records[0]
    workflow_path = run_start.fields.get("workflow_path")
    if not isinstance(workflow_path, str):
        print("run_start record missing workflow_path", file=sys.stderr)
        return 2
    rejection = _reject_unsupported_direct_workflow(Path(workflow_path))
    if rejection is not None:
        return rejection
    # Pass-3 fix #3: refuse to resume against a .orc file whose
    # contents drifted from the workflow that produced the durable
    # log. The original run recorded a sha256 of the workflow bytes
    # at run_start; resume recomputes against the file currently on
    # disk and refuses on mismatch. Without this, a transition the
    # log promised to make can resolve to a target the original
    # workflow never named, and the executor will run the new
    # transition's body silently.
    recorded_digest = run_start.fields.get("workflow_digest")
    if isinstance(recorded_digest, str) and recorded_digest:
        try:
            current_digest = _workflow_digest(Path(workflow_path))
        except OSError as exc:
            print(
                f"refusing to resume: cannot read workflow file {workflow_path}: {exc}",
                file=sys.stderr,
            )
            return 2
        if current_digest != recorded_digest:
            print(
                "refusing to resume: workflow file has changed since the original run.",
                file=sys.stderr,
            )
            print(
                f"  path: {workflow_path}",
                file=sys.stderr,
            )
            print(
                f"  recorded digest: {recorded_digest}",
                file=sys.stderr,
            )
            print(
                f"  current digest:  {current_digest}",
                file=sys.stderr,
            )
            print(
                "Resume against a modified workflow can route the "
                "next transition to a target the original workflow "
                "never named, or run an actor with semantics the "
                "durable log does not describe. Restore the original "
                "file or roll the run back.",
                file=sys.stderr,
            )
            return 2
    external_inputs = run_start.fields.get("external_inputs") or {}

    if replay.is_terminal or replay.current_state in _TERMINAL_TARGETS:
        print(
            f"run {args.run_id} already ended in terminal state "
            f"{replay.current_state!r}; nothing to resume",
            file=sys.stderr,
        )
        return 0

    if replay.current_state is None:
        print("nothing to resume; the log named no state", file=sys.stderr)
        return 2

    # Pass-4 fix #2: legacy prompt_manifest gate (sha256 of every
    # file-backed prompt source). Pass-5 found two distinct bypasses
    # of this gate (symlink retargeting, relative workflow paths),
    # both of which boil down to "the path the manifest recorded" and
    # "the path the executor opens" can drift. Pass-5 redesign
    # replaces the gate with prompt-source snapshotting (see below).
    # This branch handles backward-compatible resume of OLD runs that
    # were created with prompt_manifest but no
    # prompt_snapshot_manifest. New runs skip this gate entirely
    # because the snapshot path is the source of truth.
    recorded_snapshot_manifest = run_start.fields.get("prompt_snapshot_manifest")
    recorded_manifest = run_start.fields.get("prompt_manifest")
    if recorded_snapshot_manifest is None and isinstance(recorded_manifest, dict):
        diffs: list[str] = []
        recorded_clean: dict[str, str] = {str(k): str(v) for k, v in recorded_manifest.items()}
        for path_str, recorded_digest_v in sorted(recorded_clean.items()):
            path_obj = Path(path_str)
            if not path_obj.is_file():
                diffs.append(f"  removed: {path_str}")
                continue
            current_digest_v = hashlib.sha256(path_obj.read_bytes()).hexdigest()
            if current_digest_v != recorded_digest_v:
                diffs.append(
                    f"  changed: {path_str} "
                    f"(was {recorded_digest_v[:12]}..., "
                    f"now {current_digest_v[:12]}...)"
                )
        if diffs:
            print(
                "refusing to resume: file-backed prompt sources have "
                "changed since the original run.",
                file=sys.stderr,
            )
            for line in diffs:
                print(line, file=sys.stderr)
            print(
                "Resume against modified prompt files would run the "
                "actor with input the original run never saw. Restore "
                "the original files or roll the run back.",
                file=sys.stderr,
            )
            return 2

    registry = with_core()
    workflow = load_workflow(workflow_path, registry)

    # Pass-5 redesign: rewrite the workflow's file-backed prompt
    # sources to point at the snapshot files captured at run_start.
    # Resume reads the bytes the original run pinned, not the live
    # filesystem, so symlink retargeting, source_dir ambiguity, and
    # template edits between crash and resume cannot reach the actor.
    # Snapshot integrity is verified inside restore_prompt_snapshots:
    # a deleted or mutated snapshot file is a hard refusal because
    # the original bytes are no longer recoverable.
    if isinstance(recorded_snapshot_manifest, list):
        from orchestra.prompt_snapshot import (
            SnapshotIntegrityError,
            restore_prompt_snapshots,
        )

        try:
            workflow = restore_prompt_snapshots(workflow, recorded_snapshot_manifest)
        except SnapshotIntegrityError as exc:
            print(
                "refusing to resume: prompt snapshot integrity check failed.",
                file=sys.stderr,
            )
            print(f"  {exc}", file=sys.stderr)
            print(
                "Snapshots in the run directory are read-only inputs "
                "for resume. A missing or mutated snapshot means the "
                "original prompt bytes are no longer available; roll "
                "the run back rather than resuming against drifted "
                "input.",
                file=sys.stderr,
            )
            return 2

    store = ArtifactStore(run_dir / "store.sqlite")
    # Wrap the store and log lifetimes in try/finally from the point of
    # open. The refusal query below, run_resume_hooks, the
    # VisibilityIndex setup, and the Executor construction can all raise
    # before the run loop; a failure there would otherwise leak the
    # SQLite connection and the log fd on every failed resume in a
    # long-lived process. The log is opened partway through, so it is
    # tracked with a nullable handle the finally closes only if set.
    log: LogWriter | None = None
    terminal: str | None = None
    try:
        # Pass-2 fix #1 + pass-3 fix #2: refuse to resume into a state
        # that committed artifact versions before crashing without
        # writing state_exit. The committed work is durable in the store
        # but the log shows only state_enter, so a naive resume would
        # re-enter the state and run the actor a second time. For agent
        # states that mutate the workspace, the second run re-mutates and
        # corrupts the workspace.
        #
        # Two signals point at the same window. The artifact_write log
        # records (pass-2) catch the state when the crash lands AFTER the
        # log writes; the store-side query (pass-3) catches the earlier
        # window between commit_tentative and the first artifact_write,
        # where the committed row is durable but no log record names it.
        # Querying the store keyed by invocation_id is authoritative for
        # that earlier window.
        if replay.current_state is not None and not replay.last_state_completed:
            target_state = replay.current_state
            state_decl = next(
                (s for s in workflow.states if s.name == target_state),
                None,
            )
            if state_decl is not None and state_decl.actor.kind == "agent":
                target_attempt = replay.attempts.get(target_state)
                store_orphans: list[Any] = []
                if target_attempt is not None and replay.last_run_id:
                    from orchestra.visibility import make_invocation_id

                    target_inv = make_invocation_id(
                        replay.last_run_id, target_state, target_attempt
                    )
                    store_orphans = store.list_committed_by_invocation(target_inv)
                log_orphan_attempts = sorted(
                    a for s, a in replay.committed_without_exit if s == target_state
                )
                if store_orphans or log_orphan_attempts:
                    refusal_reason: str
                    if store_orphans:
                        arts = sorted({v.name for v in store_orphans})
                        refusal_reason = (
                            f"the store holds committed artifact versions "
                            f"({', '.join(repr(a) for a in arts)}) tagged "
                            f"with this invocation but no state_exit was "
                            "written. The crash window between commit and "
                            "log-write makes the artifact_write trail "
                            "absent, so the store is the authoritative "
                            "signal."
                        )
                    else:
                        refusal_reason = (
                            "the log shows artifact_write records for this "
                            "state with no matching state_exit"
                        )
                    print(
                        f"refusing to resume: state {target_state!r} "
                        f"(attempt {target_attempt}) committed work to the "
                        "store before crashing without a matching "
                        "state_exit. Re-entering would run the agent a "
                        "second time and re-mutate the workspace.",
                        file=sys.stderr,
                    )
                    print(
                        f"Reason: {refusal_reason}",
                        file=sys.stderr,
                    )
                    print(
                        "Inspect the run directory and either repair the "
                        "log manually with a synthesized state_exit "
                        "matching the durable artifact versions or roll "
                        "the run back. The conservative default is refusal "
                        "because agents are not assumed to be idempotent.",
                        file=sys.stderr,
                    )
                    return 2

        log = LogWriter(log_path, replay.last_run_id, start_seq=replay.next_seq)

        run_resume_hooks(workflow, registry, replay, log)

        # Slice A: the persisted ``visibility.json`` is a best-effort
        # cache; the log is the source of truth. Apply the rebuilt
        # statuses to the index BEFORE constructing the Executor so the
        # store consults the log-derived view from the first read.
        from orchestra.visibility import VisibilityIndex

        visibility_index = VisibilityIndex(persist_path=run_dir / "visibility.json")
        visibility_index.replace_from(replay.visibility_statuses)

        executor = Executor(
            workflow=workflow,
            registry=registry,
            store=store,
            log=log,
            run_dir=run_dir,
            run_id=replay.last_run_id,
            external_inputs=external_inputs,
            attempts=replay.attempts,
            retries=replay.retries,
            envelopes=replay.envelopes,
            current_state=replay.current_state,
            step_count=replay.step_count,
            visibility_index=visibility_index,
            last_transition_state=replay.last_transition_state,
            last_transition_outcome=replay.last_transition_outcome,
        )
        try:
            # Slice A fix: a crash between ``state_exit`` and
            # ``transition`` leaves the state's body durably complete but
            # the routing decision unwritten. Re-select the transition
            # from the reconstructed envelope WITHOUT re-running the
            # actor; advance _current_state to the chosen target and
            # let ``run_to_completion`` continue from there.
            if (
                replay.state_exit_without_transition
                and replay.current_state is not None
                and replay.current_state not in _TERMINAL_TARGETS
            ):
                executor.resume_pending_transition(replay.current_state)
            # Round-3 fix: a crash between ``fan_out_end`` and the
            # parent's ``transition`` leaves the routing decision
            # durable in ``fan_out_end`` but the transition record
            # unwritten. Close the missing transition without
            # re-dispatching the fan-out children.
            if replay.pending_fan_out_transition is not None and replay.open_fan_out is None:
                pft = replay.pending_fan_out_transition
                executor.close_fan_out_pending_transition(
                    parent_state_name=str(pft["parent_state"]),
                    parent_attempt=int(pft["attempt"]),
                    target=str(pft["target"]),
                )
            # Slice A: if a fan_out group is open (fan_out_start without
            # a matching fan_out_end), dispatch to resume_fan_out before
            # the linear loop takes over. The method advances
            # _current_state to the join/error target so run_to_completion
            # can continue from there.
            if replay.open_fan_out is not None:
                of = replay.open_fan_out
                children_field = of.get("children") or []
                if not isinstance(children_field, list):
                    children_field = []
                children_list = [str(c) for c in children_field]
                # A child is "completed" only when its last state_enter
                # has a matching state_exit -- i.e. the envelope's
                # attempt equals the latest state_enter's attempt
                # (replay's reconstructed ``attempts`` counter). If a
                # later state_enter exists without a matching exit
                # (the retry-mid-flight crash case), the older
                # envelope is stale; the child is still pending and
                # must be re-launched on resume per the fresh-budget
                # rule.
                completed = {
                    name: env
                    for name, env in replay.envelopes.items()
                    if (name in children_list and env.attempt == replay.attempts.get(name))
                }
                executor.resume_fan_out(
                    parent_state_name=str(of.get("parent_state", "")),
                    children=children_list,
                    join_target=str(of.get("join_target", "")),
                    error_target=str(of.get("error_target", "")),
                    completed_children=completed,
                    parent_attempt=replay.open_fan_out_attempt,
                )
            terminal = executor.run_to_completion()
        finally:
            log.write(
                "run_end",
                fields={"terminal": terminal if terminal is not None else "aborted"},
            )
    finally:
        if log is not None:
            log.close()
        store.close()
    print(f"run {replay.last_run_id} resumed and finished: {terminal}")
    return 0 if terminal == "done" else 1


_RESERVED_COMMANDS: frozenset[str] = frozenset({"run", "resume", "help"})


def _print_help_overview(config: OrchestraConfig | None) -> int:
    out = sys.stdout
    print("Configured verbs:", file=out)
    if config is None or not config.verbs:
        print(
            f"  (none; create {global_config_path()} with a 'verbs' section)",
            file=out,
        )
    else:
        width = max(len(name) for name in config.verbs)
        for verb_name in sorted(config.verbs):
            workflow = config.verbs[verb_name].workflow
            print(f"  {verb_name.ljust(width)}  runs {workflow}", file=out)
    print("", file=out)
    print("Direct workflow execution:", file=out)
    print("  run <workflow.orc> --input k=v ...", file=out)
    print("  resume <run_id>", file=out)
    print("", file=out)
    print("Use `orchestra help <verb>` for verb details.", file=out)
    return 0


def _print_help_for_verb(verb_name: str, config: OrchestraConfig) -> int:
    out = sys.stdout
    err = sys.stderr
    if verb_name not in config.verbs:
        print(
            f"unknown verb {verb_name!r}. Configured: {sorted(config.verbs)}",
            file=err,
        )
        return 2
    workflow_name = config.verbs[verb_name].workflow
    print(f"{verb_name}: runs workflow `{workflow_name}`", file=out)
    try:
        workflow_path = resolve_workflow_path(workflow_name, project_dir=None)
    except OrchestraError as exc:
        print(f"  workflow file not found: {exc}", file=err)
        return 1
    try:
        from orchestra.api import _pre_load_registry

        workflow = load_workflow(workflow_path, _pre_load_registry())
    except OrchestraError as exc:
        print(f"  workflow failed to load: {exc}", file=err)
        return 1
    role_names = sorted({role for state in workflow.states if (role := state.role) is not None})
    print(
        "Required roles: " + (", ".join(role_names) if role_names else "(none)"),
        file=out,
    )
    print(
        f"Configured bindings (from {global_config_path()}):",
        file=out,
    )
    for role in role_names:
        binding = config.roles.get(role)
        if binding is None:
            print(f"  {role}: NOT CONFIGURED", file=out)
            continue
        if binding.adapter is None:
            print(f"  {role}: INVALID (adapter missing)", file=out)
            continue
        adapter: str = binding.adapter
        details = [adapter]
        if binding.model:
            details.append(f"model={binding.model}")
        print(f"  {role}: " + ", ".join(details), file=out)
    return 0


def _try_load_merged_config(
    project_dir: Path | None = None,
) -> tuple[OrchestraConfig | None, str | None]:
    """Return ``(config, error_message)``.

    Returns ``(config, None)`` when the merged config loaded
    successfully, or ``(None, message)`` when a parse or schema error
    fires on either layer. The CLI branches on the return so it can
    show the right friendly error for each case without leaking
    exception text into help output.

    Note that ``load_config`` returns ``default_config()`` when both
    files are absent, so a successful return does not guarantee the
    user has set up any verbs. Callers that need missing-config
    detection check ``global_config_path().is_file()`` separately
    before dispatching a verb.
    """
    try:
        return load_config(project_dir=project_dir), None
    except ConfigError as exc:
        return None, str(exc)


def _no_global_config_hint() -> str:
    return (
        f"no config at {global_config_path()}; create one with verb "
        "mappings to use this command. See `orchestra help` for the format."
    )


def _dispatch_verb(
    verb_name: str,
    query_words: list[str],
    *,
    progress_callback: ProgressCallback | None = None,
) -> int:
    """Dispatch one verb invocation and print the answer."""
    project_dir = Path.cwd()
    config, err = _try_load_merged_config(project_dir=project_dir)
    if config is None:
        print(err or "config unavailable", file=sys.stderr)
        return 1
    # If the merged config has no verbs and no global config exists on
    # disk, the user is unconfigured: emit the setup hint instead of
    # an unhelpful "unknown command".
    if not config.verbs and not global_config_path().is_file():
        print(_no_global_config_hint(), file=sys.stderr)
        return 1
    if verb_name not in config.verbs:
        print(
            f"unknown command: {verb_name}; try `orchestra help`",
            file=sys.stderr,
        )
        return 2
    if not query_words:
        print(
            f"verb {verb_name!r}: no query supplied. Usage: orchestra {verb_name} <words...>",
            file=sys.stderr,
        )
        return 2
    query = " ".join(query_words)
    try:
        answer = run_verb(
            verb_name,
            query,
            config,
            progress_callback=progress_callback,
            project_dir=project_dir,
        )
    except OrchestraError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(answer)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)

    progress_cb: ProgressCallback = stderr_reporter(activity_getter=get_current_activity)

    # No arguments at all: drop into the interactive REPL. The user
    # is asking to use the tool, not asking what argparse complains
    # about. `orchestra help` still emits the static overview, and
    # `orchestra <unknown>` still hits the verb dispatcher and exits
    # 2 with a friendly hint, so this fall-through only fires when
    # there is literally nothing to dispatch on.
    if not raw_args:
        config, err = _try_load_merged_config(project_dir=Path.cwd())
        if config is None:
            print(err or "config unavailable", file=sys.stderr)
            return 1
        from orchestra.repl import run_repl

        return run_repl(config, progress_callback=progress_cb)

    # Handle the verb-style surface before argparse so positional
    # words can flow through unmangled.
    if raw_args and raw_args[0] not in _RESERVED_COMMANDS and not raw_args[0].startswith("-"):
        return _dispatch_verb(raw_args[0], raw_args[1:], progress_callback=progress_cb)

    if raw_args and raw_args[0] == "help":
        config, _err = _try_load_merged_config(project_dir=Path.cwd())
        if len(raw_args) == 1:
            return _print_help_overview(config)
        if config is None:
            print(
                f"no config; cannot describe verb. Create {global_config_path()} first.",
                file=sys.stderr,
            )
            return 1
        return _print_help_for_verb(raw_args[1], config)

    parser = argparse.ArgumentParser(prog="orchestra")
    parser.add_argument(
        "--data-root",
        help="Root directory for run state. Defaults to ~/.orchestra/runs.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Execute a workflow")
    run_p.add_argument("workflow")
    run_p.add_argument("--input", action="append", help="External input as key=value", default=[])
    run_p.set_defaults(func=cmd_run)

    resume_p = sub.add_parser("resume", help="Resume a previously interrupted run")
    resume_p.add_argument("run_id")
    resume_p.set_defaults(func=cmd_resume)

    args = parser.parse_args(raw_args)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
