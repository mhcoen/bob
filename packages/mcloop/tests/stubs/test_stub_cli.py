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
