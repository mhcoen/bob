"""A bounded reference orchestrator using Bob's real persistence APIs.

Run with the workspace Python. Default editors are deterministic and offline.
Artifacts are retained; existing output directories are never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4

from bob_tools.json_state import atomic_write_json, read_json_object
from bob_tools.ledger import Storage
from bob_tools.planfile import (
    Phase,
    Plan,
    TaskStatus,
    complete_task,
    load,
    make_task,
    save,
    update,
)
from mcloop.checks import run_command_acceptance
from mcloop.completion import (
    Completion,
    RecoveryRequired,
    acknowledge,
    execution_operation,
    pending_receipts,
    project_owner,
    recovery_report,
    run_owned_command,
)
from mcloop.ledger_emit import TaskOutcome, emit_task_lifecycle_events

HERE = Path(__file__).resolve().parent
TASK = "T-EX-000001"
REQUIREMENT = "REQ-SPAN-001"
DESCRIPTION = (
    "Count both endpoints of an integer range; return zero for a reversed range."
)
FILES = ("span.py", "regression.py", "README.md")
SCENARIOS = ("correct", "wrong", "weakened-tests", "no-op", "unrelated")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def git(project, *args):
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **clean_environment(),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    return result.stdout.strip()


def clean_environment():
    return {
        name: os.environ[name]
        for name in ("PATH", "TMPDIR", "LANG", "LC_ALL")
        if name in os.environ
    }


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_hashes(snapshot):
    return {p.name: digest(p) for p in sorted(snapshot.iterdir()) if p.is_file()}


def write_record(root, record):
    atomic_write_json(root / "acceptance.json", record)


def prepare(root, scenario):
    root.mkdir(parents=True, exist_ok=False)
    project = root / "project"
    shutil.copytree(
        HERE / "fixture", project, ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copyfile(HERE / "oracle.py", root / "oracle.py")
    (project / ".gitignore").write_text(".mcloop/\n.duplo/\n*.lock\n__pycache__/\n")
    command = shlex.join(
        [sys.executable, "-I", str(root / "oracle.py"), str(root / "snapshot")]
    )
    task = make_task(
        DESCRIPTION, task_id=TASK, annotations=(("accept", f"command-exit: {command}"),)
    )
    phase = Phase(
        "phase_001",
        "explicit_comment",
        1,
        "Phase",
        "Inclusive ranges",
        "",
        (),
        (task,),
        0,
    )
    save(
        project / "PLAN.md",
        Plan(1, "Span example", REQUIREMENT, (phase,), None, None, "EX"),
    )
    git(project, "init", "-q")
    git(project, "config", "user.name", "Bob example")
    git(project, "config", "user.email", "example@example.invalid")
    git(project, "add", ".")
    git(project, "commit", "-qm", "Fixture baseline with intentional defect")
    record = {
        "schema_version": 1,
        "run_id": uuid4().hex,
        "requirement_id": REQUIREMENT,
        "requirement_version": 1,
        "requirement": DESCRIPTION,
        "task_id": TASK,
        "plan_path": "project/PLAN.md",
        "scenario": scenario,
        "baseline_commit": git(project, "rev-parse", "HEAD"),
        "candidate_commit": None,
        "candidate_tree": None,
        "status": "prepared",
        "editor_invocations": 0,
        "oracle": {
            "version": 1,
            "sha256": digest(root / "oracle.py"),
            "path": "oracle.py",
        },
        "checks": [],
        "receipt": None,
        "ledger_event_ids": [],
        "ledger_path": "project/.duplo/ledger",
        "usage": {
            "tokens": None,
            "cost": None,
            "reason": "editor protocol supplies no accounting",
        },
    }
    write_record(root, record)


def edit(project, scenario, editor_command):
    source = (project / "span.py").read_text()
    if editor_command:
        prompt = {
            "requirement_id": REQUIREMENT,
            "requirement": DESCRIPTION,
            "files": {name: (project / name).read_text() for name in FILES},
            "response": "Return only a JSON object mapping allowed filenames to full contents.",
        }
        # This opt-in executable is trusted. It receives no oracle or candidate path.
        with tempfile.TemporaryDirectory(prefix="bob-editor-") as cwd:
            result = subprocess.run(
                editor_command,
                input=json.dumps(prompt),
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
        changes = json.loads(result.stdout)
    elif scenario == "correct":
        changes = {"span.py": source.replace("end - start)", "end - start + 1)")}
    elif scenario == "wrong":
        # Plausible fix: counts positive ranges, mishandles negative endpoints.
        changes = {
            "span.py": source.replace("end - start)", "abs(end) - abs(start) + 1)")
        }
    elif scenario == "weakened-tests":
        changes = {"regression.py": "# Candidate removed assertions.\n"}
    elif scenario == "unrelated":
        changes = {
            "README.md": (project / "README.md").read_text() + "\nNow documented!\n"
        }
    else:
        changes = {}
    require(isinstance(changes, dict), "Editor must return a JSON object")
    require(
        all(
            name in FILES and isinstance(content, str)
            for name, content in changes.items()
        ),
        "Editor returned an unsupported filename or non-text contents",
    )
    for name, content in changes.items():
        (project / name).write_text(content)


def verify(root, record, *, recovery=False):
    project = root / "project"
    oracle = root / "oracle.py"
    require(
        digest(oracle) == record["oracle"]["sha256"],
        "Oracle changed; refusing verification",
    )
    tree = git(project, "write-tree")
    require(
        tree == record["candidate_tree"], "Candidate index differs from recorded tree"
    )
    snapshot = root / ("recovery-snapshot" if recovery else "snapshot")
    snapshot.mkdir()
    git(project, "checkout-index", "--all", f"--prefix={snapshot}/")
    before = snapshot_hashes(snapshot)
    command = shlex.join([sys.executable, "-I", str(oracle), str(snapshot)])
    with patch.dict(os.environ, clean_environment(), clear=True):
        result = run_command_acceptance(snapshot, command)
    log = "recovery-checks.log" if recovery else "checks.log"
    (root / log).write_text(result.output)
    try:
        checks = json.loads(result.output)
    except json.JSONDecodeError:
        checks = [
            {"passed": False, "error": "Oracle did not return JSON; inspect check log"}
        ]
    require(
        before == snapshot_hashes(snapshot), "Checks modified the candidate snapshot"
    )
    require(digest(oracle) == record["oracle"]["sha256"], "Checks modified the oracle")
    evidence = {
        "command": command,
        "passed": result.passed,
        "results": checks,
        "log": log,
        "candidate_tree": tree,
        "files_sha256": before,
    }
    record["recovery_checks" if recovery else "checks"] = evidence
    write_record(root, record)
    return result


def settle(root, record, receipt):
    project = root / "project"
    sha = git(project, "rev-parse", "HEAD")
    require(
        git(project, "rev-parse", "HEAD^{tree}") == record["candidate_tree"],
        "Landed commit does not match the independently checked tree",
    )
    require(
        git(project, "rev-parse", "HEAD^") == record["baseline_commit"],
        "Unexpected candidate parent",
    )
    receipt.advance("commit_returned", commit_hash=sha)
    update(project / "PLAN.md", lambda plan: complete_task(plan, TASK)[0])
    receipt.advance("plan_updated", plan_after=(project / "PLAN.md").read_text())
    events = emit_task_lifecycle_events(
        storage=Storage(project / ".duplo/ledger", writer_id="example"),
        task_label=TASK,
        phase_id="phase_001",
        outcome=TaskOutcome(
            True,
            False,
            REQUIREMENT,
            tuple(
                git(
                    project, "diff", "--name-only", record["baseline_commit"], sha
                ).splitlines()
            ),
        ),
        project_dir=project,
        run_id=record["run_id"],
    )
    require(len(events) == 1, "Expected one durable commit event")
    receipt.advance("settled", ledger_event_ids=events)
    record.update(candidate_commit=sha, status="accepted", ledger_event_ids=events)
    write_record(root, record)


def attempt(root, *, interrupt=False, editor_command=()):
    project = root / "project"

    def work():
        record = read_json_object(root / "acceptance.json")
        task = load(project / "PLAN.md").phases[0].tasks[0]
        if task.status == TaskStatus.DONE:
            return "already complete"
        require(
            record["editor_invocations"] == 0,
            "This example permits only one editor attempt",
        )
        record["editor_invocations"] += 1
        record["status"] = "editing"
        write_record(root, record)
        with execution_operation(project, "example_editor"):
            edit(project, record["scenario"], editor_command)
        git(project, "add", "--", *FILES)
        record["candidate_tree"] = git(project, "write-tree")
        result = verify(root, record)
        if not result.passed:
            record["status"] = "rejected"
            write_record(root, record)
            return "rejected"
        receipt = Completion.begin(
            project,
            project / "PLAN.md",
            [task],
            command=result.command,
            output=result.output,
            baseline=record["baseline_commit"],
            ledger_dir=str(project / ".duplo/ledger"),
        )
        record.update(status="verified", receipt=str(receipt.path.relative_to(root)))
        write_record(root, record)
        git(project, "commit", "-qm", f"Complete {TASK}: {REQUIREMENT}")
        if interrupt:
            os._exit(
                71
            )  # Deliberately bypass finally blocks, after Git and before receipt update.
        settle(root, record, receipt)
        return "accepted"

    return run_owned_command(project / "PLAN.md", work)


def recover(root):
    """Explicitly reconcile only the demonstrated post-commit/pre-return boundary."""
    project = root / "project"
    record = read_json_object(root / "acceptance.json")
    atomic_write_json(root / "recovery-before.json", recovery_report(project))
    try:
        attempt(root)
    except RecoveryRequired:
        pass
    else:
        raise RuntimeError("Restart should refuse an ambiguous execution")
    with project_owner(project):
        pending = pending_receipts(project)
        completions = [(p, d) for p, d in pending if d["schema_version"] == 1]
        executions = [(p, d) for p, d in pending if d["schema_version"] == 2]
        require(
            len(completions) == len(executions) == 1, "Unexpected recovery receipts"
        )
        path, data = completions[0]
        require(
            data["stage"] == "verified" and record["status"] == "verified",
            "Only the documented interruption boundary can be reconciled",
        )
        require(
            git(project, "status", "--porcelain") == "",
            "Recovery requires an unchanged project",
        )
        require(
            (project / "PLAN.md").read_text() == data["plan_before"], "Plan changed"
        )
        require(
            git(project, "rev-parse", "HEAD^{tree}") == record["candidate_tree"],
            "Tree changed",
        )
        require(
            git(project, "rev-parse", "HEAD^") == record["baseline_commit"],
            "Parent changed",
        )
        require(
            verify(root, record, recovery=True).passed, "Recovery acceptance failed"
        )
        settle(root, record, Completion(project, path))
        execution_id = executions[0][1]["id"]
    acknowledge(
        project,
        execution_id,
        "Example reconciled exact committed tree after oracle recheck",
    )
    head = git(project, "rev-parse", "HEAD")
    require(
        attempt(root) == "already complete", "Resume did not recognize task completion"
    )
    require(git(project, "rev-parse", "HEAD") == head, "Resume created another commit")
    record["recovery"] = {
        "restart_blocked": True,
        "editor_replayed": False,
        "acknowledged_execution": execution_id,
        "resumed": "already complete",
    }
    write_record(root, record)


def demo(root):
    root.mkdir(parents=True, exist_ok=False)
    results = {}
    for scenario in (*SCENARIOS, "interrupted"):
        destination = root / scenario
        prepare(destination, "correct" if scenario == "interrupted" else scenario)
        if scenario == "interrupted":
            worker = subprocess.run(
                [sys.executable, str(HERE / "run.py"), "_interrupt", str(destination)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            (destination / "worker.log").write_text(worker.stdout + worker.stderr)
            require(
                worker.returncode == 71,
                "Interruption worker failed; inspect worker.log",
            )
            recover(destination)
        else:
            attempt(destination)
        record = read_json_object(destination / "acceptance.json")
        expected = "accepted" if scenario in ("correct", "interrupted") else "rejected"
        require(record["status"] == expected, f"{scenario}: expected {expected}")
        require(record["editor_invocations"] == 1, f"{scenario}: editor replayed")
        require(
            record["checks"]["results"][0]["passed"], "Existing regressions should pass"
        )
        results[scenario] = {
            "status": record["status"],
            "evidence": f"{scenario}/acceptance.json",
        }
    atomic_write_json(root / "result.json", {"schema_version": 1, "scenarios": results})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("demo", "run", "recover", "interrupt", "_interrupt")
    )
    parser.add_argument(
        "work_dir", type=Path, help="New artifact directory (existing for recover)"
    )
    parser.add_argument("--scenario", choices=SCENARIOS, default="correct")
    parser.add_argument(
        "--editor-command",
        nargs=argparse.REMAINDER,
        help="Opt in to one trusted editor command; stdin/out JSON protocol",
    )
    args = parser.parse_args()
    root = args.work_dir.resolve()
    if args.action == "demo":
        require(not args.editor_command, "External editors are supported only with run")
        print(json.dumps(demo(root), indent=2))
    elif args.action == "recover":
        recover(root)
    elif args.action == "_interrupt":
        attempt(root, interrupt=True)
    elif args.action == "interrupt":
        prepare(root, "correct")
        attempt(root, interrupt=True)
    else:
        prepare(root, "external" if args.editor_command else args.scenario)
        result = attempt(root, editor_command=args.editor_command or ())
        print(f"{result}: {root / 'acceptance.json'}")
        return 0 if result == "accepted" else 1
    print(f"Evidence: {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
