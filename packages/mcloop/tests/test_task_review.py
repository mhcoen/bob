"""Requirement review rejects absent evidence and invalid reviewer responses."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.task_review import (
    EVIDENCE_PATH,
    MAX_INPUT_BYTES,
    ReviewPolicy,
    _request_review,
    build_packet,
    load_policy,
    prepare_evidence,
    review_task,
)


@pytest.fixture
def project(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "Baseline",
        ],
        cwd=tmp_path,
        check=True,
    )
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (tmp_path / ".mcloop").mkdir()
    (tmp_path / "DESIGN.md").write_text("Delivery operations must use a disk-backed sequence.\n")
    (tmp_path / "ports.swift").write_text("struct Plan { let operations: [String] }\n")
    (tmp_path / "smoke.swift").write_text('XCTAssertEqual(PortsModule.name, "Ports")\n')
    evidence = {
        "requirements": [
            {
                "requirement": "Bound delivery memory independently of transcript length",
                "design": ["DESIGN.md:1-1"],
                "implementation": ["ports.swift:1-1"],
                "verification": ["smoke.swift:1-1"],
            }
        ]
    }
    (tmp_path / EVIDENCE_PATH).write_text(json.dumps(evidence))
    policy = ReviewPolicy(
        True, "review-model", documents=(("DESIGN.md", (tmp_path / "DESIGN.md").read_text()),)
    )
    return tmp_path, policy, baseline


def verdict(packet, *, accepted=True):
    return json.dumps(
        {
            "verdict": "accept" if accepted else "reject",
            "requirements": [
                {
                    "requirement_id": packet["requirements"][0]["requirement_id"],
                    "satisfied": accepted,
                    "evidence": ["DESIGN.md:1-1", "ports.swift:1-1", "smoke.swift:1-1"],
                    "reason": (
                        "The array stores the complete transcript. "
                        "The smoke assertion checks a name."
                    ),
                }
            ],
            "findings": [] if accepted else ["ports.swift:1-1 contradicts DESIGN.md:1-1."],
        }
    )


def test_packet_includes_offending_code_and_actual_smoke_assertion(project):
    root, policy, baseline = project
    packet = build_packet(root, policy, "Define bounded delivery ports", baseline)
    assert "[String]" in packet["changed_files"]["ports.swift"]
    ref = packet["requirements"][0]["verification"][0]
    location = packet["evidence"][ref["evidence_id"]]
    assert "PortsModule.name" in packet["changed_files"][location["changed_file"]]
    with patch(
        "mcloop.task_review._request_review", return_value=verdict(packet, accepted=False)
    ) as call:
        result = review_task(root, policy, packet["task"], baseline, "editor-model")
    assert not result.passed
    assert "contradicts" in result.output
    call.assert_called_once()
    assert json.loads(Path(result.receipt).read_text())["passed"] is False


def test_valid_review_is_bound_to_packet(project):
    root, policy, baseline = project
    packet = build_packet(root, policy, "Task", baseline)
    with patch("mcloop.task_review._request_review", return_value=verdict(packet)):
        result = review_task(root, policy, "Task", baseline, "editor-model")
    assert result.passed
    assert json.loads(Path(result.receipt).read_text())["input_sha256"]


@pytest.mark.parametrize("damage", [None, "missing", "duplicate", "unknown", "no_id"])
def test_requirement_ids_preserve_complete_unambiguous_coverage(project, damage):
    root, policy, baseline = project
    path = root / EVIDENCE_PATH
    evidence = json.loads(path.read_text())
    # Identical wording must still represent two distinct supplied obligations.
    evidence["requirements"] *= 2
    path.write_text(json.dumps(evidence))
    packet = build_packet(root, policy, "Task", baseline)
    assert [item["requirement_id"] for item in packet["requirements"]] == ["Q1", "Q2"]
    response = json.loads(verdict(packet))
    assessment = response["requirements"][0]
    assessment["requirement"] = "A shortened description the reviewer volunteered"
    response["requirements"].append({**assessment, "requirement_id": "Q2"})
    if damage == "missing":
        response["requirements"].pop()
    elif damage == "duplicate":
        response["requirements"][1]["requirement_id"] = "Q1"
    elif damage == "unknown":
        response["requirements"][1]["requirement_id"] = "Q3"
    elif damage == "no_id":
        del response["requirements"][1]["requirement_id"]
    with patch("mcloop.task_review._request_review", return_value=json.dumps(response)) as call:
        result = review_task(root, policy, "Task", baseline, "editor-model")
    call.assert_called_once()
    assert result.passed is (damage is None)
    if damage is None:
        receipt = json.loads(Path(result.receipt).read_text())
        assert all(
            item["requirement"] == packet["requirements"][0]["requirement"]
            for item in receipt["review"]["requirements"]
        )
    elif damage == "missing":
        assert "Q2" in result.output


def test_changed_file_and_repeated_citations_share_one_copy(project):
    root, policy, baseline = project
    content = "// whole-file-start\n" + "x" * 60_000 + "\n// whole-file-end\n"
    (root / "ports.swift").write_text(content)
    evidence = json.loads((root / EVIDENCE_PATH).read_text())
    evidence["requirements"][0]["implementation"] = ["ports.swift:1-3"] * 3
    (root / EVIDENCE_PATH).write_text(json.dumps(evidence))
    packet = build_packet(root, policy, "Task", baseline)
    encoded = json.dumps(packet).encode()
    assert len(encoded) < MAX_INPUT_BYTES
    assert packet["changed_files"]["ports.swift"] == content
    assert encoded.count(b"whole-file-start") == 1
    refs = packet["requirements"][0]["implementation"]
    assert len({ref["evidence_id"] for ref in refs}) == 1
    location = packet["evidence"][refs[0]["evidence_id"]]
    assert (location["changed_file"], location["start_line"], location["end_line"]) == (
        "ports.swift",
        1,
        3,
    )


def test_unchanged_cited_passage_is_included_once(project):
    root, policy, _ = project
    subprocess.run(["git", "add", "DESIGN.md"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Accept design",
        ],
        cwd=root,
        check=True,
    )
    baseline = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    evidence = json.loads((root / EVIDENCE_PATH).read_text())
    evidence["requirements"][0]["design"] *= 3
    (root / EVIDENCE_PATH).write_text(json.dumps(evidence))
    packet = build_packet(root, policy, "Task", baseline)
    refs = packet["requirements"][0]["design"]
    assert len({ref["evidence_id"] for ref in refs}) == 1
    passage = packet["evidence"][refs[0]["evidence_id"]]
    assert passage["text"] == (root / "DESIGN.md").read_text().rstrip("\n")
    assert json.dumps(packet).count("Delivery operations must use a disk-backed sequence.") == 1


@pytest.mark.parametrize("unknown", [False, True])
def test_review_cites_packet_evidence_ids(project, unknown):
    root, policy, baseline = project
    packet = build_packet(root, policy, "Task", baseline)
    references = [
        ref
        for requirement in packet["requirements"]
        for field in ("design", "implementation", "verification")
        for ref in requirement[field]
    ]
    assert len({ref["evidence_id"] for ref in references}) == len(references)
    assert build_packet(root, policy, "Task", baseline) == packet
    response = json.loads(verdict(packet))
    response["requirements"][0]["evidence"] = (
        ["R999"] if unknown else [ref["evidence_id"] for ref in references]
    )
    with patch("mcloop.task_review._request_review", return_value=json.dumps(response)):
        result = review_task(root, policy, "Task", baseline, "editor-model")
    assert result.passed is not unknown
    if not unknown:
        receipt = json.loads(Path(result.receipt).read_text())
        assert receipt["input"]["requirements"] == packet["requirements"]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        "{}",
        "not JSON",
        '{"verdict":"accept","requirements":[],"findings":[]}',
        '{"verdict":"accept","requirements":[{"satisfied":true}],"findings":[]}',
    ],
)
def test_invalid_review_never_passes(project, raw):
    root, policy, baseline = project
    with patch("mcloop.task_review._request_review", return_value=raw):
        assert not review_task(root, policy, "Task", baseline, "editor-model").passed


@pytest.mark.parametrize("damage", ["missing", "range", "escape", "oversized", "design_changed"])
def test_invalid_input_stops_before_network(project, damage):
    root, policy, baseline = project
    evidence_path = root / EVIDENCE_PATH
    if damage == "missing":
        evidence_path.unlink()
    elif damage == "range":
        evidence_path.write_text(
            evidence_path.read_text().replace("ports.swift:1-1", "ports.swift:1-50")
        )
    elif damage == "escape":
        evidence_path.write_text(
            evidence_path.read_text().replace("ports.swift:1-1", "../secret:1-1")
        )
    elif damage == "oversized":
        (root / "ports.swift").write_text("x" * MAX_INPUT_BYTES)
    else:
        (root / "DESIGN.md").write_text("Arrays are fine.\n")
    with patch("mcloop.task_review._request_review") as call:
        assert not review_task(root, policy, "Task", baseline, "editor-model").passed
    call.assert_not_called()


def test_source_change_during_review_invalidates_verdict(project):
    root, policy, baseline = project

    def respond(_policy, packet):
        (root / "ports.swift").write_text("changed\n")
        return verdict(packet)

    with patch("mcloop.task_review._request_review", side_effect=respond):
        result = review_task(root, policy, "Task", baseline, "editor-model")
    assert not result.passed
    assert "changed during review" in result.output


def test_transport_failure_stops_without_retry(project):
    root, policy, baseline = project
    with patch("mcloop.task_review._request_review", side_effect=TimeoutError) as call:
        result = review_task(root, policy, "Task", baseline, "editor-model")
    assert not result.passed
    call.assert_called_once()


def test_editor_cannot_review_own_work(project):
    root, policy, baseline = project
    with patch("mcloop.task_review._request_review") as call:
        assert not review_task(root, policy, "Task", baseline, "review-model").passed
    call.assert_not_called()


def test_prepare_discards_stale_evidence(project):
    root, policy, _ = project
    prompt = prepare_evidence(root, policy, "New task")
    assert not (root / EVIDENCE_PATH).exists()
    assert "New task" in prompt and "verification" in prompt


def test_request_has_no_tools_and_fixed_output_bound(project, monkeypatch):
    _, policy, _ = project
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-token")
    with patch("mcloop.task_review.urllib.request.urlopen") as call:
        call.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "{}"},
                    }
                ]
            }
        ).encode()
        assert _request_review(policy, {"task": "Check"}) == "{}"
    payload = json.loads(call.call_args.args[0].data)
    assert payload["messages"][1]["content"] == '{"task":"Check"}'
    assert payload["max_tokens"] == 3000
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["response_format"] == {"type": "json_object"}
    assert "tools" not in payload
    assert call.call_args.kwargs["timeout"] == 90


def test_design_project_requires_review_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "SOFTWARE_DESIGN.md").write_text("Design")
    with pytest.raises(ValueError, match="task_review.model"):
        load_policy(tmp_path)


def test_legacy_project_is_unchanged_and_explicit_disable_is_respected(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert not load_policy(tmp_path).enabled
    (tmp_path / "SOFTWARE_DESIGN.md").write_text("Design")
    (tmp_path / ".mcloop").mkdir()
    (tmp_path / ".mcloop/config.json").write_text('{"task_review":{"enabled":false}}')
    assert not load_policy(tmp_path).enabled


@pytest.mark.parametrize(
    "damage", ["contradiction", "string_boolean", "unknown_reference", "omission"]
)
def test_incomplete_or_contradictory_acceptance_is_rejected(project, damage):
    root, policy, baseline = project
    packet = build_packet(root, policy, "Task", baseline)
    response = json.loads(verdict(packet))
    assessment = response["requirements"][0]
    if damage == "contradiction":
        response["findings"] = ["Contract violated"]
    elif damage == "string_boolean":
        assessment["satisfied"] = "false"
    elif damage == "unknown_reference":
        assessment["evidence"] = ["nonexistent.swift:1-2"]
    else:
        assessment["requirement_id"] = "Q999"
    with patch("mcloop.task_review._request_review", return_value=json.dumps(response)):
        assert not review_task(root, policy, "Task", baseline, "editor-model").passed


def test_complete_json_fence_is_accepted(project):
    root, policy, baseline = project
    packet = build_packet(root, policy, "Task", baseline)
    with patch(
        "mcloop.task_review._request_review", return_value="```json\n" + verdict(packet) + "\n```"
    ):
        assert review_task(root, policy, "Task", baseline, "editor-model").passed


def test_editor_cannot_disable_frozen_review_policy(project, monkeypatch):
    root, _, baseline = project
    monkeypatch.setattr(Path, "home", lambda: root / "home")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-token")
    config = root / ".mcloop/config.json"
    config.write_text(
        json.dumps(
            {
                "task_review": {
                    "enabled": True,
                    "model": "review-model",
                    "documents": ["DESIGN.md"],
                }
            }
        )
    )
    policy = load_policy(root)
    config.write_text('{"task_review":{"enabled":false}}')
    with patch("mcloop.task_review._request_review") as call:
        result = review_task(root, policy, "Task", baseline, "editor-model")
    assert not result.passed
    assert "configuration changed" in result.output
    call.assert_not_called()


def test_file_heading_and_python_symbol_refs_are_resolved(project):
    root, policy, baseline = project
    design = "# Design\n\n## D-005: Storage\nKeep committed rows.\n\n## Other\nOther contract.\n"
    (root / "DESIGN.md").write_text(design)
    (root / "store.py").write_text("def migrate():\n    return 1\n\ndef unrelated():\n    pass\n")
    policy = ReviewPolicy(True, "review-model", documents=(("DESIGN.md", design),))
    evidence = {
        "requirements": [
            {
                "requirement": "Preserve rows",
                "design": ["DESIGN.md#D-005"],
                "implementation": ["store.py#migrate"],
                "verification": ["smoke.swift"],
            }
        ]
    }
    (root / EVIDENCE_PATH).write_text(json.dumps(evidence))
    packet = build_packet(root, policy, "Migrate", baseline)
    item = packet["requirements"][0]
    assert item["design"][0]["reference"] == "DESIGN.md:3-5"
    assert item["implementation"][0]["reference"] == "store.py:1-2"
    assert item["verification"][0]["reference"] == "smoke.swift:1-1"


def test_overlapping_references_preserve_all_cited_lines(project):
    root, policy, baseline = project
    (root / "ports.swift").write_text("one\ntwo\nthree\nfour\n")
    data = json.loads((root / EVIDENCE_PATH).read_text())
    data["requirements"][0]["implementation"] = ["ports.swift:1-3", "ports.swift:2-4"]
    (root / EVIDENCE_PATH).write_text(json.dumps(data))
    packet = build_packet(root, policy, "Task", baseline)
    refs = packet["requirements"][0]["implementation"]
    assert refs[0]["evidence_id"] == refs[1]["evidence_id"]
    assert refs[0]["reference"] == "ports.swift:1-4"
    assert packet["changed_files"]["ports.swift"] == "one\ntwo\nthree\nfour\n"


@pytest.mark.parametrize("reference", ["../outside.py", "ports.swift#missing", "ports.swift:9-10"])
def test_bad_references_block_before_provider_request(project, reference):
    root, policy, baseline = project
    data = json.loads((root / EVIDENCE_PATH).read_text())
    data["requirements"][0]["implementation"] = [reference]
    (root / EVIDENCE_PATH).write_text(json.dumps(data))
    with patch("mcloop.task_review._request_review") as provider:
        result = review_task(root, policy, "Task", baseline, "editor")
    assert result.blocked and not result.passed
    provider.assert_not_called()
    record = json.loads(Path(result.receipt).read_text())
    assert record["status"] == "blocked"
    assert record["timings"]["evidence"] >= 0


def test_resume_survives_checkpoint_and_evidence_repair_but_not_code_changes(project):
    from mcloop import review_resume
    from mcloop.runner import RunResult

    root, policy, baseline = project
    result = RunResult(True, "done", 0, root / "logs/editor.log")
    review_resume.save(root, policy, "Task", baseline, "editor-model", result)
    (root / EVIDENCE_PATH).write_text('{"requirements": []}')
    subprocess.run(["git", "add", "ports.swift"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Checkpoint editor work",
        ],
        cwd=root,
        check=True,
    )
    restored = review_resume.load(root, policy, "Task")
    assert restored is not None and restored[0] == baseline
    assert restored[1] == "editor-model"
    assert review_resume.load(root, ReviewPolicy(True, "other-model"), "Task") is None
    (root / "ports.swift").write_text("changed implementation\n")
    assert review_resume.load(root, policy, "Task") is None


def test_blocked_review_resume_never_calls_editor_again(project, monkeypatch):
    from plan_fixtures import canonical_plan_text

    from mcloop._planfile_compat import parse
    from mcloop.main import run_loop
    from mcloop.runner import RunResult
    from mcloop.task_review import TaskReview

    root, policy, baseline = project
    plan = root / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Preserve rows [accept: command-exit: true]\n"))
    monkeypatch.setattr("mcloop.main.load_policy", lambda root: policy)
    monkeypatch.setattr("mcloop.main._ensure_git", lambda *args: None)
    monkeypatch.setattr("mcloop.main._kill_orphan_sessions", lambda *args: None)
    monkeypatch.setattr("mcloop.main.run_autofix", lambda *args, **kwargs: None)
    monkeypatch.setattr("mcloop.main._get_git_hash", lambda *args: baseline)
    with (
        patch(
            "mcloop.main.run_task",
            return_value=RunResult(True, "done", 0, root / "logs/editor.log"),
        ) as editor,
        patch(
            "mcloop.main.review_task",
            return_value=TaskReview(False, "Provider unavailable", blocked=True),
        ) as reviewer,
    ):
        run_loop(plan, no_audit=True)
        assert not parse(plan)[0].failed and not parse(plan)[0].checked
        run_loop(plan, no_audit=True)
    assert editor.call_count == 1
    assert reviewer.call_count == 2
    for call in reviewer.call_args_list:
        assert call.kwargs["checks"].command == "true"
        assert call.kwargs["checks"].passed
    assert not parse(plan)[0].checked


def test_batch_blocked_review_preserves_editor_work(project, monkeypatch):
    from test_args import _make_batch_args

    from mcloop.main import _run_batch
    from mcloop.runner import RunResult
    from mcloop.task_review import TaskReview

    root, policy, baseline = project
    args = _make_batch_args(root)
    args.update(project_dir=root, task_review_policy=policy)
    monkeypatch.setattr("mcloop.main._get_git_hash", lambda *args: baseline)
    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch(
            "mcloop.main.run_task",
            return_value=RunResult(True, "done", 0, root / "logs/editor.log"),
        ) as editor,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["ports.swift"]),
        patch("mcloop.main.run_autofix"),
        patch("mcloop.main.run_checks") as checks,
        patch(
            "mcloop.main.review_task",
            return_value=TaskReview(False, "Transport failed", blocked=True),
        ) as reviewer,
        patch("mcloop.main._commit") as commit,
    ):
        checks.return_value.passed = True
        assert _run_batch(**args) == ("review_pending", "Transport failed")
        assert _run_batch(**args) == ("review_pending", "Transport failed")
    assert editor.call_count == 1
    assert checks.call_count == 2
    assert reviewer.call_args.kwargs["checks"] is checks.return_value
    commit.assert_not_called()
    assert (root / "ports.swift").read_text() == "struct Plan { let operations: [String] }\n"


def test_ambiguous_python_symbol_refuses_to_guess(project):
    root, policy, baseline = project
    (root / "duplicate.py").write_text(
        "class A:\n    def run(self): pass\nclass B:\n    def run(self): pass\n"
    )
    data = json.loads((root / EVIDENCE_PATH).read_text())
    data["requirements"][0]["implementation"] = ["duplicate.py#run"]
    (root / EVIDENCE_PATH).write_text(json.dumps(data))
    with pytest.raises(ValueError, match="one declaration"):
        build_packet(root, policy, "Task", baseline)


def test_review_retains_orchestrator_check_after_editor_notes(project):
    import hashlib

    from mcloop.checks import CheckResult

    root, policy, baseline = project
    (root / "editor-notes.md").write_text("swift test was not run in the editor session.\n")
    output = "setup output\n" * 1000 + "Executed 24 tests, with 0 failures.\n"
    check = CheckResult(True, output, "swift test")
    packet = build_packet(root, policy, "Task", baseline, check)
    with patch("mcloop.task_review._request_review", return_value=verdict(packet)) as request:
        result = review_task(root, policy, "Task", baseline, "editor", checks=check)
    assert result.passed
    supplied = request.call_args.args[1]
    observation = supplied["mcloop_checks"]
    assert observation["passed"] is True
    assert observation["command"] == "swift test"
    assert observation["output_tail"].endswith("Executed 24 tests, with 0 failures.\n")
    assert observation["output_omitted_bytes"] == len(output.encode()) - 4000
    assert observation["output_sha256"] == hashlib.sha256(output.encode()).hexdigest()
    assert "not run" in supplied["changed_files"]["editor-notes.md"]
    receipt = json.loads(Path(result.receipt).read_text())
    assert receipt["mcloop_checks"]["output"] == output


@pytest.mark.parametrize("output", ["assertion failed", "TIMEOUT after 300s", "Command not found"])
def test_failed_checks_never_request_review(project, output):
    from mcloop.checks import CheckResult

    root, policy, baseline = project
    with patch("mcloop.task_review._request_review") as request:
        result = review_task(
            root, policy, "Task", baseline, "editor", CheckResult(False, output, "swift test")
        )
    assert result.blocked and not result.passed
    request.assert_not_called()


def test_absent_and_waived_checks_do_not_claim_test_execution(project):
    from mcloop.checks import CheckResult

    root, policy, baseline = project
    assert "mcloop_checks" not in build_packet(root, policy, "Task", baseline)
    packet = build_packet(
        root,
        policy,
        "Task",
        baseline,
        CheckResult(True, "Covered by later hardware acceptance", "accept:waived:T-2"),
    )
    assert packet["mcloop_checks"]["command"] == "accept:waived:T-2"
    assert "exit_code" not in packet["mcloop_checks"]


def test_large_review_uses_one_complete_request(project):
    root, policy, baseline = project
    content = "// Start\n" + "x" * 150_000 + "\n// End\n"
    (root / "ports.swift").write_text(content)
    packet = build_packet(root, policy, "Task", baseline)
    with patch("mcloop.task_review._request_review", return_value=verdict(packet)) as request:
        result = review_task(root, policy, "Task", baseline, "editor")
    assert result.passed
    request.assert_called_once()
    assert request.call_args.args[1]["changed_files"]["ports.swift"] == content
    receipt = json.loads(Path(result.receipt).read_text())
    assert receipt["input_bytes"] > 96000
    assert receipt["input_limit_bytes"] == 256000
    assert receipt["input_sections_bytes"]["changed_files"] > 150000


@pytest.mark.parametrize("limit", [0, -1, True, "256000", 1024001])
def test_invalid_input_budget_is_rejected(limit):
    with pytest.raises(ValueError, match="max_input_bytes"):
        ReviewPolicy(max_input_bytes=limit)


def test_budget_refusal_reports_context_and_preserves_no_request(project):
    from dataclasses import replace

    root, policy, baseline = project
    policy = replace(policy, max_input_bytes=500)
    with patch("mcloop.task_review._request_review") as request:
        result = review_task(root, policy, "Task", baseline, "editor")
    assert result.blocked
    assert "Sections:" in result.output and "evidence" in result.output
    assert "max_input_bytes" in result.output
    request.assert_not_called()


def test_budget_only_configuration_change_preserves_editor_checkpoint(project):
    import hashlib
    from dataclasses import replace

    from mcloop import review_resume
    from mcloop.runner import RunResult

    root, policy, baseline = project
    config = root / ".mcloop/config.json"

    def configure(budget, model="review-model"):
        config.write_text(json.dumps({"task_review": {"max_input_bytes": budget, "model": model}}))
        return replace(
            policy,
            model=model,
            max_input_bytes=budget,
            configuration=((config, hashlib.sha256(config.read_bytes()).hexdigest()),),
        )

    first = configure(96000)
    review_resume.save(
        root, first, "Task", baseline, "editor", RunResult(True, "done", 0, Path("editor.log"))
    )
    assert review_resume.load(root, configure(256000), "Task") is not None
    assert review_resume.load(root, configure(256000, "changed-reviewer"), "Task") is None


def test_load_policy_uses_configured_input_budget(project, monkeypatch):
    root, _, _ = project
    monkeypatch.setattr(Path, "home", lambda: root / "home")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-token")
    (root / ".mcloop/config.json").write_text(
        json.dumps(
            {
                "task_review": {
                    "enabled": True,
                    "model": "review-model",
                    "documents": ["DESIGN.md"],
                    "max_input_bytes": 180000,
                }
            }
        )
    )
    assert load_policy(root).max_input_bytes == 180000


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_provider_accounting_survives_success_and_blocked_output(project, monkeypatch, finish):
    root, policy, baseline = project
    packet = build_packet(root, policy, "Task", baseline)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-token")
    usage = {
        "prompt_tokens": 1234,
        "completion_tokens": 67,
        "prompt_tokens_details": {"cached_tokens": 1000},
        "cost": 0.001,
    }
    body = {
        "id": "generation-fixture",
        "model": "review-model",
        "usage": usage,
        "choices": [{"finish_reason": finish, "message": {"content": verdict(packet)}}],
    }
    with patch("mcloop.task_review.urllib.request.urlopen") as request:
        request.return_value.__enter__.return_value.read.return_value = json.dumps(body).encode()
        result = review_task(root, policy, "Task", baseline, "editor")
    receipt = json.loads(Path(result.receipt).read_text())
    assert receipt["provider"] == {
        "id": "generation-fixture",
        "model": "review-model",
        "usage": usage,
    }
    assert result.passed is (finish == "stop")
    assert result.blocked is (finish == "length")


def test_acceptance_repair_preserves_existing_requirement_evidence(project):
    root, policy, _ = project
    original = (root / EVIDENCE_PATH).read_text()
    instruction = prepare_evidence(root, policy, "Repair the test", preserve_existing=True)
    assert (root / EVIDENCE_PATH).read_text() == original
    assert "Repair the test" in instruction
