"""Startup-preflight class guard: BUGS.md is a loose queue, never canonical.

``_enforce_canonical_inputs`` must canonical-preflight PLAN.md ONLY.
Routing BUGS.md through ``preflight_runtime_plan`` both crashed on
legitimate id-less entries (a stray magic line force-enables strict
parsing) and — worse — re-stamped the magic line and T-ids via the
migration path on every startup, oscillating against the ``magic=False``
write path that deliberately drops them. These tests pin both halves:
the crash is gone AND the re-stamp is gone, while genuine structural
corruption detection and PLAN.md's full canonical preflight survive.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bob_tools.planfile.model import PlanSyntaxError
from plan_fixtures import canonical_plan_text

from mcloop import _planfile_compat as shim
from mcloop.main import _enforce_canonical_inputs

# Mirrors the real bug-filer output that crashed the startup preflight:
# stray magic line + id-less entry + fenced observation block.
_MAGIC_IDLESS_BUGS = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "## Bugs\n"
    "- [ ] Fix issue reported during task 11.8 (see observation below):\n"
    "```\n"
    "extract a corpus of 1 file containing two sentences of 10 and 20 words\n"
    "```\n"
)

_CANONICAL_PLAN = canonical_plan_text("# Demo\n\n## Stage 1: Core\n- [ ] First task\n")


def _write_pair(tmp_path: Path, bugs_text: str = _MAGIC_IDLESS_BUGS) -> tuple[Path, Path]:
    plan_path = tmp_path / "PLAN.md"
    plan_path.write_text(_CANONICAL_PLAN)
    bugs_path = tmp_path / "BUGS.md"
    bugs_path.write_text(bugs_text)
    return plan_path, bugs_path


def test_enforce_tolerates_magic_idless_bugs(tmp_path: Path) -> None:
    """The crash half: a magic-lined id-less BUGS.md must not abort startup."""
    plan_path, bugs_path = _write_pair(tmp_path)
    _enforce_canonical_inputs(plan_path, bugs_path)  # must not raise


def test_enforce_never_rewrites_bugs(tmp_path: Path) -> None:
    """The oscillation half: BUGS.md bytes are untouched — no magic-line
    re-stamp, no T-id injection via the canonical migration path."""
    plan_path, bugs_path = _write_pair(tmp_path)
    before = bugs_path.read_bytes()

    _enforce_canonical_inputs(plan_path, bugs_path)

    assert bugs_path.read_bytes() == before


def test_enforce_skips_missing_bugs_file(tmp_path: Path) -> None:
    plan_path = tmp_path / "PLAN.md"
    plan_path.write_text(_CANONICAL_PLAN)
    _enforce_canonical_inputs(plan_path, tmp_path / "BUGS.md")  # must not raise


def test_enforce_still_rejects_structurally_corrupt_bugs(tmp_path: Path) -> None:
    """Tolerant is not blind: duplicate Bugs sections are structural
    corruption that even the magic-blanking reader rejects."""
    plan_path, bugs_path = _write_pair(
        tmp_path,
        bugs_text="## Bugs\n- [ ] one\n\n## Bugs\n- [ ] two\n",
    )
    with pytest.raises(PlanSyntaxError):
        _enforce_canonical_inputs(plan_path, bugs_path)


def test_enforce_plan_path_still_canonical_preflighted(tmp_path: Path) -> None:
    """PLAN.md is not loosened: a legacy plan (no magic line, id-less tasks)
    is still migrated to canonical form by the preflight."""
    plan_path = tmp_path / "PLAN.md"
    plan_path.write_text("# Demo\n\n## Stage 1: Core\n\n- [ ] legacy task no id\n")
    bugs_path = tmp_path / "BUGS.md"
    bugs_path.write_text(_MAGIC_IDLESS_BUGS)

    _enforce_canonical_inputs(plan_path, bugs_path)

    migrated = plan_path.read_text()
    assert "<!-- bob-plan-format: 1 -->" in migrated
    assert "T-000001" in migrated
    # And the canonical migration stayed scoped to PLAN.md.
    assert bugs_path.read_text() == _MAGIC_IDLESS_BUGS


def test_clear_failed_markers_tolerates_magic_idless_bugs(tmp_path: Path) -> None:
    """The second strict reader: the [!] pre-count must not strict-parse
    BUGS.md. No failed markers here -> returns 0 without raising."""
    bugs_path = tmp_path / "BUGS.md"
    bugs_path.write_text(_MAGIC_IDLESS_BUGS)

    assert shim.clear_failed_markers(bugs_path) == 0


def test_clear_failed_markers_counts_and_clears_idless_failed(tmp_path: Path) -> None:
    """With a failed id-less entry the count is right and the marker is
    cleared through the magic-aware write path (magic line dropped)."""
    bugs_path = tmp_path / "BUGS.md"
    bugs_path.write_text(
        "<!-- bob-plan-format: 1 -->\n"
        "\n"
        "## Bugs\n"
        "- [!] Fix issue reported during task 11.8 (see observation below):\n"
        "```\n"
        "extract a corpus of 1 file containing two sentences of 10 and 20 words\n"
        "```\n"
    )

    assert shim.clear_failed_markers(bugs_path) == 1

    text = bugs_path.read_text()
    assert "- [ ] Fix issue reported during task 11.8" in text
    assert "<!-- bob-plan-format:" not in text
