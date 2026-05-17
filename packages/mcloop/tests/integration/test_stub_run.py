"""Integration test: run_loop with stub_cli as the CLI backend.

Uses the stub_cli.py script instead of mocking run_task, so the full
run_task → _build_command → _run_session pipeline executes against a
real subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.main import run_loop

STUB_CLI = str(Path(__file__).resolve().parent.parent / "stubs" / "stub_cli.py")


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, check=True)


def _setup_repo(
    tmp_path: Path,
    plan_content: str,
    scenario: dict,
    mcloop_json: dict | None = None,
) -> tuple[Path, Path]:
    """Create a temp git repo with PLAN.md and a scenario file.

    Returns (plan_md, scenario_path).
    """
    _git(["git", "init"], tmp_path)
    _git(["git", "config", "user.email", "test@mcloop.test"], tmp_path)
    _git(["git", "config", "user.name", "McLoop Test"], tmp_path)

    plan_md = tmp_path / "PLAN.md"
    plan_md.write_text(plan_content)

    # Trivial check command so run_checks always passes
    if mcloop_json is None:
        mcloop_json = {"checks": [f"{sys.executable} -c \"print('ok')\""]}
    (tmp_path / "mcloop.json").write_text(json.dumps(mcloop_json) + "\n")

    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario))

    _git(["git", "add", "."], tmp_path)
    _git(["git", "commit", "-m", "initial"], tmp_path)

    return plan_md, scenario_path


def _make_stub_build_command(scenario_path: Path):
    """Return a _build_command replacement that invokes stub_cli.py."""

    def build_command(
        cli: str,
        prompt: str | None = None,
        model: str | None = None,
        allowed_tools: str = "Edit,Write,Bash,Read,Glob,Grep",
    ) -> list[str]:
        cmd = [sys.executable, STUB_CLI, "--scenario", str(scenario_path)]
        if cli == "claude":
            cmd.append("-p")
            if prompt:
                cmd.append(prompt)
            cmd.extend(["--output-format", "stream-json"])
        elif cli == "codex":
            cmd.append("exec")
            if prompt:
                cmd.append(prompt)
        return cmd

    return build_command


# -------------------------------------------------------------------
# Test 1: single task completes, gets checked off, files committed
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_creates_file_and_checks_off_task(tmp_path):
    """run_loop with stub_cli: task creates a file, gets checked off, commit made."""
    scenario = {
        "tasks": [
            {
                "match": "Create hello.txt",
                "files": {"hello.txt": "hello from stub\n"},
                "output": "Created hello.txt",
                "exit_code": 0,
            }
        ],
        "default": {"output": "unknown task", "exit_code": 1},
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Create hello.txt\n",
        scenario,
    )

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            result = run_loop(plan_md, max_retries=1, no_audit=True)

    assert result.ok
    assert (tmp_path / "hello.txt").exists()
    assert (tmp_path / "hello.txt").read_text() == "hello from stub\n"

    content = plan_md.read_text()
    assert "- [x] Create hello.txt" in content

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "Create hello.txt" in log.stdout


# -------------------------------------------------------------------
# Test 2: task fails, retries up to max_retries, marked failed
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_failing_task_retried_and_marked_failed(tmp_path):
    """Stub returns exit_code=1 → task retried, then marked [!]."""
    scenario = {
        "tasks": [
            {
                "match": "Impossible",
                "output": "error: cannot do this",
                "exit_code": 1,
            }
        ],
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Impossible task\n",
        scenario,
    )

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            result = run_loop(plan_md, max_retries=2, no_audit=True)

    assert result.ok
    content = plan_md.read_text()
    assert "- [!] Impossible task" in content


# -------------------------------------------------------------------
# Test 3: check command fails, task retries with check output as prior_errors
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_check_failure_retries_with_prior_errors(tmp_path):
    """When checks fail, task is retried and the check output becomes prior_errors."""
    # Use a counter file to make checks pass on the second attempt.
    # The check script fails if counter.txt contains "0", passes otherwise.
    counter_file = tmp_path / "counter.txt"

    check_script = tmp_path / "check.py"
    check_script.write_text(
        f"import sys\n"
        f"from pathlib import Path\n"
        f'c = Path("{counter_file}")\n'
        f"n = int(c.read_text()) if c.exists() else 0\n"
        f"c.write_text(str(n + 1))\n"
        f"if n == 0:\n"
        f'    print("LINT ERROR: undefined variable foo")\n'
        f"    sys.exit(1)\n"
        f'print("ok")\n'
    )

    scenario = {
        "tasks": [
            {
                "match": "Create widget",
                "files": {"widget.py": "def widget(): pass\n"},
                "output": "Created widget.py",
                "exit_code": 0,
            }
        ],
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Create widget\n",
        scenario,
        mcloop_json={"checks": [f"{sys.executable} {check_script}"]},
    )

    prompts_seen = []
    original_build = _make_stub_build_command(scenario_path)

    def tracking_build(cli, prompt=None, model=None, allowed_tools=None):
        if prompt:
            prompts_seen.append(prompt)
        kw = {}
        if allowed_tools is not None:
            kw["allowed_tools"] = allowed_tools
        return original_build(cli, prompt=prompt, model=model, **kw)

    with patch("mcloop.runner._build_command", tracking_build):
        with patch("mcloop.main.notify"):
            result = run_loop(plan_md, max_retries=3, no_audit=True)

    assert result.ok
    content = plan_md.read_text()
    assert "- [x] Create widget" in content

    # The second prompt should contain the check error from the first attempt
    assert len(prompts_seen) >= 2
    assert "LINT ERROR" in prompts_seen[1]


# -------------------------------------------------------------------
# Test 4: rate limit output triggers pause and retry
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_rate_limit_triggers_pause_and_retry(tmp_path):
    """Rate limit on first attempt triggers wait_for_reset, then retries successfully."""
    call_count = 0

    scenario = {
        "tasks": [
            {
                "match": "Create output",
                "files": {"output.txt": "done\n"},
                "output": "Created output.txt",
                "exit_code": 0,
            }
        ],
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Create output file\n",
        scenario,
    )

    def fake_is_rate_limited(output, exit_code):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True
        return False

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            with patch(
                "mcloop.main.is_rate_limited",
                side_effect=fake_is_rate_limited,
            ):
                with patch(
                    "mcloop.main.wait_for_reset",
                    return_value="claude",
                ):
                    result = run_loop(plan_md, max_retries=2, no_audit=True)

    assert result.ok
    content = plan_md.read_text()
    assert "- [x] Create output file" in content
    # Rate limit attempt should not count, so we should have seen at least 2 calls
    assert call_count >= 2


# -------------------------------------------------------------------
# Test 5: session limit output triggers polling
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_session_limit_triggers_polling(tmp_path):
    """Session limit on first attempt triggers sleep poll, then retries successfully."""
    call_count = 0

    scenario = {
        "tasks": [
            {
                "match": "Create result",
                "files": {"result.txt": "done\n"},
                "output": "Created result.txt",
                "exit_code": 0,
            }
        ],
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Create result file\n",
        scenario,
    )

    def fake_is_session_limited(output, exit_code):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True
        return False

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            with patch(
                "mcloop.main.is_session_limited",
                side_effect=fake_is_session_limited,
            ):
                with patch("mcloop.main.SESSION_LIMIT_POLL", 0):
                    result = run_loop(plan_md, max_retries=2, no_audit=True)

    assert result.ok
    content = plan_md.read_text()
    assert "- [x] Create result file" in content
    assert call_count >= 2


# -------------------------------------------------------------------
# Test 6: [BATCH] parent with multiple children runs one session,
#          all children checked off
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_batch_runs_one_session_checks_off_all(tmp_path):
    """[BATCH] parent: children combined into single session, all checked off."""
    scenario = {
        "tasks": [
            {
                "match": "Do all of the following",
                "files": {
                    "part_a.txt": "a\n",
                    "part_b.txt": "b\n",
                    "part_c.txt": "c\n",
                },
                "output": "Completed all parts",
                "exit_code": 0,
            }
        ],
        "default": {"output": "unexpected", "exit_code": 1},
    }

    plan_content = (
        "# Test project\n\n"
        "- [ ] [BATCH] Build all parts\n"
        "  - [ ] Create part_a.txt\n"
        "  - [ ] Create part_b.txt\n"
        "  - [ ] Create part_c.txt\n"
    )
    plan_md, scenario_path = _setup_repo(tmp_path, plan_content, scenario)

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            result = run_loop(plan_md, max_retries=1, no_audit=True)

    assert result.ok
    assert (tmp_path / "part_a.txt").read_text() == "a\n"
    assert (tmp_path / "part_b.txt").read_text() == "b\n"
    assert (tmp_path / "part_c.txt").read_text() == "c\n"

    content = plan_md.read_text()
    assert "- [x] Create part_a.txt" in content
    assert "- [x] Create part_b.txt" in content
    assert "- [x] Create part_c.txt" in content

    # Should have made exactly one commit for the batch (plus initial)
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    lines = [line for line in log.stdout.strip().splitlines() if line]
    # Expect: initial commit + 1 batch commit
    assert len(lines) == 2
    assert "BATCH" in lines[0]


# -------------------------------------------------------------------
# Test 7: batch failure falls back to individual execution
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_batch_failure_falls_back_to_individual(tmp_path):
    """When batch session fails, tasks are retried individually."""
    # The batch prompt ("Do all of the following") will match the
    # "batch_fail" task and exit 1. Individual tasks match their own
    # patterns and succeed.
    # Patterns are checked in order; put more specific ones first
    # to avoid cross-matching via session context.
    # "Write beta" must precede "Write alpha" because session context
    # from task 1.1 bleeds "Write alpha" into task 1.2's prompt.
    scenario = {
        "tasks": [
            {
                "match": "Do all of the following",
                "output": "error: batch failed",
                "exit_code": 1,
            },
            {
                "match": "Write beta",
                "files": {"beta.txt": "b\n"},
                "output": "done beta",
                "exit_code": 0,
            },
            {
                "match": "Write alpha",
                "files": {"alpha.txt": "a\n"},
                "output": "done alpha",
                "exit_code": 0,
            },
        ],
        "default": {"output": "unknown", "exit_code": 1},
    }

    plan_content = (
        "# Test project\n\n"
        "- [ ] [BATCH] Build things\n"
        "  - [ ] Write alpha component\n"
        "  - [ ] Write beta component\n"
    )
    plan_md, scenario_path = _setup_repo(tmp_path, plan_content, scenario)

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            result = run_loop(plan_md, max_retries=1, no_audit=True)

    assert result.ok
    assert (tmp_path / "alpha.txt").read_text() == "a\n"
    assert (tmp_path / "beta.txt").read_text() == "b\n"

    content = plan_md.read_text()
    assert "- [x] Write alpha component" in content
    assert "- [x] Write beta component" in content


# -------------------------------------------------------------------
# Test 8: explicit read-only task (no file changes) gets checked off
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_no_file_changes_checks_pass_auto_checked(tmp_path):
    """Stub exits 0 but creates no files for an explicit read-only task."""
    scenario = {
        "tasks": [
            {
                "match": "Verify",
                "output": "looks good",
                "exit_code": 0,
            }
        ],
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Verify everything works; do not modify any files\n",
        scenario,
    )

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            result = run_loop(plan_md, max_retries=1, no_audit=True)

    assert result.ok
    content = plan_md.read_text()
    assert "- [x] Verify everything works" in content


# -------------------------------------------------------------------
# Test 9: reviewer spawned after successful commit when config present
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_reviewer_spawned_after_commit(tmp_path):
    """When reviewer config is present, _spawn_reviewer is called after commit."""
    scenario = {
        "tasks": [
            {
                "match": "Add feature",
                "files": {"feature.py": "def feature(): pass\n"},
                "output": "Added feature",
                "exit_code": 0,
            }
        ],
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Add feature\n",
        scenario,
    )

    reviewer_calls = []

    def fake_spawn_reviewer(project_dir):
        reviewer_calls.append(str(project_dir))

    fake_config = {"model": "test-model", "base_url": "http://localhost", "api_key": "test"}

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            with patch(
                "mcloop.main.load_reviewer_config",
                return_value=fake_config,
            ):
                with patch(
                    "mcloop.main.format_reviewer_status",
                    return_value="test-model (test)",
                ):
                    with patch(
                        "mcloop.main._spawn_reviewer",
                        side_effect=fake_spawn_reviewer,
                    ):
                        result = run_loop(
                            plan_md,
                            max_retries=1,
                            no_audit=True,
                        )

    assert result.ok
    content = plan_md.read_text()
    assert "- [x] Add feature" in content

    # Reviewer should have been called exactly once after the commit
    assert len(reviewer_calls) == 1
    assert reviewer_calls[0] == str(tmp_path)


# -------------------------------------------------------------------
# Test 10: stage boundary triggers full test suite and stops
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_stage_boundary_full_suite(tmp_path, capsys):
    """Stage boundary: run_loop completes stage 1, runs full suite, stops before stage 2."""
    scenario = {
        "tasks": [
            {
                "match": "Create stage1",
                "files": {"stage1.txt": "stage 1 done\n"},
                "output": "Created stage1.txt",
                "exit_code": 0,
            },
        ],
        "default": {"output": "unknown", "exit_code": 1},
    }

    plan_content = (
        "# Test project\n\n"
        "## Stage 1: Foundation\n"
        "- [ ] Create stage1 file\n"
        "\n"
        "## Stage 2: Polish\n"
        "- [ ] Create stage2 file\n"
    )
    plan_md, scenario_path = _setup_repo(tmp_path, plan_content, scenario)

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            result = run_loop(plan_md, max_retries=1, no_audit=True)

    assert result.ok

    content = plan_md.read_text()
    # Stage 1 task checked off
    assert "- [x] Create stage1 file" in content
    # Stage 2 task NOT attempted
    assert "- [ ] Create stage2 file" in content

    assert (tmp_path / "stage1.txt").exists()
    assert not (tmp_path / "stage2.txt").exists()

    # Full test suite ran at stage boundary
    captured = capsys.readouterr()
    assert "full test suite" in captured.out.lower()
    assert "stage 1: foundation complete" in captured.out.lower()


# -------------------------------------------------------------------
# Test 11: check command always fails → task retries and marked [!]
# -------------------------------------------------------------------


@pytest.mark.integration
def test_stub_check_always_fails_retries_and_marked_failed(tmp_path):
    """Stub task succeeds but check command always fails → retries, marked [!]."""
    scenario = {
        "tasks": [
            {
                "match": "Create hello",
                "files": {"hello.txt": "hello\n"},
                "output": "Created hello.txt",
                "exit_code": 0,
            }
        ],
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Create hello.txt\n",
        scenario,
        mcloop_json={"checks": ["test -f goodbye.txt"]},
    )

    prompts_seen = []
    original_build = _make_stub_build_command(scenario_path)

    def tracking_build(cli, prompt=None, model=None, allowed_tools=None):
        if prompt:
            prompts_seen.append(prompt)
        kw = {}
        if allowed_tools is not None:
            kw["allowed_tools"] = allowed_tools
        return original_build(cli, prompt=prompt, model=model, **kw)

    with patch("mcloop.runner._build_command", tracking_build):
        with patch("mcloop.main.notify"):
            result = run_loop(plan_md, max_retries=2, no_audit=True)

    # Task failed (exhausted retries)
    assert result.ok

    # hello.txt was created (task succeeds) but goodbye.txt never exists
    assert (tmp_path / "hello.txt").exists()
    assert not (tmp_path / "goodbye.txt").exists()

    # Task marked failed
    content = plan_md.read_text()
    assert "- [!] Create hello.txt" in content
    assert "- [x]" not in content

    # No commits beyond initial (checks never passed)
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    lines = [line for line in log.stdout.strip().splitlines() if line]
    assert len(lines) == 1

    # Retry prompts should contain the check failure output
    assert len(prompts_seen) >= 2
    assert "test -f goodbye.txt" in prompts_seen[1]
