"""Tests for ``_is_readonly_task`` and the no-op acceptance branch.

Background: the canonical synthesizer can author tasks that are
read-only by design — capture a baseline, verify a property,
record state — and those tasks legitimately produce zero file
changes when executed. Pre-fix, mcloop's no-op handling treated
"no file changes + post-task checks fail" as terminal failure,
which dead-locked Phase 0 plans where checks couldn't pass yet
because the surrounding project hadn't been built.

The fix is a text-classification heuristic: tasks whose text
contains a recognizable read-only phrase ("do not modify",
"capture baseline", etc.) are accepted as no-op success even
when checks fail. Tasks WITHOUT such language fall back to the
existing terminal-failure path.
"""

from __future__ import annotations

import pytest

from mcloop.main import (
    _READONLY_TASK_PHRASES,
    _is_readonly_task,
    _is_zero_diff_check_task,
)

# -- positive cases: deliberate read-only ----------------------------


@pytest.mark.parametrize(
    "text",
    [
        # The exact Phase 0 baseline-capture wording from the
        # smoke-fixture PLAN.md.
        "Capture pre-edit baseline by running ./run.sh --help and "
        "recording exit code, stdout, stderr; do not modify any "
        "files in this task",
        "Verify the entry point works; do not modify any files.",
        "Run smoke tests without modifying anything",
        "Read-only verification of the build artifact",
        "Read only check of the package layout",
        "Capture baseline of test failures before any edits",
        "Capture pre-edit state of pyproject.toml",
        "Record exit code from `./run.sh --help`",
        "Record stdout and stderr from the smoke test",
        "Verify and record the package version",
        "This task makes no file changes; just confirms the import works",
        "Inspect without making any changes to the source tree",
        "Do not edit pyproject.toml during this verification step",
        "Do not change any source files; only run diagnostics",
    ],
)
def test_recognizes_readonly_task_text(text: str) -> None:
    assert _is_readonly_task(text) is True


def test_recognizes_uppercase_phrase() -> None:
    """Match is case-insensitive — synthesizers may capitalize."""
    assert _is_readonly_task("DO NOT MODIFY any files in this task")


# -- negative cases: tasks that are supposed to change files ---------


@pytest.mark.parametrize(
    "text",
    [
        "Implement the watcher loop in fswatch_run_smoke/__main__.py",
        "Rename the [project.scripts] key from x to y in pyproject.toml",
        "Add tests/test_cli_smoke.py covering --help",
        "Replace the body of __main__.py with an argparse CLI",
        "Wire pytest-xdist into the test suite",
        "Refactor the queueing logic to handle in-flight runs",
        # Negation traps:
        "Modify pyproject.toml to declare watchdog as a dependency",
        "Edit __main__.py to add the --glob flag",
        "Change the import from ... to ...",
        # An empty/short text shouldn't match anything.
        "",
        "Add a feature",
    ],
)
def test_rejects_change_oriented_task_text(text: str) -> None:
    assert _is_readonly_task(text) is False


def test_phrase_set_is_non_empty() -> None:
    """Sanity: the phrase set itself is populated. Pin so a future
    edit can't silently empty it."""
    assert len(_READONLY_TASK_PHRASES) >= 8
    # Each phrase is non-empty and lowercase.
    for phrase in _READONLY_TASK_PHRASES:
        assert phrase
        assert phrase == phrase.lower()


@pytest.mark.parametrize(
    "text",
    [
        "Run the full suite and confirm the phase closes.",
        "Verify Stage 13 gate: ruff, mypy, pytest all green.",
        "Confirm all checks are green before release.",
        "Quality gate verification: pytest and mypy.",
    ],
)
def test_zero_diff_check_task_recognizes_verification_gates(text: str) -> None:
    assert _is_zero_diff_check_task(text)


@pytest.mark.parametrize(
    "text",
    [
        "Add tests for the renderer.",
        "Implement the verifier and run pytest.",
        "Already done task",
        "Capture baseline without modifying files.",
        "Refactor the queueing logic so checks pass.",
    ],
)
def test_zero_diff_check_task_rejects_implementation_or_readonly_tasks(text: str) -> None:
    assert not _is_zero_diff_check_task(text)
