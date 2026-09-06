"""Acceptance and recovery checks through the standalone example process."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "examples/verified-change/run.py"
spec = importlib.util.spec_from_file_location("verified_example", SCRIPT)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def invoke(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=50,
    )


@pytest.fixture(scope="module")
def demonstration(tmp_path_factory):
    root = tmp_path_factory.mktemp("verified") / "evidence with spaces"
    result = invoke("demo", root)
    assert result.returncode == 0, result.stdout + result.stderr
    return root


@pytest.mark.parametrize("scenario", [*example.SCENARIOS, "interrupted"])
def test_independent_verdict_and_exact_commit(demonstration, scenario):
    root = demonstration / scenario
    record = json.loads((root / "acceptance.json").read_text())
    accepted = scenario in ("correct", "interrupted")
    assert record["status"] == ("accepted" if accepted else "rejected")
    assert record["editor_invocations"] == 1
    assert record["checks"]["results"][0]["passed"]  # All pass in-project tests.
    assert record["checks"]["passed"] == accepted
    assert record["oracle"]["sha256"] == example.digest(root / "oracle.py")
    project = root / "project"
    assert len(
        example.Storage(project / ".duplo/ledger", writer_id="test").read_all()
    ) == int(accepted)
    task = example.load(project / "PLAN.md").phases[0].tasks[0]
    assert (task.status == example.TaskStatus.DONE) == accepted
    if accepted:
        assert example.git(project, "rev-parse", "HEAD") == record["candidate_commit"]
        assert (
            example.git(project, "rev-parse", "HEAD^{tree}") == record["candidate_tree"]
        )
        assert example.git(project, "rev-list", "--count", "HEAD") == "2"
        receipt = json.loads((root / record["receipt"]).read_text())
        assert receipt["stage"] == "settled"
        assert (
            receipt["observations"][-1]["ledger_event_ids"]
            == record["ledger_event_ids"]
        )
    else:
        assert record["candidate_commit"] is None
        assert example.git(project, "rev-parse", "HEAD") == record["baseline_commit"]
    if scenario == "interrupted":
        assert record["recovery"]["restart_blocked"]
        assert not record["recovery"]["editor_replayed"]
        assert example.pending_receipts(project) == []
        assert record["recovery_checks"]["passed"]


@pytest.mark.parametrize("changed", ["oracle", "project", "commit"])
def test_recovery_refuses_changed_evidence(tmp_path, changed):
    root = tmp_path / changed
    example.prepare(root, "correct")
    result = invoke("_interrupt", root)
    assert result.returncode == 71, result.stderr
    project = root / "project"
    if changed == "oracle":
        (root / "oracle.py").write_text("raise SystemExit(0)\n")
    else:
        (project / "span.py").write_text("print(0)\n")
        if changed == "commit":
            example.git(project, "add", "span.py")
            example.git(project, "commit", "-qm", "Unverified replacement")
    with pytest.raises(RuntimeError):
        example.recover(root)
    assert (
        example.load(project / "PLAN.md").phases[0].tasks[0].status
        == example.TaskStatus.TODO
    )
    assert example.pending_receipts(project)
    assert json.loads((root / "acceptance.json").read_text())["editor_invocations"] == 1


def test_external_editor_protocol_and_path_restriction(tmp_path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "import json, sys\np = json.load(sys.stdin)\n"
        'print(json.dumps({"span.py": p["files"]["span.py"].replace('
        '"end - start)", "end - start + 1)")}))\n'
    )
    result = invoke(
        "run", tmp_path / "external", "--editor-command", sys.executable, adapter
    )
    assert result.returncode == 0, result.stdout + result.stderr
    root = tmp_path / "invalid"
    example.prepare(root, "external")
    adapter.write_text('print(\'{"../oracle.py": "tampered"}\')\n')
    original = example.digest(root / "oracle.py")
    with pytest.raises(RuntimeError, match="unsupported filename"):
        example.attempt(root, editor_command=[sys.executable, str(adapter)])
    assert example.digest(root / "oracle.py") == original


def test_existing_output_is_preserved(demonstration):
    before = (demonstration / "result.json").read_bytes()
    assert invoke("demo", demonstration).returncode != 0
    assert (demonstration / "result.json").read_bytes() == before


def test_passing_checks_cannot_substitute_candidate_bytes(tmp_path, monkeypatch):
    root = tmp_path / "mutating-check"
    example.prepare(root, "no-op")

    def mutate_during_checks(project, *_):
        correct = (
            (project / "span.py")
            .read_text()
            .replace("end - start)", "end - start + 1)")
        )
        (project / "regression.py").write_text(
            f"from pathlib import Path\nPath('span.py').write_text({correct!r})\n"
        )

    monkeypatch.setattr(example, "edit", mutate_during_checks)
    with pytest.raises(RuntimeError, match="Checks modified the candidate snapshot"):
        example.attempt(root)
    checks = json.loads((root / "checks.log").read_text())
    assert all(check["passed"] for check in checks)
    assert example.git(root / "project", "rev-list", "--count", "HEAD") == "1"
    assert (
        example.load(root / "project/PLAN.md").phases[0].tasks[0].status
        == example.TaskStatus.TODO
    )
