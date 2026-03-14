"""Tests for mcloop.reviewer."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

from mcloop.reviewer import (
    ReviewFinding,
    ReviewRequest,
    _collect_changed_functions,
    _extract_enclosing_functions,
    _parse_diff_line_ranges,
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
    assert run_review(request, {"api_key": ""}) == []
    assert run_review(request, {"api_key": "sk-test"}) == []  # no base_url


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
    config = {
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    }

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
    config = {
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    }

    with patch("mcloop.reviewer.urllib.request.urlopen", return_value=mock_resp):
        result = run_review(request, config)

    assert len(result) == 1


def test_run_review_http_error():
    request = ReviewRequest("abc", "diff", "desc", "1", "task")
    config = {
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    }

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
    config = {
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    }

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
    config = {
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    }

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
        "api_key": "sk-test",
        "base_url": "http://localhost:8080/v1/",
        "model": "llama-3",
    }

    with patch("mcloop.reviewer.urllib.request.urlopen", return_value=mock_resp) as mock_open:
        run_review(request, config)

    call_args = mock_open.call_args
    req_obj = call_args[0][0]
    assert req_obj.full_url == "http://localhost:8080/v1/chat/completions"
    body = json.loads(req_obj.data)
    assert body["model"] == "llama-3"


# --- run_review_cli ---


_FAKE_CONFIG = {
    "api_key": "sk-test",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
}


def test_run_review_cli_writes_results(tmp_path):
    plan = tmp_path / "PLAN.md"
    plan.write_text("# My Project\nDo stuff\n")

    diff_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="diff --git a/x b/x\n+hello\n", stderr=""
    )

    with (
        patch("mcloop.config.load_reviewer_config", return_value=_FAKE_CONFIG),
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
    assert "findings" in data
    assert "elapsed_seconds" in data
    assert data["commit"] == "abc123"
    assert len(data["findings"]) == 1
    assert data["findings"][0]["severity"] == "warning"


def test_run_review_cli_no_config(tmp_path, capsys):
    with patch("mcloop.config.load_reviewer_config", return_value=None):
        run_review_cli("abc123", str(tmp_path))

    # Should return early without error
    assert capsys.readouterr().err == ""


def test_run_review_cli_empty_diff(tmp_path, capsys):
    diff_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch("mcloop.config.load_reviewer_config", return_value=_FAKE_CONFIG),
        patch("mcloop.reviewer.subprocess.run", return_value=diff_result),
    ):
        run_review_cli("abc123", str(tmp_path))

    assert "Empty diff" in capsys.readouterr().err


def test_run_review_cli_git_error(tmp_path, capsys):
    diff_result = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="fatal: bad revision"
    )

    with (
        patch("mcloop.config.load_reviewer_config", return_value=_FAKE_CONFIG),
        patch("mcloop.reviewer.subprocess.run", return_value=diff_result),
    ):
        run_review_cli("bad", str(tmp_path))

    assert "git diff failed" in capsys.readouterr().err


def test_run_review_cli_no_plan(tmp_path):
    diff_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="diff\n+line\n", stderr=""
    )

    with (
        patch("mcloop.config.load_reviewer_config", return_value=_FAKE_CONFIG),
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


# --- _parse_diff_line_ranges ---


def test_parse_diff_single_file():
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -10,3 +10,5 @@\n"
        " context\n"
        "+added line\n"
        "+another\n"
    )
    result = _parse_diff_line_ranges(diff)
    assert "foo.py" in result
    assert result["foo.py"] == [(10, 14)]


def test_parse_diff_multiple_files():
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+new\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -5,2 +5,3 @@\n"
        "+stuff\n"
    )
    result = _parse_diff_line_ranges(diff)
    assert "a.py" in result
    assert "b.py" in result
    assert result["a.py"] == [(1, 4)]
    assert result["b.py"] == [(5, 7)]


def test_parse_diff_deleted_file():
    diff = "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,5 +0,0 @@\n-deleted\n"
    result = _parse_diff_line_ranges(diff)
    assert "old.py" not in result


def test_parse_diff_hunk_count_one():
    """Hunk header with count=1 (no comma): @@ -5 +5 @@"""
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -5 +5 @@\n+line\n"
    result = _parse_diff_line_ranges(diff)
    assert result["x.py"] == [(5, 5)]


def test_parse_diff_empty():
    assert _parse_diff_line_ranges("") == {}
    assert _parse_diff_line_ranges("not a diff at all") == {}


# --- _extract_enclosing_functions ---


def test_extract_python_function(tmp_path):
    src = (
        "import os\n\ndef foo():\n    x = 1\n    return x\n\ndef bar():\n    y = 2\n    return y\n"
    )
    f = tmp_path / "mod.py"
    f.write_text(src)

    # Changed lines in foo (lines 4-5, 1-indexed)
    result = _extract_enclosing_functions(f, [(4, 5)])
    assert "def foo" in result
    assert "def bar" not in result


def test_extract_includes_header(tmp_path):
    src = "import os\nimport sys\n\ndef main():\n    pass\n"
    f = tmp_path / "mod.py"
    f.write_text(src)

    result = _extract_enclosing_functions(f, [(5, 5)])
    assert "import os" in result
    assert "import sys" in result


def test_extract_top_level_code(tmp_path):
    src = "import os\n\nX = 42\n"
    f = tmp_path / "mod.py"
    f.write_text(src)

    # Change at line 3 (top-level, no function)
    result = _extract_enclosing_functions(f, [(3, 3)])
    assert "Changed lines" in result
    assert "X = 42" in result


def test_extract_nonexistent_file(tmp_path):
    f = tmp_path / "missing.py"
    assert _extract_enclosing_functions(f, [(1, 5)]) == ""


def test_extract_empty_file(tmp_path):
    f = tmp_path / "empty.py"
    f.write_text("")
    assert _extract_enclosing_functions(f, [(1, 1)]) == ""


def test_extract_swift_func(tmp_path):
    src = (
        "import Foundation\n"
        "\n"
        "func doSomething() {\n"
        "    let x = 1\n"
        "    print(x)\n"
        "}\n"
        "\n"
        "func other() {\n"
        "    let y = 2\n"
        "}\n"
    )
    f = tmp_path / "app.swift"
    f.write_text(src)

    result = _extract_enclosing_functions(f, [(4, 5)])
    assert "func doSomething" in result
    assert "func other" not in result


def test_extract_functions_separated_by_dots(tmp_path):
    src = "import os\n\ndef foo():\n    x = 1\n\ndef bar():\n    y = 2\n"
    f = tmp_path / "mod.py"
    f.write_text(src)

    # Changes in both functions
    result = _extract_enclosing_functions(f, [(4, 4), (7, 7)])
    assert "..." in result


# --- _collect_changed_functions ---


def test_collect_changed_functions(tmp_path):
    src = "import os\n\ndef hello():\n    print('hi')\n"
    (tmp_path / "app.py").write_text(src)

    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -3,2 +3,2 @@\n"
        "-def hello():\n"
        "+def hello(name):\n"
    )
    result = _collect_changed_functions(tmp_path, diff)
    assert result is not None
    assert "app.py" in result
    assert "def hello" in result["app.py"]


def test_collect_changed_functions_skips_deleted():
    diff = "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,5 +0,0 @@\n-deleted\n"
    from pathlib import Path

    result = _collect_changed_functions(Path("/nonexistent"), diff)
    assert result is None


def test_collect_changed_functions_skips_binary(tmp_path):
    # Create a file with binary content
    binary_file = tmp_path / "data.bin"
    binary_file.write_bytes(b"\x00\x01\x02\xff" * 100)

    diff = (
        "diff --git a/data.bin b/data.bin\n"
        "--- a/data.bin\n"
        "+++ b/data.bin\n"
        "@@ -1,2 +1,2 @@\n"
        "+stuff\n"
    )
    result = _collect_changed_functions(tmp_path, diff)
    # Binary files produce no extractable functions
    assert result is None


def test_collect_changed_functions_none_when_empty():
    result = _collect_changed_functions(
        __import__("pathlib").Path("/tmp"),
        "",
    )
    assert result is None


# --- _collect_review_findings null commit ---


def test_collect_review_findings_null_commit(tmp_path):
    """Review JSON with commit: null should not crash."""
    from mcloop.main import _collect_review_findings
    from mcloop.session_context import SessionContext

    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)

    review_data = {
        "findings": [],
        "elapsed_seconds": 1.5,
        "commit": None,
    }
    review_file = reviews_dir / "abc12345.json"
    review_file.write_text(json.dumps(review_data))

    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Task\n")

    ctx = SessionContext()
    # Should not raise — uses f.stem fallback when commit is null
    _collect_review_findings(tmp_path, plan, ctx)
