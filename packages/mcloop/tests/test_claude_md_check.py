"""Tests for mcloop.claude_md_check."""

from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.claude_md_check import (
    SyncResult,
    _is_source_file,
    _is_test_file,
    _parse_llm_response,
    auto_update_claude_md,
    check_claude_md_freshness,
)


class TestIsTestFile:
    def test_python_test(self):
        assert _is_test_file("tests/test_foo.py") is True

    def test_go_test(self):
        assert _is_test_file("pkg/bar_test.go") is True

    def test_regular_python(self):
        assert _is_test_file("mcloop/main.py") is False

    def test_test_prefix_non_python(self):
        assert _is_test_file("test_foo.js") is False

    def test_go_non_test(self):
        assert _is_test_file("pkg/bar.go") is False


class TestIsSourceFile:
    def test_python_source(self):
        assert _is_source_file("mcloop/main.py") is True

    def test_swift_source(self):
        assert _is_source_file("Sources/App.swift") is True

    def test_rust_source(self):
        assert _is_source_file("src/main.rs") is True

    def test_go_source(self):
        assert _is_source_file("cmd/server.go") is True

    def test_js_source(self):
        assert _is_source_file("index.js") is True

    def test_ts_source(self):
        assert _is_source_file("app.ts") is True

    def test_java_source(self):
        assert _is_source_file("com/example/Main.java") is True

    def test_c_source(self):
        assert _is_source_file("main.c") is True

    def test_cpp_source(self):
        assert _is_source_file("main.cpp") is True

    def test_ruby_source(self):
        assert _is_source_file("lib/app.rb") is True

    def test_shell_source(self):
        assert _is_source_file("scripts/deploy.sh") is True

    def test_src_dir(self):
        assert _is_source_file("src/main.py") is True

    def test_src_dir_non_code(self):
        assert _is_source_file("src/config.toml") is False

    def test_lib_dir(self):
        assert _is_source_file("lib/app.js") is True

    def test_lib_dir_non_code(self):
        assert _is_source_file("lib/utils.txt") is False

    def test_package_dir(self):
        assert _is_source_file("package/main.ts") is True

    def test_package_dir_non_code(self):
        assert _is_source_file("package/index.html") is False

    def test_markdown(self):
        assert _is_source_file("README.md") is False

    def test_json(self):
        assert _is_source_file("package.json") is False

    def test_test_file_excluded(self):
        assert _is_source_file("tests/test_main.py") is False

    def test_go_test_excluded(self):
        assert _is_source_file("pkg/handler_test.go") is False


class TestCheckClaudeMdFreshness:
    project = Path("/tmp/proj")

    def test_no_files(self):
        assert check_claude_md_freshness([], self.project) is True

    def test_no_source_files(self):
        assert check_claude_md_freshness(["README.md", "pyproject.toml"], self.project) is True

    def test_source_without_claude_md(self):
        assert check_claude_md_freshness(["mcloop/main.py"], self.project) is False

    def test_source_with_claude_md(self):
        assert check_claude_md_freshness(["mcloop/main.py", "CLAUDE.md"], self.project) is True

    def test_only_claude_md(self):
        assert check_claude_md_freshness(["CLAUDE.md"], self.project) is True

    def test_multiple_sources_without_claude_md(self):
        assert (
            check_claude_md_freshness(
                ["mcloop/main.py", "mcloop/runner.py", "index.js"],
                self.project,
            )
            is False
        )

    def test_multiple_sources_with_claude_md(self):
        assert (
            check_claude_md_freshness(
                ["mcloop/main.py", "mcloop/runner.py", "CLAUDE.md"],
                self.project,
            )
            is True
        )

    def test_test_files_only(self):
        assert (
            check_claude_md_freshness(
                ["tests/test_main.py", "pkg/handler_test.go"],
                self.project,
            )
            is True
        )

    def test_test_and_source_without_claude_md(self):
        assert (
            check_claude_md_freshness(
                ["tests/test_main.py", "mcloop/main.py"],
                self.project,
            )
            is False
        )

    def test_src_dir_triggers(self):
        assert check_claude_md_freshness(["src/main.py"], self.project) is False

    def test_src_dir_non_code_ignored(self):
        assert check_claude_md_freshness(["src/config.yaml"], self.project) is True

    def test_lib_dir_triggers(self):
        assert check_claude_md_freshness(["lib/helper.rb"], self.project) is False

    def test_lib_dir_non_code_ignored(self):
        assert check_claude_md_freshness(["lib/helper.txt"], self.project) is True

    def test_package_dir_triggers(self):
        assert check_claude_md_freshness(["package/index.ts"], self.project) is False

    def test_package_dir_non_code_ignored(self):
        assert check_claude_md_freshness(["package/data.json"], self.project) is True

    def test_nested_claude_md_not_accepted(self):
        # Only root CLAUDE.md counts, not docs/CLAUDE.md or subdir/CLAUDE.md
        assert (
            check_claude_md_freshness(["mcloop/main.py", "docs/CLAUDE.md"], self.project) is False
        )

    def test_subdir_claude_md_not_accepted(self):
        assert (
            check_claude_md_freshness(["mcloop/main.py", "subdir/CLAUDE.md"], self.project)
            is False
        )

    def test_docs_claude_md_fails_but_root_claude_md_passes(self):
        # docs/CLAUDE.md does NOT satisfy the freshness gate
        assert (
            check_claude_md_freshness(["mcloop/main.py", "docs/CLAUDE.md"], self.project) is False
        )
        # repo-root CLAUDE.md DOES satisfy the freshness gate
        assert check_claude_md_freshness(["mcloop/main.py", "CLAUDE.md"], self.project) is True
        # Both present: root CLAUDE.md is sufficient
        assert (
            check_claude_md_freshness(
                ["mcloop/main.py", "docs/CLAUDE.md", "CLAUDE.md"], self.project
            )
            is True
        )

    def test_non_source_mixed(self):
        assert (
            check_claude_md_freshness(
                ["README.md", "pyproject.toml", ".gitignore"],
                self.project,
            )
            is True
        )


class TestAutoUpdateClaudeMdTypeError:
    """TypeError is caught when API response contains None in the chain."""

    def test_none_message_returns_transient_failed(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n")

        with (
            patch(
                "mcloop.claude_md_check._load_update_config",
                return_value={
                    "base_url": "https://api.example.com/v1",
                    "model": "test-model",
                    "api_key": "sk-test",
                },
            ),
            patch("mcloop.claude_md_check._get_diff_text", return_value="some diff"),
            patch("mcloop.claude_md_check._call_deepseek", return_value=None),
            patch("mcloop.claude_md_check._call_sonnet_fallback", return_value=None),
            patch("mcloop.claude_md_check._DEEPSEEK_RETRY_SLEEP", 0),
        ):
            assert auto_update_claude_md(tmp_path) is SyncResult.TRANSIENT_FAILED


def test_auto_update_both_providers_fail_returns_transient(tmp_path):
    """When both DeepSeek and Sonnet fail, returns TRANSIENT_FAILED."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Project\n")

    with (
        patch(
            "mcloop.claude_md_check._load_update_config",
            return_value={
                "base_url": "https://api.example.com/v1",
                "model": "test-model",
                "api_key": "sk-test",
            },
        ),
        patch("mcloop.claude_md_check._get_diff_text", return_value="some diff"),
        patch("mcloop.claude_md_check._call_deepseek", return_value=None),
        patch("mcloop.claude_md_check._call_sonnet_fallback", return_value=None),
        patch("mcloop.claude_md_check._DEEPSEEK_RETRY_SLEEP", 0),
    ):
        assert auto_update_claude_md(tmp_path) is SyncResult.TRANSIENT_FAILED


def test_auto_update_appends_summary(tmp_path):
    """On success, the summary is appended to CLAUDE.md (not rewritten)."""
    claude_md = tmp_path / "CLAUDE.md"
    original = "# Project\n\nExisting content.\n"
    claude_md.write_text(original)

    with (
        patch(
            "mcloop.claude_md_check._load_update_config",
            return_value={
                "base_url": "https://api.example.com/v1",
                "model": "test-model",
                "api_key": "sk-test",
            },
        ),
        patch("mcloop.claude_md_check._get_diff_text", return_value="some diff"),
        patch(
            "mcloop.claude_md_check._call_deepseek",
            return_value="Fixed a bug in the parser.",
        ),
    ):
        result = auto_update_claude_md(tmp_path, commit_sha="abc1234def")

    assert result is SyncResult.OK
    content = claude_md.read_text()
    # Original content preserved
    assert content.startswith("# Project\n\nExisting content.\n")
    # Summary appended with short SHA
    assert "abc1234: Fixed a bug in the parser." in content


class TestParseLlmResponse:
    """Pure-function tests for _parse_llm_response."""

    @pytest.mark.parametrize(
        "body",
        [
            {"choices": []},
            {"choices": "not a list"},
            {"choices": [None]},
            {"choices": [{"message": {"content": 42}}]},
            {"choices": [[]]},
            {"no_choices": True},
            "not a dict",
            {"choices": [{"message": 123}]},
            {"choices": None},
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"no_message": True}]},
            {"choices": [{"message": {"no_content": True}}]},
            None,
            42,
            [1, 2, 3],
            {"choices": {"key": "val"}},
        ],
        ids=[
            "empty_choices",
            "choices_not_list",
            "first_choice_none",
            "content_not_string",
            "first_choice_not_dict",
            "missing_choices_key",
            "body_not_dict",
            "message_not_dict",
            "choices_null",
            "content_null",
            "missing_message_key",
            "missing_content_key",
            "body_null",
            "body_integer",
            "body_list",
            "choices_dict",
        ],
    )
    def test_malformed_response_returns_none(self, body):
        assert _parse_llm_response(body) is None

    def test_valid_response_returns_content(self):
        body = {"choices": [{"message": {"content": "x" * 200}}]}
        result = _parse_llm_response(body)
        assert result == "x" * 200

    def test_short_content_returns_content(self):
        body = {"choices": [{"message": {"content": "short"}}]}
        assert _parse_llm_response(body) == "short"

    def test_strips_markdown_fences(self):
        body = {"choices": [{"message": {"content": "```markdown\nSummary of changes\n```"}}]}
        result = _parse_llm_response(body)
        assert result == "Summary of changes"

    def test_empty_content_returns_none(self):
        body = {"choices": [{"message": {"content": ""}}]}
        assert _parse_llm_response(body) is None

    def test_whitespace_content_returns_none(self):
        body = {"choices": [{"message": {"content": "   \n  "}}]}
        assert _parse_llm_response(body) is None
