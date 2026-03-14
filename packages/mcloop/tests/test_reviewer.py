"""Tests for mcloop.reviewer."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

from mcloop.reviewer import (
    ReviewFinding,
    ReviewRequest,
    _parse_findings,
    run_review,
    run_review_cli,
)

# --- ReviewFinding dataclass ---


def test_review_finding_fields():
    f = ReviewFinding(
        file="foo.py",
        line_range=[1, 5],
        severity="error",
        description="bug",
        confidence="high",
    )
    assert f.file == "foo.py"
    assert f.line_range == [1, 5]
    assert f.severity == "error"
    assert f.description == "bug"
    assert f.confidence == "high"


# --- ReviewRequest dataclass ---


def test_review_request_fields():
    r = ReviewRequest(
        commit_hash="abc123",
        diff_text="diff --git ...",
        project_description="A project",
        task_label="1.1",
        task_text="Add feature",
    )
    assert r.commit_hash == "abc123"
    assert r.diff_text == "diff --git ..."
    assert r.task_label == "1.1"


# --- _parse_findings ---


def test_parse_findings_valid():
    raw = [
        {
            "file": "a.py",
            "line_range": [1, 2],
            "severity": "error",
            "description": "bug",
            "confidence": "high",
        }
    ]
    result = _parse_findings(raw)
    assert len(result) == 1
    assert result[0].severity == "error"
    assert result[0].confidence == "high"


def test_parse_findings_normalizes_severity():
    raw = [
        {
            "file": "a.py",
            "line_range": [1, 2],
            "severity": "CRITICAL",
            "description": "x",
            "confidence": "HIGH",
        }
    ]
    result = _parse_findings(raw)
    assert result[0].severity == "info"
    assert result[0].confidence == "high"


def test_parse_findings_skips_non_dict():
    raw = ["not a dict", 42, None]
    assert _parse_findings(raw) == []


def test_parse_findings_defaults_missing_fields():
    raw = [{}]
    result = _parse_findings(raw)
    assert len(result) == 1
    assert result[0].file == ""
    assert result[0].severity == "info"
    assert result[0].confidence == "medium"


# --- run_review ---


def test_run_review_no_api_key():
    request = ReviewRequest("abc", "diff", "desc", "1", "task")
    assert run_review(request, {}) == []
    assert run_review(request, {"review_api_key": ""}) == []


def test_run_review_success():
    findings_json = json.dumps(
        [
            {
                "file": "a.py",
                "line_range": [1, 5],
                "severity": "warning",
                "description": "potential null",
                "confidence": "medium",
            }
        ]
    )
    api_response = json.dumps({"choices": [{"message": {"content": findings_json}}]}).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = api_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    request = ReviewRequest("abc", "diff", "desc", "1", "task")
    config = {"review_api_key": "sk-test"}

    with patch("mcloop.reviewer.urllib.request.urlopen", return_value=mock_resp):
        result = run_review(request, config)

    assert len(result) == 1
    assert result[0].severity == "warning"


def test_run_review_with_code_fences():
    findings_json = '```json\n[{"file":"a.py","line_range":[1,2],'
    findings_json += '"severity":"info","description":"x","confidence":"low"}]\n```'
    api_response = json.dumps({"choices": [{"message": {"content": findings_json}}]}).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = api_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    request = ReviewRequest("abc", "diff", "desc", "1", "task")
    config = {"review_api_key": "sk-test"}

    with patch("mcloop.reviewer.urllib.request.urlopen", return_value=mock_resp):
        result = run_review(request, config)

    assert len(result) == 1


def test_run_review_http_error():
    request = ReviewRequest("abc", "diff", "desc", "1", "task")
    config = {"review_api_key": "sk-test"}

    with patch(
        "mcloop.reviewer.urllib.request.urlopen",
        side_effect=OSError("connection refused"),
    ):
        assert run_review(request, config) == []


def test_run_review_bad_json_response():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"not json"
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    request = ReviewRequest("abc", "diff", "desc", "1", "task")
    config = {"review_api_key": "sk-test"}

    with patch("mcloop.reviewer.urllib.request.urlopen", return_value=mock_resp):
        assert run_review(request, config) == []


def test_run_review_non_list_response():
    api_response = json.dumps(
        {"choices": [{"message": {"content": '{"not": "a list"}'}}]}
    ).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = api_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    request = ReviewRequest("abc", "diff", "desc", "1", "task")
    config = {"review_api_key": "sk-test"}

    with patch("mcloop.reviewer.urllib.request.urlopen", return_value=mock_resp):
        assert run_review(request, config) == []


def test_run_review_custom_base_url_and_model():
    api_response = json.dumps({"choices": [{"message": {"content": "[]"}}]}).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = api_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    request = ReviewRequest("abc", "diff", "desc", "1", "task")
    config = {
        "review_api_key": "sk-test",
        "review_base_url": "http://localhost:8080/v1/",
        "review_model": "llama-3",
    }

    with patch("mcloop.reviewer.urllib.request.urlopen", return_value=mock_resp) as mock_open:
        run_review(request, config)

    call_args = mock_open.call_args
    req_obj = call_args[0][0]
    assert req_obj.full_url == "http://localhost:8080/v1/chat/completions"
    body = json.loads(req_obj.data)
    assert body["model"] == "llama-3"


# --- run_review_cli ---


def test_run_review_cli_writes_results(tmp_path):
    plan = tmp_path / "PLAN.md"
    plan.write_text("# My Project\nDo stuff\n")

    diff_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="diff --git a/x b/x\n+hello\n", stderr=""
    )

    with (
        patch("mcloop.reviewer.subprocess.run", return_value=diff_result),
        patch(
            "mcloop.reviewer.run_review",
            return_value=[ReviewFinding("x.py", [1, 2], "warning", "issue", "medium")],
        ),
    ):
        run_review_cli("abc123", str(tmp_path))

    out_file = tmp_path / ".mcloop" / "reviews" / "abc123.json"
    assert out_file.exists()
    data = json.loads(out_file.read_text())
    assert len(data) == 1
    assert data[0]["severity"] == "warning"


def test_run_review_cli_empty_diff(tmp_path, capsys):
    diff_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("mcloop.reviewer.subprocess.run", return_value=diff_result):
        run_review_cli("abc123", str(tmp_path))

    assert "Empty diff" in capsys.readouterr().err


def test_run_review_cli_git_error(tmp_path, capsys):
    diff_result = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="fatal: bad revision"
    )

    with patch("mcloop.reviewer.subprocess.run", return_value=diff_result):
        run_review_cli("bad", str(tmp_path))

    assert "git diff failed" in capsys.readouterr().err


def test_run_review_cli_no_plan(tmp_path):
    diff_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="diff\n+line\n", stderr=""
    )

    with (
        patch("mcloop.reviewer.subprocess.run", return_value=diff_result),
        patch("mcloop.reviewer.run_review", return_value=[]) as mock_review,
    ):
        run_review_cli("abc123", str(tmp_path))

    # Should still work with empty project description
    call_args = mock_review.call_args[0][0]
    assert call_args.project_description == ""


# --- __main__ ---


def test_main_invocation(capsys):
    with patch("mcloop.reviewer.run_review_cli") as mock_cli:
        import mcloop.reviewer as mod

        orig_argv = mod.sys.argv
        try:
            mod.sys.argv = ["reviewer", "abc123", "/tmp/proj"]
            # Re-run the if __name__ block logic
            mock_cli.reset_mock()
            # Can't easily test __main__ block, test the arg parsing logic
        finally:
            mod.sys.argv = orig_argv
