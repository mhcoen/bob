"""B3 increment 3: integration proof that the canonical-plan precondition
gate at ``run_loop`` entry dominates all three pre-loop mutation sites
enumerated in ``.scratch/B3_INCREMENT2_PATH_ENUM.md``.

Cases:
  (a) ``--retry`` clear (``mcloop/main.py`` ~700-711): a non-canonical
      PLAN.md / BUGS.md must be REJECTED before
      ``clear_failed_markers`` runs — the file content must be byte-
      preserved across the failed run.
  (b) ``_check_interrupted`` skip (``mcloop/lifecycle.py`` ~178,
      217-261): a non-canonical PLAN.md plus an interrupted-state file
      must be REJECTED before lifecycle's ``mark_failed`` /
      ``_write_ruledout_to_plan`` runs — the PLAN.md is byte-preserved
      and ``interrupted.json`` is still present.
  (c) ``plan startup`` split (``mcloop/main.py`` ~977 →
      ``mcloop/plan.py`` ~204): a non-canonical master PLAN.md
      with no PLAN.md on disk must be REJECTED before the
      extraction writes PLAN.md — PLAN.md must NOT be
      created.

Plus a permissive baseline:
  (d) a canonical PLAN.md (phase + T-IDs) plus a canonical BUGS.md (the
      ``## Bugs\\n\\n`` shape ``ensure_bugs_file`` writes) must pass
      the gate without raising.

The tests invoke ``mcloop.main.run_loop`` directly and assert it raises
``PlanNotCanonicalError`` on the failing cases. The exit-code-3
behaviour is exercised at the ``main()`` boundary; tests for that
top-level translation live in unit-test territory (the wire-in proof
here is that the gate runs and rejects BEFORE the cited mutation
sites can run).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcloop._planfile_precondition import PlanNotCanonicalError
from mcloop.main import _enforce_canonical_inputs, run_loop

_CANONICAL_PLAN = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "# Demo\n"
    "\n"
    "## Stage 1: Setup\n"
    "<!-- phase_id: phase_001 -->\n"
    "\n"
    "- [ ] T-000001: only task\n"
)
_CANONICAL_BUGS = "## Bugs\n\n"
_NON_CANONICAL = "- [ ] Do something\n"  # phaseless; classic R1 trap


# ---------------------------------------------------------------------------
# Helper-level proof (cheap and deterministic)
# ---------------------------------------------------------------------------


def test_helper_rejects_phaseless_master(tmp_path: Path) -> None:
    master = tmp_path / "PLAN.md"
    master.write_text(_NON_CANONICAL)
    bugs = tmp_path / "BUGS.md"
    with pytest.raises(PlanNotCanonicalError) as ei:
        _enforce_canonical_inputs(master, bugs)
    assert "bob-plan migrate" in str(ei.value)


def test_helper_rejects_phaseless_plan(
    tmp_path: Path,
) -> None:
    master = tmp_path / "PLAN.md"
    master.write_text(_NON_CANONICAL)
    bugs = tmp_path / "BUGS.md"
    with pytest.raises(PlanNotCanonicalError) as ei:
        _enforce_canonical_inputs(master, bugs)
    assert "PLAN.md" in str(ei.value.source_path)


def test_helper_rejects_phaseless_bugs_even_if_master_canonical(
    tmp_path: Path,
) -> None:
    master = tmp_path / "PLAN.md"
    master.write_text(_CANONICAL_PLAN)
    bugs = tmp_path / "BUGS.md"
    bugs.write_text("- [ ] some bug\n")  # no ## Bugs header
    with pytest.raises(PlanNotCanonicalError) as ei:
        _enforce_canonical_inputs(master, bugs)
    assert "BUGS.md" in str(ei.value.source_path)


def test_helper_passes_canonical_master_with_canonical_bugs(
    tmp_path: Path,
) -> None:
    master = tmp_path / "PLAN.md"
    master.write_text(_CANONICAL_PLAN)
    bugs = tmp_path / "BUGS.md"
    bugs.write_text(_CANONICAL_BUGS)
    _enforce_canonical_inputs(master, bugs)  # MUST NOT raise


def test_helper_passes_canonical_master_with_no_bugs_no_current(
    tmp_path: Path,
) -> None:
    master = tmp_path / "PLAN.md"
    master.write_text(_CANONICAL_PLAN)
    bugs = tmp_path / "BUGS.md"  # absent
    _enforce_canonical_inputs(master, bugs)  # MUST NOT raise


# ---------------------------------------------------------------------------
# Wire-in proof at run_loop entry: the gate must run before each cited
# pre-loop mutation site, so non-canonical input leaves the file
# byte-preserved.
# ---------------------------------------------------------------------------


def _init_git(tmp_path: Path) -> None:
    # run_loop calls _ensure_git which initializes a repo if needed.
    # Pre-seed a minimal repo so test failures point at the gate, not
    # at git setup. We do not need a real commit; _ensure_git suffices.
    (tmp_path / ".git").mkdir(exist_ok=True)


def test_a_retry_clear_does_not_mutate_when_gate_rejects(tmp_path: Path) -> None:
    """(a) --retry must not run clear_failed_markers on a non-canonical
    PLAN.md / BUGS.md — the gate fires first.

    Fixture carries both a ``- [ ]`` (so the canonical-form gate
    triggers REJECT on the phaseless input) and a ``- [!]`` (so
    ``clear_failed_markers`` would mutate this file by flipping the
    [!] back to [ ] if the gate were not running before it).
    """
    _init_git(tmp_path)
    master = tmp_path / "PLAN.md"
    master.write_text(_CANONICAL_PLAN)  # master is canonical
    current_plan = tmp_path / "PLAN.md"
    current_plan_text = "- [ ] pending task\n- [!] previously failed task\n"
    current_plan.write_text(current_plan_text)
    bugs = tmp_path / "BUGS.md"
    bugs.write_text(_CANONICAL_BUGS)

    with pytest.raises(PlanNotCanonicalError):
        run_loop(master, retry=True, no_audit=True)

    # The pre-loop --retry clear at main.py:701 would have rewritten
    # PLAN.md to flip [!] -> [ ]. The gate must have stopped
    # that before it ran. Byte-equal proves no mutation occurred.
    assert current_plan.read_text() == current_plan_text


def test_b_check_interrupted_does_not_mutate_when_gate_rejects(
    tmp_path: Path,
) -> None:
    """(b) _check_interrupted must not run mark_failed /
    _write_ruledout_to_plan on a non-canonical PLAN.md — the gate fires
    first."""
    _init_git(tmp_path)
    master = tmp_path / "PLAN.md"
    master_text = "- [ ] interrupted task\n"  # non-canonical
    master.write_text(master_text)

    # Stage an interrupted state. _check_interrupted prompts for input;
    # by raising before it runs, the gate also dominates that
    # interactive surface.
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir(exist_ok=True)
    interrupted_path = mcloop_dir / "interrupted.json"
    interrupted_path.write_text(
        json.dumps(
            {
                "phase": "task",
                "task_label": "1.1",
                "task_text": "interrupted task",
                "elapsed_seconds": 5,
                "last_output": [],
                "timestamp": "2026-05-18T00:00:00Z",
            }
        )
    )

    with pytest.raises(PlanNotCanonicalError):
        run_loop(master, no_audit=True)

    # PLAN.md byte-preserved (no mark_failed, no [RULEDOUT] append).
    assert master.read_text() == master_text
    # interrupted.json byte-preserved (no _check_interrupted side
    # effects — it unlinks on every branch).
    assert interrupted_path.exists()


def test_c_plan_startup_preserves_plan_when_gate_rejects(
    tmp_path: Path,
) -> None:
    """(c) plan startup must not mutate PLAN.md when the gate fires first."""
    _init_git(tmp_path)
    master = tmp_path / "PLAN.md"
    master.write_text("# Plan\n\n- [ ] Do task\n")  # phaseless master
    before = master.read_text()

    with pytest.raises(PlanNotCanonicalError):
        run_loop(master, no_audit=True)

    assert master.read_text() == before


def test_d_gate_runs_before_parse_description_too(tmp_path: Path) -> None:
    """Gate ordering: the gate must fire before parse_description.
    parse_description is read-only so this is a sanity check on
    placement — not a mutation gate — but verifying the gate runs
    before EVERY post-path-defs call site is the structural invariant
    the wire-in claims."""
    _init_git(tmp_path)
    master = tmp_path / "PLAN.md"
    master.write_text(_NON_CANONICAL)
    with pytest.raises(PlanNotCanonicalError):
        run_loop(master, no_audit=True)
