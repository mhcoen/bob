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
import json
import sys
from pathlib import Path
from typing import Any

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
from orchestra.log import LogWriter
from orchestra.registry.registry import with_core
from orchestra.resume import replay_log, run_resume_hooks
from orchestra.spine import NO_INITIAL, ExternalInputDecl, Workflow
from orchestra.store import ArtifactStore

_TERMINAL_TARGETS = {"done", "stop"}


def _data_root() -> Path:
    return Path.home() / ".orchestra" / "runs"


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
            raise SystemExit(
                f"--input {decl.name}={raw!r}: invalid JSON ({exc})"
            ) from exc
    if t == "integer":
        try:
            return int(raw)
        except ValueError as exc:
            raise SystemExit(
                f"--input {decl.name}={raw!r}: not an integer ({exc})"
            ) from exc
    if t == "decimal":
        try:
            return float(raw)
        except ValueError as exc:
            raise SystemExit(
                f"--input {decl.name}={raw!r}: not a decimal ({exc})"
            ) from exc
    if t == "boolean":
        if raw.lower() in ("true", "1", "yes"):
            return True
        if raw.lower() in ("false", "0", "no"):
            return False
        raise SystemExit(
            f"--input {decl.name}={raw!r}: not a boolean (expected true/false)"
        )
    # Pass-through for unknown types; slice 2+ may add structured
    # artifact-type inputs.
    return raw


def cmd_run(args: argparse.Namespace) -> int:
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
        name: _parse_external_input(declared[name], raw)
        for name, raw in raw_inputs.items()
    }

    run_id = new_run_id()
    run_dir = (Path(args.data_root) if args.data_root else _data_root()) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write(
        "run_start",
        fields={
            "workflow_path": str(args.workflow),
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

    registry = with_core()
    workflow = load_workflow(workflow_path, registry)
    store = ArtifactStore(run_dir / "store.sqlite")
    log = LogWriter(log_path, replay.last_run_id, start_seq=replay.next_seq)

    run_resume_hooks(workflow, registry, replay, log)

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
            f"unknown verb {verb_name!r}. Configured: "
            f"{sorted(config.verbs)}",
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
    role_names = sorted(
        {state.role for state in workflow.states if state.role is not None}
    )
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
        details = [binding.adapter]
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


def _dispatch_verb(verb_name: str, query_words: list[str]) -> int:
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
            f"verb {verb_name!r}: no query supplied. "
            f"Usage: orchestra {verb_name} <words...>",
            file=sys.stderr,
        )
        return 2
    query = " ".join(query_words)
    try:
        answer = run_verb(verb_name, query, config)
    except OrchestraError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(answer)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)

    # No arguments at all: show the help overview and exit 0. The user
    # is asking what this tool does; argparse's "required: cmd" error
    # is the wrong answer. `orchestra <unknown>` still hits the verb
    # dispatcher and exits 2 with a friendly hint, so this fall-through
    # only fires when there is literally nothing to dispatch on.
    if not raw_args:
        config, _err = _try_load_merged_config(project_dir=Path.cwd())
        return _print_help_overview(config)

    # Handle the verb-style surface before argparse so positional
    # words can flow through unmangled.
    if raw_args and raw_args[0] not in _RESERVED_COMMANDS and not raw_args[0].startswith("-"):
        return _dispatch_verb(raw_args[0], raw_args[1:])

    if raw_args and raw_args[0] == "help":
        config, _err = _try_load_merged_config(project_dir=Path.cwd())
        if len(raw_args) == 1:
            return _print_help_overview(config)
        if config is None:
            print(
                "no config; cannot describe verb. Create "
                f"{global_config_path()} first.",
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
    run_p.add_argument(
        "--input", action="append", help="External input as key=value", default=[]
    )
    run_p.set_defaults(func=cmd_run)

    resume_p = sub.add_parser("resume", help="Resume a previously interrupted run")
    resume_p.add_argument("run_id")
    resume_p.set_defaults(func=cmd_resume)

    args = parser.parse_args(raw_args)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
