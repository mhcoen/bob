"""Cross-repo parity between bob_tools and mcloop canonical predicates.

Stage 17 acceptance: a shared fixture corpus run through both
``bob_tools.planfile.assert_mcloop_canonical`` and mcloop's real
``mcloop._planfile_precondition.enforce_canonical`` must produce
identical accept/reject verdicts on every fixture. This is the Path 1
mitigation for R1/R2 drift recorded in PLAN.md Stage 17: if the two
predicates ever diverge — e.g. mcloop's canonical contract tightens
in a way bob_tools has not mirrored, or vice versa — this test fails
loudly.

Why the parity check feeds both predicates the same rendered text
-----------------------------------------------------------------
The two functions have different signatures:

* ``bob_tools.planfile.assert_mcloop_canonical(plan)`` operates on a
  parsed ``Plan``. It renders the plan internally, re-parses, and
  applies the R1 (grammar-narrowing) and R2 (id-less) equivalents to
  that ``(rendered_text, reparsed_plan)`` pair.
* ``mcloop._planfile_precondition.enforce_canonical(source_text, plan)``
  takes the source text and a parsed plan as separate arguments and
  applies R1 to that pair directly.

To compare predicate semantics — not signature differences — the test
gives mcloop the same ``(rendered_text, reparsed_plan)`` pair that
bob_tools constructs internally. Both functions then evaluate the same
input; any disagreement is a true semantic divergence. Concretely,
for every fixture:

1. ``plan`` is obtained (parsed from a fixture file or constructed
   programmatically).
2. ``rendered = render_plan(plan)`` and ``reparsed = parse_plan(rendered)``
   are computed once.
3. ``bob_verdict = assert_mcloop_canonical(plan)`` — accept iff no raise.
4. ``mc_verdict = enforce_canonical(rendered, reparsed)`` — accept iff
   no raise.
5. Assert ``bob_verdict == mc_verdict``.

The corpus deliberately covers the three branches the gate must
exercise:

* **canonical-pass**: rendered text and reparsed plan are clean; both
  predicates ACCEPT.
* **R2-idless**: reparsed plan contains tasks without ``T-NNNNNN`` ids;
  both predicates REJECT (R2 fires on each side).
* **R1-drop**: rendered text contains ``- [ ]`` lines that the reparse
  drops from the task set (e.g. a checkbox-shaped line embedded in
  preamble prose, which the parser does not surface as a task). On the
  bob_tools side this trips the semantic round-trip check before R1
  proper has a chance to fire, but the externally-visible verdict is
  still REJECT, which is the parity claim. On the mcloop side R1 fires
  directly because ``src_incomplete > plan_incomplete``.

The R1-drop case is constructed programmatically because the parser
strips checkbox-shaped orphans from preamble during the very first
parse: a ``.md`` fixture cannot carry such a violation through a
parse-render-reparse round-trip. Building the plan in code lets us
inject the orphan after parsing.

Importing mcloop's precondition module
--------------------------------------
mcloop is not a bob_tools dependency, so the package is not on the
venv's import path. The sibling project's source tree is added to
``sys.path`` at runtime to import ``mcloop._planfile_precondition``
directly; the test skips with a clear message when the sibling project
is not present (CI, fresh clones, anywhere outside the dev environment).
This mirrors the approach in ``test_mcloop_parity.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from bob_tools.planfile import (
    Phase,
    Plan,
    PlanValidationError,
    Task,
    TaskStatus,
    assert_mcloop_canonical,
    parse_plan,
    render_plan,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "parity"

# Workspace sibling: packages/mcloop next to packages/bob-tools. The
# old hardcoded /Users/mhcoen/proj/mcloop pointed at the VESTIGIAL
# standalone checkout, so parity was silently verified against stale
# code (which is exactly how the fence-parity wedge shipped: the live
# predicate diverged while the corpus kept passing against the relic).
_MCLOOP_ROOT = Path(__file__).parents[4] / "mcloop"


def _load_mcloop_precondition() -> Any | None:
    """Import ``mcloop._planfile_precondition`` (live copy), or None.

    In the bob workspace venv mcloop is editable-installed, so a plain
    import resolves to the live ``packages/mcloop`` source — preferred.
    The sys.path fallback covers a checkout without the editable
    install. Returns ``None`` when neither works; the parametrized
    tests skip cleanly instead of erroring.
    """
    try:
        from mcloop import _planfile_precondition  # type: ignore[import-not-found]

        return _planfile_precondition
    except Exception:
        pass
    target = _MCLOOP_ROOT / "mcloop" / "_planfile_precondition.py"
    if not target.is_file():
        return None
    if str(_MCLOOP_ROOT) not in sys.path:
        sys.path.insert(0, str(_MCLOOP_ROOT))
    try:
        from mcloop import _planfile_precondition
    except Exception:
        return None
    return _planfile_precondition


def _construct_r1_drop_plan() -> Plan:
    """Build a ``Plan`` whose rendered form contains an orphan ``- [ ]`` line.

    The preamble carries a literal checkbox-shaped line. The renderer
    emits the preamble verbatim, so the rendered text has one more
    ``- [ ]`` line than the parser will surface as a task on reparse
    (the parser strips orphan checkboxes from preamble). That count
    mismatch is the R1 signature; mcloop's predicate fires it directly.
    Bob_tools' predicate raises first on the failed semantic round-trip
    (preamble in vs preamble out differ) — the *reason* differs but the
    externally-visible verdict is identical, which is the parity claim.
    """
    return Plan(
        magic_version=None,
        project_title="R1-drop fixture",
        preamble="Intro paragraph.\n- [ ] orphan in preamble\nClosing paragraph.",
        phases=(
            Phase(
                phase_id="phase_001",
                phase_id_source="explicit_comment",
                ordinal=1,
                keyword="Stage",
                title="One",
                prose="",
                subsections=(),
                tasks=(
                    Task(
                        task_id="T-000001",
                        text="real task",
                        status=TaskStatus.TODO,
                        flag_tags=(),
                        action_tag=None,
                        annotations=(),
                        deps=(),
                        children=(),
                        ruled_out=(),
                        indent_level=0,
                        line_number=0,
                        trailing_lines=(),
                    ),
                ),
                line_number=0,
            ),
        ),
        bugs=None,
        source_path=None,
    )


# The corpus is a list of ``(case_id, plan_factory, expected_verdict)``.
# ``case_id`` is the parametrize id used in test output. ``plan_factory``
# returns a freshly built plan each call so a test cannot accidentally
# mutate the fixture for the next iteration (the dataclasses are frozen,
# but tuple aliasing across phases is still defensive against). The
# expected_verdict pins the *intended* outcome so a regression in either
# predicate (e.g. both start passing what should fail) is also caught.
_TEXT_FIXTURES: tuple[tuple[str, Path, str], ...] = (
    (
        "canonical_pass_magic",
        FIXTURES_DIR / "canonical_pass_magic.md",
        "ACCEPT",
    ),
    (
        "canonical_pass_no_magic",
        FIXTURES_DIR / "canonical_pass_no_magic.md",
        "ACCEPT",
    ),
    (
        "canonical_pass_bugs_only",
        FIXTURES_DIR / "canonical_pass_bugs_only.md",
        "ACCEPT",
    ),
    (
        # A fenced checkbox example must not read as a dropped task on
        # EITHER side of the parity contract (the fence-unaware count in
        # mcloop's precondition used to reject what fmt blesses,
        # wedging startup with a no-op remediation).
        "canonical_pass_fenced_example",
        FIXTURES_DIR / "canonical_pass_fenced_example.md",
        "ACCEPT",
    ),
    (
        "r2_idless_phase_bearing",
        FIXTURES_DIR / "r2_idless_phase_bearing.md",
        "REJECT",
    ),
    (
        "r2_idless_subsection",
        FIXTURES_DIR / "r2_idless_subsection.md",
        "REJECT",
    ),
)


def _build_corpus() -> list[tuple[str, Plan, str]]:
    """Materialize the parity corpus as ``(case_id, plan, expected)`` triples.

    Text fixtures are loaded once at module import. The R1-drop case is
    constructed programmatically because the parser strips checkbox
    orphans from preamble on the way in, so the violation cannot be
    expressed in a ``.md`` file that survives a parse-render-reparse
    round-trip.
    """
    out: list[tuple[str, Plan, str]] = []
    for case_id, path, expected in _TEXT_FIXTURES:
        text = path.read_text()
        plan = parse_plan(text)
        out.append((case_id, plan, expected))
    out.append(("r1_drop_preamble_orphan", _construct_r1_drop_plan(), "REJECT"))
    return out


CORPUS = _build_corpus()


@pytest.mark.parametrize(
    ("case_id", "plan", "expected"),
    CORPUS,
    ids=[c[0] for c in CORPUS],
)
def test_predicates_agree(case_id: str, plan: Plan, expected: str) -> None:
    """Both predicates must reach the same verdict on every corpus entry.

    The corpus's ``expected`` field pins the intended outcome so a
    regression where both predicates start accepting (or rejecting) the
    wrong thing — and therefore "agree" — is also caught.
    """
    precondition = _load_mcloop_precondition()
    if precondition is None:
        pytest.skip(
            f"mcloop._planfile_precondition could not be imported from "
            f"{_MCLOOP_ROOT}; this parity check only runs in the dev "
            "environment where the mcloop project is checked out "
            "alongside bob_tools"
        )
    enforce_canonical = precondition.enforce_canonical
    plan_not_canonical_error = precondition.PlanNotCanonicalError

    rendered = render_plan(plan)
    reparsed = parse_plan(rendered)

    try:
        assert_mcloop_canonical(plan)
        bob_verdict = "ACCEPT"
        bob_reason = ""
    except PlanValidationError as exc:
        bob_verdict = "REJECT"
        bob_reason = "; ".join(exc.messages)

    try:
        enforce_canonical(rendered, reparsed)
        mc_verdict = "ACCEPT"
        mc_reason = ""
    except plan_not_canonical_error as exc:
        mc_verdict = "REJECT"
        mc_reason = str(exc)

    assert bob_verdict == mc_verdict, (
        f"R1/R2 parity violation on case {case_id!r}: "
        f"bob_tools.assert_mcloop_canonical={bob_verdict!r} "
        f"mcloop._planfile_precondition.enforce_canonical={mc_verdict!r}.\n"
        f"  bob_tools reason: {bob_reason}\n"
        f"  mcloop reason:    {mc_reason}"
    )
    assert bob_verdict == expected, (
        f"both predicates reached {bob_verdict!r} on case {case_id!r} "
        f"but the corpus pins the expected verdict as {expected!r}. "
        "Either the fixture's expected verdict is stale or both "
        "predicates regressed in the same direction."
    )


def test_corpus_covers_required_branches() -> None:
    """The corpus must exercise canonical-pass, R1-drop, and R2-idless branches.

    Stage 17 gate (PLAN.md T-000185) requires the parity corpus to
    include all three categories. This guard fails loudly if a future
    edit deletes the only fixture in one category, leaving the parity
    test green but its coverage shallow.
    """
    case_ids = {entry[0] for entry in CORPUS}
    canonical = {c for c in case_ids if c.startswith("canonical_pass")}
    r1 = {c for c in case_ids if c.startswith("r1_drop")}
    r2 = {c for c in case_ids if c.startswith("r2_idless")}
    assert canonical, "corpus missing a canonical-pass fixture"
    assert r1, "corpus missing an R1-drop fixture"
    assert r2, "corpus missing an R2-idless fixture"
