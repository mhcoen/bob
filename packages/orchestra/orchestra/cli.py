"""Command-line entry point for the runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
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


def main(argv: list[str] | None = None) -> int:
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

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
