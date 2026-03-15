"""Tests for stub_cli.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.stubs.stub_cli import (
    _apply_files,
    _find_scenario,
    main,
)


class TestFindScenario:
    def test_matches_first_pattern(self):
        tasks = [
            {"match": "build.*app", "exit_code": 0},
            {"match": "test.*app", "exit_code": 1},
        ]
        result = _find_scenario(tasks, "build the app now")
        assert result is not None
        assert result["exit_code"] == 0

    def test_returns_none_when_no_match(self):
        tasks = [{"match": "xyz123", "exit_code": 0}]
        assert _find_scenario(tasks, "unrelated prompt") is None

    def test_case_insensitive(self):
        tasks = [{"match": "hello", "exit_code": 0}]
        assert _find_scenario(tasks, "HELLO world") is not None

    def test_empty_tasks(self):
        assert _find_scenario([], "anything") is None

    def test_regex_pattern(self):
        tasks = [{"match": r"fix\s+bug\s+#\d+", "exit_code": 0}]
        assert _find_scenario(tasks, "fix bug #42") is not None

    def test_first_match_wins(self):
        tasks = [
            {"match": "hello", "exit_code": 0},
            {"match": "hello", "exit_code": 1},
        ]
        result = _find_scenario(tasks, "hello")
        assert result is not None
        assert result["exit_code"] == 0


class TestApplyFiles:
    def test_create_file(self, tmp_path):
        os.chdir(tmp_path)
        _apply_files({"newfile.txt": "hello world"})
        assert (tmp_path / "newfile.txt").read_text() == "hello world"

    def test_create_nested(self, tmp_path):
        os.chdir(tmp_path)
        _apply_files({"sub/dir/file.py": "print('hi')"})
        assert (tmp_path / "sub" / "dir" / "file.py").exists()

    def test_overwrite_file(self, tmp_path):
        os.chdir(tmp_path)
        (tmp_path / "existing.txt").write_text("old")
        _apply_files({"existing.txt": "new"})
        assert (tmp_path / "existing.txt").read_text() == "new"

    def test_patch_append(self, tmp_path):
        os.chdir(tmp_path)
        (tmp_path / "file.txt").write_text("line1\n")
        _apply_files({"file.txt": {"patch": "line2\n"}})
        assert (tmp_path / "file.txt").read_text() == "line1\nline2\n"

    def test_patch_new_file(self, tmp_path):
        os.chdir(tmp_path)
        _apply_files({"new.txt": {"patch": "content"}})
        assert (tmp_path / "new.txt").read_text() == "content"


class TestMain:
    def _write_scenario(self, tmp_path, scenario):
        p = tmp_path / "scenario.json"
        p.write_text(json.dumps(scenario))
        return str(p)

    def test_matching_task(self, tmp_path, capsys):
        os.chdir(tmp_path)
        scenario_file = self._write_scenario(
            tmp_path,
            {
                "tasks": [
                    {
                        "match": "greet",
                        "output": "Hello!",
                        "exit_code": 0,
                    }
                ]
            },
        )
        code = main(["--scenario", scenario_file, "-p", "greet the user"])
        assert code == 0
        assert "Hello!" in capsys.readouterr().out

    def test_default_when_no_match(self, tmp_path, capsys):
        os.chdir(tmp_path)
        scenario_file = self._write_scenario(
            tmp_path,
            {
                "tasks": [{"match": "xyz", "output": "matched"}],
                "default": {
                    "output": "fallback",
                    "exit_code": 3,
                },
            },
        )
        code = main(["--scenario", scenario_file, "-p", "unrelated"])
        assert code == 3
        assert "fallback" in capsys.readouterr().out

    def test_no_default_no_match(self, tmp_path):
        os.chdir(tmp_path)
        scenario_file = self._write_scenario(
            tmp_path,
            {"tasks": [{"match": "xyz", "exit_code": 0}]},
        )
        code = main(["--scenario", scenario_file, "-p", "unrelated"])
        assert code == 0  # empty default has exit_code 0

    def test_file_creation(self, tmp_path, capsys):
        os.chdir(tmp_path)
        scenario_file = self._write_scenario(
            tmp_path,
            {
                "tasks": [
                    {
                        "match": "create",
                        "files": {str(tmp_path / "out.txt"): "created"},
                        "output": "done",
                        "exit_code": 0,
                    }
                ]
            },
        )
        code = main(["--scenario", scenario_file, "-p", "create file"])
        assert code == 0
        assert (tmp_path / "out.txt").read_text() == "created"

    def test_delay(self, tmp_path):
        import time

        os.chdir(tmp_path)
        scenario_file = self._write_scenario(
            tmp_path,
            {
                "tasks": [
                    {
                        "match": "wait",
                        "delay": 0.1,
                        "exit_code": 0,
                    }
                ]
            },
        )
        start = time.monotonic()
        main(["--scenario", scenario_file, "-p", "wait"])
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1

    def test_stream_json_output(self, tmp_path, capsys):
        os.chdir(tmp_path)
        scenario_file = self._write_scenario(
            tmp_path,
            {
                "tasks": [
                    {
                        "match": "stream",
                        "output": "line1\nline2",
                        "exit_code": 0,
                    }
                ]
            },
        )
        code = main(
            [
                "--scenario",
                scenario_file,
                "-p",
                "stream it",
                "--output-format",
                "stream-json",
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert len(lines) == 2
        parsed = json.loads(lines[0])
        assert parsed["type"] == "assistant"
        assert parsed["text"] == "line1"

    def test_missing_scenario_arg(self, capsys):
        code = main(["-p", "hello"])
        assert code == 2
        assert "scenario" in capsys.readouterr().err.lower()

    def test_missing_prompt_arg(self, tmp_path, capsys):
        sf = self._write_scenario(tmp_path, {"tasks": []})
        code = main(["--scenario", sf])
        assert code == 2
        assert "prompt" in capsys.readouterr().err.lower()

    def test_bad_scenario_file(self, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("not json{{{")
        code = main(["--scenario", str(bad), "-p", "hello"])
        assert code == 2

    def test_missing_scenario_file(self, capsys):
        code = main(["--scenario", "/nonexistent.json", "-p", "hello"])
        assert code == 2

    def test_multi_behavior_scenario(self, tmp_path, capsys):
        """Scenario with multiple behaviors keyed by prompt substring."""
        os.chdir(tmp_path)
        scenario_file = self._write_scenario(
            tmp_path,
            {
                "tasks": [
                    {
                        "match": "create hello\\.txt",
                        "files": {"hello.txt": "hello"},
                        "output": "Created hello.txt",
                        "exit_code": 0,
                    },
                    {
                        "match": "rate limit",
                        "output": "Rate limit exceeded. Try again later.",
                        "exit_code": 1,
                    },
                    {
                        "match": "delete everything",
                        "output": "Deleted all files",
                        "exit_code": 0,
                    },
                ],
                "default": {
                    "output": "Unknown task",
                    "exit_code": 1,
                },
            },
        )
        # First behavior: create hello.txt
        code = main(["--scenario", scenario_file, "-p", "create hello.txt"])
        assert code == 0
        assert (tmp_path / "hello.txt").read_text() == "hello"
        assert "Created hello.txt" in capsys.readouterr().out

        # Second behavior: rate limit
        code = main(["--scenario", scenario_file, "-p", "got a rate limit error"])
        assert code == 1
        out = capsys.readouterr().out
        assert "Rate limit" in out

        # Third behavior: different task
        code = main(["--scenario", scenario_file, "-p", "delete everything now"])
        assert code == 0

        # Default: no match
        code = main(["--scenario", scenario_file, "-p", "something else entirely"])
        assert code == 1
        assert "Unknown task" in capsys.readouterr().out

    def test_multiple_files_per_task(self, tmp_path):
        """A single task can create multiple files."""
        os.chdir(tmp_path)
        scenario_file = self._write_scenario(
            tmp_path,
            {
                "tasks": [
                    {
                        "match": "scaffold",
                        "files": {
                            str(tmp_path / "src" / "main.py"): "# main",
                            str(tmp_path / "tests" / "test_main.py"): "# test",
                            str(tmp_path / "README.md"): "# Project",
                        },
                        "exit_code": 0,
                    }
                ]
            },
        )
        code = main(["--scenario", scenario_file, "-p", "scaffold project"])
        assert code == 0
        assert (tmp_path / "src" / "main.py").read_text() == "# main"
        assert (tmp_path / "tests" / "test_main.py").read_text() == "# test"
        assert (tmp_path / "README.md").read_text() == "# Project"

    def test_mixed_create_and_patch(self, tmp_path):
        """A task can create new files and patch existing ones."""
        os.chdir(tmp_path)
        (tmp_path / "existing.py").write_text("line1\n")
        scenario_file = self._write_scenario(
            tmp_path,
            {
                "tasks": [
                    {
                        "match": "update",
                        "files": {
                            str(tmp_path / "new.py"): "brand new",
                            str(tmp_path / "existing.py"): {"patch": "line2\n"},
                        },
                        "exit_code": 0,
                    }
                ]
            },
        )
        code = main(["--scenario", scenario_file, "-p", "update project"])
        assert code == 0
        assert (tmp_path / "new.py").read_text() == "brand new"
        assert (tmp_path / "existing.py").read_text() == "line1\nline2\n"

    def test_runnable_as_script(self, tmp_path):
        """Verify stub_cli.py works as a subprocess."""
        scenario_file = self._write_scenario(
            tmp_path,
            {
                "tasks": [
                    {
                        "match": "test",
                        "output": "subprocess ok",
                        "exit_code": 0,
                    }
                ]
            },
        )
        stub_path = Path(__file__).parent / "stub_cli.py"
        result = subprocess.run(
            [
                sys.executable,
                str(stub_path),
                "--scenario",
                scenario_file,
                "-p",
                "test prompt",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "subprocess ok" in result.stdout
