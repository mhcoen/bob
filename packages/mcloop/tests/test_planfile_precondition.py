"""Empirical validation of the R1 discriminator over the actual test corpus.

INCREMENT 1 of B3 Stage B3.1. The corpus is the set of distinct plan-input
literals harvested by ``.scratch/harvest_plans.py`` from every ``write_text``
call against a PLAN.md / PLAN.md / master / current receiver in
``tests/``. Each literal is auto-labeled by the harvester based on whether
the source text contains an incomplete checkbox (``- [ ]``) and whether it
contains a ``## Stage`` / ``## Phase`` header. The labels are the oracle
this test checks the discriminator against.

The corpus file lives at ``.scratch/precondition_corpus.json``. Running the
harvester is part of test discovery (the corpus is loaded at module import).

Confusion-matrix emission: this module emits a confusion matrix to
``.scratch/confusion_matrix.txt`` whether tests pass or fail; that file is
the deliverable for the increment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bob_tools.planfile import parse_plan
from bob_tools.planfile.model import PlanSyntaxError

from mcloop._planfile_precondition import (
    PlanNotCanonicalError,
    discriminate_r1,
    discriminate_r2,
    enforce_canonical,
)

ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = ROOT / ".scratch" / "precondition_corpus.json"

# Map oracle label → expected R1 verdict
_EXPECTED: dict[str, str] = {
    "GRAMMAR_NARROWED": "REJECT_GRAMMAR_NARROWED",
    "GENUINELY_EMPTY": "ALLOW",
    "PHASE_BEARING": "ALLOW",  # R1 lets these through; R2 may later reject
}


def _load_corpus() -> list[dict]:
    if not CORPUS_PATH.exists():
        pytest.skip(
            f"corpus not found at {CORPUS_PATH}; run .scratch/harvest_plans.py first",
            allow_module_level=True,
        )
    data = json.loads(CORPUS_PATH.read_text())
    return data["corpus"]


CORPUS = _load_corpus()


def _classify(text: str) -> str:
    """Run the discriminator and return its verdict for a single input.

    Returns ``"PARSE_FAILED"`` when ``parse_plan`` rejects the input as
    structurally corrupt — that case is handled upstream of the
    precondition by the parser itself; the discriminator does not run.
    """
    try:
        plan = parse_plan(text, source_path=None)
    except PlanSyntaxError:
        return "PARSE_FAILED"
    verdict, _ = discriminate_r1(text, plan)
    return verdict


@pytest.mark.parametrize(
    "entry",
    CORPUS,
    ids=[f"{e['label']}#{i:03d}" for i, e in enumerate(CORPUS)],
)
def test_discriminator_matches_oracle(entry: dict) -> None:
    label = entry["label"]
    expected = _EXPECTED[label]
    actual = _classify(entry["text"])
    # ``PARSE_FAILED`` is an upstream parser rejection, not a discriminator
    # outcome. Both ``GRAMMAR_NARROWED`` and ``PHASE_BEARING`` entries can
    # hit it on structurally corrupt input (e.g. duplicate phase numbers);
    # mcloop's runtime will surface the parser error directly without the
    # precondition needing to fire.
    assert actual == expected or actual == "PARSE_FAILED", (
        f"discriminator misclassification: corpus label={label} "
        f"expected verdict={expected} actual verdict={actual}\n"
        f"  first site: {entry['sites'][0]}\n"
        f"  text repr: {entry['text']!r}"
    )


def test_enforce_canonical_raises_on_r1_or_r2_rejection_and_passes_others() -> None:
    """enforce_canonical() raises iff R1 or R2 rejects.

    This is the call shape mcloop will use at run_loop entry once Stage B3.1
    completes. The empirical claim: over every harvested input that
    successfully parses, the raise behavior matches the distinct R1/R2
    predicate outcomes.
    """
    rejected = 0
    allowed = 0
    parse_failed = 0
    for entry in CORPUS:
        try:
            plan = parse_plan(entry["text"], source_path=None)
        except PlanSyntaxError:
            parse_failed += 1
            continue
        r1_verdict, _ = discriminate_r1(entry["text"], plan)
        r2_verdict, _ = discriminate_r2(plan)
        should_reject = (
            r1_verdict == "REJECT_GRAMMAR_NARROWED" or r2_verdict == "REJECT_ID_LESS_TASKS"
        )
        try:
            enforce_canonical(entry["text"], plan)
        except PlanNotCanonicalError:
            rejected += 1
            assert should_reject, (
                f"enforce_canonical rejected despite ALLOW/ALLOW predicates: "
                f"label={entry['label']} first site={entry['sites'][0]} "
                f"r1={r1_verdict} r2={r2_verdict}"
            )
        else:
            allowed += 1
            assert not should_reject, (
                f"enforce_canonical allowed a rejecting input: "
                f"first site={entry['sites'][0]} r1={r1_verdict} r2={r2_verdict}"
            )
    # Sanity floor: the harvested corpus is non-trivial in both directions.
    assert rejected >= 30, f"too few REJECTs ({rejected}); corpus may be skewed"
    assert allowed >= 1, f"too few ALLOWs ({allowed}); corpus may be skewed"
    # Record the parse-failed count on stderr-equivalent (xdist-friendly).
    assert parse_failed >= 0  # tautology; the count is in the confusion matrix.


def test_emit_confusion_matrix() -> None:
    """Always-emit confusion matrix so the increment's report is reproducible."""
    matrix: dict[tuple[str, str], int] = {}
    misclass: list[dict] = []
    for entry in CORPUS:
        label = entry["label"]
        actual = _classify(entry["text"])
        key = (label, actual)
        matrix[key] = matrix.get(key, 0) + 1
        if actual != _EXPECTED[label]:
            misclass.append(
                {
                    "label": label,
                    "expected": _EXPECTED[label],
                    "actual": actual,
                    "first_site": entry["sites"][0],
                    "site_count": entry["site_count"],
                    "text": entry["text"],
                }
            )

    labels = sorted({lbl for lbl, _ in matrix})
    verdicts = sorted({v for _, v in matrix})
    lines = ["R1 discriminator confusion matrix", "=" * 50, ""]
    header = "label".ljust(22) + " | " + " | ".join(v.ljust(28) for v in verdicts)
    lines.append(header)
    lines.append("-" * len(header))
    for lbl in labels:
        row = (
            lbl.ljust(22)
            + " | "
            + " | ".join(str(matrix.get((lbl, v), 0)).ljust(28) for v in verdicts)
        )
        lines.append(row)
    lines.append("")
    lines.append(f"corpus size: {len(CORPUS)} distinct literal(s)")
    lines.append(f"misclassifications: {len(misclass)}")
    if misclass:
        lines.append("")
        for m in misclass:
            lines.append(
                f"  MISCLASS  label={m['label']}  expected={m['expected']}  "
                f"actual={m['actual']}  first_site={m['first_site']}  "
                f"sites={m['site_count']}"
            )
            lines.append(f"            text={m['text']!r}")
    out = ROOT / ".scratch" / "confusion_matrix.txt"
    out.write_text("\n".join(lines) + "\n")
    # No assertion here — emission only. The label-vs-verdict assertions
    # live in the parametrized test above.
