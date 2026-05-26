"""Tests for the cross-file ID resolver ``resolve_global`` (T-000004).

The resolver walks every ``PLAN.md`` under a workspace root and returns
``(file, task)`` for a fully-qualified ``T-XX-NNNNNN`` id, or raises
``TaskNotFoundError``. Inputs that are not in the namespaced form are
rejected with ``ValueError`` — legacy ``T-NNNNNN`` ids are intentionally
non-addressable across files because the namespace prefix is exactly
what disambiguates them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bob_tools.planfile import (
    PlanSyntaxError,
    TaskNotFoundError,
    resolve_global,
)


def _plan(namespace: str, *task_ids: str) -> str:
    body = "\n".join(f"- [ ] T-{namespace}-{tid}: task {tid}" for tid in task_ids)
    return (
        "<!-- bob-plan-format: 1 -->\n"
        "\n"
        f"<!-- task_namespace: {namespace} -->\n"
        "\n"
        f"# Plan {namespace}\n"
        "\n"
        f"## Stage 1: Bootstrap\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        f"{body}\n"
    )


def _write_plan(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


class TestResolveGlobal:
    def test_finds_task_in_single_plan(self, tmp_path: Path) -> None:
        path = _write_plan(tmp_path, "PLAN.md", _plan("AB", "000001", "000002"))
        found_path, task = resolve_global("T-AB-000002", tmp_path)
        assert found_path == path
        assert task.task_id == "T-AB-000002"
        assert task.text == "task 000002"

    def test_finds_task_in_nested_plan(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "PLAN.md", _plan("RT", "000001"))
        nested = _write_plan(tmp_path, "packages/foo/PLAN.md", _plan("FO", "000001"))
        found_path, task = resolve_global("T-FO-000001", tmp_path)
        assert found_path == nested
        assert task.task_id == "T-FO-000001"

    def test_finds_nested_child_task(self, tmp_path: Path) -> None:
        nested_plan = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "<!-- task_namespace: CD -->\n"
            "\n"
            "# Plan\n"
            "\n"
            "## Stage 1: Bootstrap\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-CD-000001: parent\n"
            "  - [ ] T-CD-000002: child\n"
        )
        path = _write_plan(tmp_path, "PLAN.md", nested_plan)
        found_path, task = resolve_global("T-CD-000002", tmp_path)
        assert found_path == path
        assert task.task_id == "T-CD-000002"
        assert task.text == "child"

    def test_finds_bug_section_task(self, tmp_path: Path) -> None:
        plan_text = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "<!-- task_namespace: BG -->\n"
            "\n"
            "# Plan\n"
            "\n"
            "## Stage 1: Bootstrap\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-BG-000001: phase task\n"
            "\n"
            "## Bugs\n"
            "\n"
            "- [ ] T-BG-000099: bug task\n"
        )
        path = _write_plan(tmp_path, "PLAN.md", plan_text)
        found_path, task = resolve_global("T-BG-000099", tmp_path)
        assert found_path == path
        assert task.text == "bug task"

    def test_raises_task_not_found_when_absent(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "PLAN.md", _plan("AB", "000001"))
        with pytest.raises(TaskNotFoundError) as exc_info:
            resolve_global("T-AB-000999", tmp_path)
        assert exc_info.value.task_id == "T-AB-000999"
        assert exc_info.value.root == tmp_path

    def test_raises_task_not_found_when_no_plan_files(self, tmp_path: Path) -> None:
        with pytest.raises(TaskNotFoundError):
            resolve_global("T-AB-000001", tmp_path)

    def test_rejects_legacy_unprefixed_id(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "PLAN.md", _plan("AB", "000001"))
        with pytest.raises(ValueError, match="fully qualified"):
            resolve_global("T-000001", tmp_path)

    def test_rejects_malformed_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            resolve_global("not-a-task-id", tmp_path)

    def test_rejects_wrong_namespace_length(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            resolve_global("T-A-000001", tmp_path)
        with pytest.raises(ValueError):
            resolve_global("T-ABC-000001", tmp_path)

    def test_rejects_non_canonical_digit_count(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            resolve_global("T-AB-1", tmp_path)

    def test_namespace_disambiguates_across_files(self, tmp_path: Path) -> None:
        # Two files with the same numeric suffix but different namespaces;
        # the resolver routes to the correct one by namespace.
        a_path = _write_plan(tmp_path, "a/PLAN.md", _plan("AA", "000001"))
        b_path = _write_plan(tmp_path, "b/PLAN.md", _plan("BB", "000001"))
        found_a, _ = resolve_global("T-AA-000001", tmp_path)
        found_b, _ = resolve_global("T-BB-000001", tmp_path)
        assert found_a == a_path
        assert found_b == b_path

    def test_walk_order_is_deterministic(self, tmp_path: Path) -> None:
        # Two files share a namespace + id (canonical input would never
        # contain this, but the resolver must still pick a deterministic
        # winner so callers see stable behavior). Sorted-path order means
        # ``a/`` wins over ``b/``.
        a_path = _write_plan(tmp_path, "a/PLAN.md", _plan("DP", "000001"))
        _write_plan(tmp_path, "b/PLAN.md", _plan("DP", "000001"))
        found_path, _ = resolve_global("T-DP-000001", tmp_path)
        assert found_path == a_path

    def test_propagates_parse_errors(self, tmp_path: Path) -> None:
        # A malformed PLAN.md in the walk is surfaced rather than
        # silently skipped, so callers know to fix the file. Duplicate
        # H1 is one of the structural anomalies that raise in compat
        # mode.
        _write_plan(tmp_path, "PLAN.md", "# Same\n## Stage 1: A\n- [ ] x\n# Same\n")
        with pytest.raises(PlanSyntaxError):
            resolve_global("T-AB-000001", tmp_path)
