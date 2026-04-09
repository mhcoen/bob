"""Tests for mcloop.claude_md_check."""

from pathlib import Path

from mcloop.claude_md_check import (
    _is_source_file,
    _is_test_file,
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
