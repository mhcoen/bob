"""Command-line entry point for the runner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.log import LogWriter
from orchestra.registry.registry import with_core
from orchestra.resume import replay_log, run_resume_hooks
from orchestra.spine import Workflow
from orchestra.store import ArtifactStore


def _data_root() -> Path:
    return Path.home() / ".orchestra" / "runs"


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not None:
            qualifiers["initial"] = art.initial
        if art.source_kind is not None:
            qualifiers["source"] = {
                "kind": art.source_kind,
                "value": art.source_value,
            }
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def cmd_run(args: argparse.Namespace) -> int:
    registry = with_core()
    workflow = load_workflow(args.workflow, registry)

    # Parse external inputs from --input k=v pairs.
    external: dict[str, Any] = {}
    for entry in args.input or []:
        if "=" not in entry:
            print(f"--input expects key=value, got {entry!r}", file=sys.stderr)
            return 2
        k, v = entry.split("=", 1)
        external[k] = v

    # Confirm every declared external input is supplied.
    for ext in workflow.external_inputs:
        if ext.name not in external:
            print(
                f"missing required external input: {ext.name}",
                file=sys.stderr,
            )
            return 2

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

    # We need the workflow source path to reload the spec. It is
    # recorded in the run_start record.
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

    registry = with_core()
    workflow = load_workflow(workflow_path, registry)
    store = ArtifactStore(run_dir / "store.sqlite")
    log = LogWriter(log_path, replay.last_run_id, start_seq=replay.next_seq)

    # Run resume hooks (slice 1: empty hook set).
    run_resume_hooks(workflow, registry, replay, log)

    if replay.current_state is None:
        print("nothing to resume; the log named no state", file=sys.stderr)
        log.close()
        store.close()
        return 2

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
        current_state=replay.current_state,
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
