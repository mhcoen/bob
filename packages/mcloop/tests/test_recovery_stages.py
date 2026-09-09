"""Recover review failures without repeating verified project work."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import test_task_review
from plan_fixtures import canonical_plan_text
from test_args import _run_loop_with_patches

from mcloop._planfile_compat import parse
from mcloop.checks import CheckResult
from mcloop.completion import pending_receipts
from mcloop.review_packets import partition
from mcloop.review_resume import checked_stage
from mcloop.runner import RunResult
from mcloop.task_review import ReviewResponseError, build_packet, review_task


@pytest.fixture
def project(tmp_path):
    return test_task_review.project.__wrapped__(tmp_path)


def accept_all(packet):
    return json.dumps(
        {
            "verdict": "accept",
            "findings": [],
            "requirements": [
                {
                    "requirement_id": entry["requirement_id"],
                    "satisfied": True,
                    "reason": "The declaration and assertions implement this requirement.",
                    "evidence": [entry["design"][0]["evidence_id"]],
                }
                for entry in packet["requirements"]
            ],
        }
    )


def test_checks_resume_without_regenerating_artifacts_and_invalidate(project, monkeypatch):
    root, policy, _ = project
    calls = []

    def check():
        calls.append(1)
        (root / "observation.txt").write_text(f"run {len(calls)}")
        return CheckResult(True, "observed execution", "oracle")

    first = checked_stage(root, policy, "task", "oracle", check)
    assert checked_stage(root, policy, "task", "oracle", check) == first
    assert len(calls) == 1
    (root / "ports.swift").write_text("struct Changed {}")
    checked_stage(root, policy, "task", "oracle", check)
    assert len(calls) == 2
    monkeypatch.setenv("CHECK_INPUT", "changed")
    checked_stage(root, policy, "task", "oracle", check)
    assert len(calls) == 3
    checked_stage(root, policy, "task", "other command", check)
    assert len(calls) == 4


def test_failed_checks_are_never_reused(project):
    root, policy, _ = project
    with patch(__name__ + ".accept_all") as operation:
        operation.return_value = CheckResult(False, "failed", "oracle")
        checked_stage(root, policy, "task", "oracle", operation)
        checked_stage(root, policy, "task", "oracle", operation)
    assert operation.call_count == 2


def test_malformed_verdict_recovers_and_cached_verdict_survives_restart(project):
    root, policy, baseline = project
    packet = build_packet(root, policy, "Task", baseline)
    with patch(
        "mcloop.task_review._request_review", side_effect=["{}", accept_all(packet)]
    ) as call:
        result = review_task(root, policy, "Task", baseline, "editor")
    assert result.passed
    assert call.call_count == 2
    assert "response_correction" in call.call_args.args[1]
    with patch("mcloop.task_review._request_review") as call:
        resumed = review_task(root, policy, "Task", baseline, "editor")
    assert resumed.passed
    call.assert_not_called()
    assert json.loads(Path(resumed.receipt).read_text())["cached_parts"]
    (root / "ports.swift").write_text("struct Changed {}")
    with patch(
        "mcloop.task_review._request_review", side_effect=lambda _, p: accept_all(p)
    ) as call:
        assert review_task(root, policy, "Task", baseline, "editor").passed
    call.assert_called_once()


@pytest.mark.parametrize("transport_first", [False, True])
def test_exhausted_output_budget_survives_resume(project, transport_first):
    root, policy, baseline = project
    length = ReviewResponseError("length", {"choices": [{"finish_reason": "length"}]})
    failures = [length, length]
    if transport_first:
        failures.insert(0, TimeoutError())
    with (
        patch("mcloop.task_review.time.sleep"),
        patch("mcloop.task_review._request_review", side_effect=failures) as request,
    ):
        result = review_task(root, policy, "Task", baseline, "editor")
    assert result.blocked
    assert request.call_count == len(failures)
    packet = build_packet(root, policy, "Task", baseline)
    with patch("mcloop.task_review._request_review", return_value=accept_all(packet)) as request:
        assert review_task(root, policy, "Task", baseline, "editor").passed
    request.assert_called_once()
    assert request.call_args.kwargs == {"max_output_tokens": 9000}
    (root / "ports.swift").write_text("struct Changed {}")
    with patch(
        "mcloop.task_review._request_review", side_effect=lambda _, p: accept_all(p)
    ) as request:
        assert review_task(root, policy, "Task", baseline, "editor").passed
    assert not request.call_args.kwargs


@pytest.mark.parametrize("errors", [("timeout", "invalid"), ("invalid", "timeout")])
def test_transport_and_invalid_verdict_recover_without_editing(project, errors):
    root, policy, baseline = project
    packet = build_packet(root, policy, "Task", baseline)
    results = [TimeoutError() if error == "timeout" else "{}" for error in errors]
    with (
        patch("mcloop.task_review.time.sleep"),
        patch(
            "mcloop.task_review._request_review", side_effect=results + [accept_all(packet)]
        ) as call,
    ):
        result = review_task(root, policy, "Task", baseline, "editor")
    assert result.passed
    assert call.call_count == 3
    assert "response_correction" in call.call_args.args[1]


def test_length_at_saved_larger_budget_is_not_repeated(project):
    root, policy, baseline = project
    length = ReviewResponseError("length", {"choices": [{"finish_reason": "length"}]})
    with patch("mcloop.task_review._request_review", side_effect=length):
        assert review_task(root, policy, "Task", baseline, "editor").blocked
    with patch("mcloop.task_review._request_review", side_effect=length) as call:
        assert review_task(root, policy, "Task", baseline, "editor").blocked
    call.assert_called_once()
    assert call.call_args.kwargs == {"max_output_tokens": 9000}


def test_partition_preserves_every_change_and_every_cited_passage(project):
    root, policy, baseline = project
    for number in range(4):
        (root / f"large{number}.py").write_text("# content\n" * 500)
    packet = build_packet(root, policy, "Task", baseline)
    parts = partition(packet, 8000)
    assert 1 < len(parts) <= 4
    combined = {}
    for part in parts:
        assert len(json.dumps(part, ensure_ascii=False, separators=(",", ":")).encode()) <= 8000
        combined.update(part["changed_files"])
        assert part["requirements"] == packet["requirements"]
        for key, original in packet["evidence"].items():
            assert part["evidence"][key]["reference"] == original["reference"]
            assert part["evidence"][key]["text"]
    assert combined == packet["changed_files"]


def test_partial_review_resume_does_not_repeat_completed_parts(project):
    root, policy, baseline = project
    for number in range(3):
        (root / f"large{number}.py").write_text("# content\n" * 500)
    policy = replace(policy, max_input_bytes=8000)
    calls = []

    def provider(_, packet):
        part = packet["review_scope"]["part"]
        calls.append(part)
        if part == 2:
            raise TimeoutError()
        return accept_all(packet)

    with patch("mcloop.task_review._request_review", side_effect=provider):
        first = review_task(root, policy, "Task", baseline, "editor")
    assert first.blocked and calls == [1, 2, 2]
    with patch(
        "mcloop.task_review._request_review", side_effect=lambda _, p: accept_all(p)
    ) as call:
        final = review_task(root, policy, "Task", baseline, "editor")
    assert final.passed
    assert all(c.args[1]["review_scope"]["part"] != 1 for c in call.call_args_list)


def test_interrupted_request_leaves_attempt_receipt_and_no_acceptance(project):
    root, policy, baseline = project
    with patch("mcloop.task_review._request_review", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            review_task(root, policy, "Task", baseline, "editor")
    record = json.loads(next((root / ".mcloop/task-reviews").glob("*.json")).read_text())
    assert record["passed"] is False
    assert len(record["provider_attempts"]) == 1
    assert not list((root / ".mcloop/review-cache").glob("*.json"))


def test_unattended_tasks_recover_provider_and_verdict_failures(project, monkeypatch):
    root, policy, baseline = project
    plan = root / "PLAN.md"
    plan.write_text(
        canonical_plan_text(
            "".join(f"- [ ] Implement item {i} [accept: command-exit: oracle]\n" for i in range(4))
        )
    )
    monkeypatch.setattr("mcloop.main.load_policy", lambda _: policy)
    monkeypatch.setattr("mcloop.main._get_git_hash", lambda _: baseline)
    editors, checks, commits, requests = [], [], [], []

    def editor(task, *args, **kwargs):
        editors.append(task)
        # The fixtures' evidence maps the same contract for each isolated task.
        (root / ".mcloop/task-evidence.json").write_text(json.dumps(evidence))
        return RunResult(True, "edited", 0, root / "editor.log")

    evidence = json.loads((root / ".mcloop/task-evidence.json").read_text())

    def check(*args):
        checks.append(1)
        return CheckResult(True, "executed", "oracle")

    def provider(_, packet, **kwargs):
        requests.append(packet["task"])
        count = requests.count(packet["task"])
        if count == 1:
            index = len(editors)
            if index == 1:
                raise TimeoutError()
            if index == 2:
                return "{}"
            if index == 3:
                raise ReviewResponseError("length", {"choices": [{"finish_reason": "length"}]})
        return accept_all(packet)

    def commit(*args):
        commits.append(1)
        return ""

    with patch("mcloop.task_review._request_review", side_effect=provider):
        status, _ = _run_loop_with_patches(
            plan,
            no_audit=True,
            extra_patches={
                "mcloop.main.run_task": editor,
                "mcloop.main.run_command_acceptance": check,
                "mcloop.main._commit": commit,
                "mcloop.main.run_autofix": lambda *a, **k: None,
                "mcloop.main._maybe_auto_wrap": lambda *a: None,
            },
        )
    assert status.ok
    assert all(task.checked for task in parse(plan))
    assert len(editors) == len(checks) == len(commits) == 4
    assert len(requests) == 7
    assert not pending_receipts(root)


@pytest.mark.parametrize("failure", ["rejection", "evidence"])
def test_review_findings_and_evidence_errors_get_one_editor_repair(project, monkeypatch, failure):
    root, policy, baseline = project
    plan = root / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Implement result [accept: command-exit: oracle]\n"))
    evidence = json.loads((root / ".mcloop/task-evidence.json").read_text())
    editors, checks, requests = [], [], []
    monkeypatch.setattr("mcloop.main.load_policy", lambda _: policy)
    monkeypatch.setattr("mcloop.main._get_git_hash", lambda _: baseline)

    def editor(task, *args, **kwargs):
        editors.append(kwargs)
        if failure == "rejection":
            (root / "ports.swift").write_text(f"struct Plan{len(editors)} {{}}\n")
        data = json.loads(json.dumps(evidence))
        if failure == "evidence" and len(editors) == 1:
            data["requirements"][0]["design"] = ["smoke.swift:1-1"]
        (root / ".mcloop/task-evidence.json").write_text(json.dumps(data))
        return RunResult(True, "edited", 0, root / "editor.log")

    def check(*args):
        checks.append(1)
        return CheckResult(True, "executed", "oracle")

    def provider(_, packet):
        requests.append(1)
        value = json.loads(accept_all(packet))
        if failure == "rejection" and len(requests) == 1:
            value["verdict"] = "reject"
            value["findings"] = ["The required behavior is absent."]
            value["requirements"][0]["satisfied"] = False
        return json.dumps(value)

    with patch("mcloop.task_review._request_review", side_effect=provider):
        status, _ = _run_loop_with_patches(
            plan,
            no_audit=True,
            stop_after_one=True,
            extra_patches={
                "mcloop.main.run_task": editor,
                "mcloop.main.run_command_acceptance": check,
                "mcloop.main.run_autofix": lambda *a, **k: None,
                "mcloop.main._maybe_auto_wrap": lambda *a: None,
            },
        )
    assert status.ok and parse(plan)[0].checked
    assert len(editors) == 2
    assert len(checks) == (2 if failure == "rejection" else 1)
    assert len(requests) == (2 if failure == "rejection" else 1)
    assert "Repair" in editors[1]["prior_errors"]
    assert not pending_receipts(root)


def test_check_cache_invalidates_executable_and_file_mode_changes(project, monkeypatch):
    root, policy, _ = project
    executable = root / "oracle"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(root) + ":" + __import__("os").environ["PATH"])
    calls = []

    def check():
        calls.append(1)
        return CheckResult(True, "passed", "oracle")

    checked_stage(root, policy, "task", "oracle", check)
    checked_stage(root, policy, "task", "oracle", check)
    assert len(calls) == 1
    executable.chmod(0o644)
    checked_stage(root, policy, "task", "oracle", check)
    assert len(calls) == 2


def test_batch_recovers_truncated_verdict_without_repeating_editor(project, monkeypatch):
    from test_args import _make_batch_args

    from mcloop.main import _run_batch

    root, policy, baseline = project
    args = _make_batch_args(root)
    args.update(project_dir=root, task_review_policy=policy)
    monkeypatch.setattr("mcloop.main._get_git_hash", lambda *_: baseline)
    original = (root / ".mcloop/task-evidence.json").read_text()

    def edit(*a, **kw):
        (root / ".mcloop/task-evidence.json").write_text(original)
        return RunResult(True, "done", 0, root / "editor.log")

    responses = []

    def respond(_, packet, **kw):
        responses.append(1)
        return "{}" if len(responses) == 1 else accept_all(packet)

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main.run_task", side_effect=edit) as editor,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["ports.swift"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_autofix"),
        patch(
            "mcloop.main.run_checks", return_value=CheckResult(True, "passed", "oracle")
        ) as checks,
        patch("mcloop.task_review._request_review", side_effect=respond),
        patch("mcloop.main._commit", return_value="") as commit,
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._maybe_auto_wrap"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.check_off"),
    ):
        outcome, _ = _run_batch(**args)
    assert outcome == "success"
    editor.assert_called_once()
    checks.assert_called_once()
    commit.assert_called_once()
    assert len(responses) == 2


def test_http_deadline_kills_stalled_worker_without_exposing_credentials(monkeypatch):
    import subprocess
    import sys
    import urllib.request

    from mcloop.review_http import exchange

    real_run = subprocess.run
    commands = []

    def stalled_worker(command, **kwargs):
        commands.append(command)
        return real_run([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)

    monkeypatch.setattr("mcloop.review_http.subprocess.run", stalled_worker)
    request = urllib.request.Request(
        "https://example.invalid",
        data=b"private source",
        headers={"Authorization": "Bearer private-key"},
    )
    with pytest.raises(TimeoutError, match="deadline"):
        exchange(request, 1000, timeout=0.1)
    assert len(commands) == 1
    assert all("private" not in argument for argument in commands[0])


def test_invalid_cached_review_is_replaced_by_fresh_valid_review(project):
    root, policy, baseline = project
    with patch("mcloop.task_review._request_review", side_effect=lambda _, p: accept_all(p)):
        assert review_task(root, policy, "Task", baseline, "editor").passed
    cache = next((root / ".mcloop/review-cache").glob("*.json"))
    cache.write_text("{}")
    with patch(
        "mcloop.task_review._request_review", side_effect=lambda _, p: accept_all(p)
    ) as call:
        result = review_task(root, policy, "Task", baseline, "editor")
    assert result.passed
    call.assert_called_once()
    assert json.loads(Path(result.receipt).read_text())["invalid_cached_parts"]


def test_provider_error_inside_successful_http_response_is_retried(project):
    root, policy, baseline = project
    packet = build_packet(root, policy, "Task", baseline)
    failure = ReviewResponseError(
        "provider disconnected",
        {
            "usage": {"completion_tokens": 20},
            "choices": [{"finish_reason": "error", "error": {"code": 502}}],
        },
    )
    with patch(
        "mcloop.task_review._request_review", side_effect=[failure, accept_all(packet)]
    ) as call:
        result = review_task(root, policy, "Task", baseline, "editor")
    assert result.passed and call.call_count == 2
    receipt = json.loads(Path(result.receipt).read_text())
    assert receipt["provider_attempts"][0]["provider_status"] == 502


def test_evidence_json_fence_is_normalized_without_model_repair(project):
    root, policy, baseline = project
    file = root / ".mcloop/task-evidence.json"
    file.write_text("```json\n" + file.read_text() + "\n```")
    assert build_packet(root, policy, "Task", baseline)["requirements"]


def test_ignored_check_input_changes_invalidate_saved_result(project):
    root, policy, _ = project
    (root / ".gitignore").write_text("input.flag\n")
    flag = root / "input.flag"
    flag.write_text("pass")
    calls = []

    def check():
        calls.append(1)
        return CheckResult(flag.read_text() == "pass", flag.read_text(), "oracle")

    assert checked_stage(root, policy, "task", "oracle", check).passed
    assert checked_stage(root, policy, "task", "oracle", check).passed
    assert len(calls) == 1
    flag.write_text("fail")
    assert not checked_stage(root, policy, "task", "oracle", check).passed
    assert len(calls) == 2
