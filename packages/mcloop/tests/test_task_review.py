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
                    "requirement": packet["requirements"][0]["requirement"],
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
        assessment["requirement"] = "A different task"
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
