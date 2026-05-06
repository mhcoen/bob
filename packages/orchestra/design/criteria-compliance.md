# Criteria-compliance: F2.5a (deployed) and F2.5b (deferred)

This document is the canonical scope-split design for the
criteria-compliance work. F2.5a is deployed. F2.5b is queued, with
its open questions captured below.

## Why F2.5

Calibration runs during Phase 2 surfaced a failure mode where the
judge accepted artifacts that strictly violated configured criteria.
The relevant evidence is in `/tmp/orchestra-phase2/REPORT.md`
Addendum 6: in iter-anchor-{neg,pos}, haiku produced titles that
violated the character count and last-character constraints, and
the judge accepted them anyway. The judge claimed to verify the
word count ("I verified the word count at 12 words") but did not
verify the other criteria.

T1+T2 (commit e983312) added prose-level "verify each criterion
against the current artifact" instructions. The iter-anchor data
falsified this as a sufficient general discipline: a free-text
verification instruction does not structurally prevent the judge
from skipping criteria.

F2.5 makes the judge structurally unable to skip criteria. The
verdict carries a `criteria_compliance` array, one entry per
configured criterion. The schema enforces shape; the runtime
enforces semantic consistency between `decision` and the
per-criterion compliance values.

## F2.5 splits into F2.5a and F2.5b

A single global `criteria_compliance → decision` derivation rule
does not generalize across PRJI. iterate has three decisions
({accept, iterate, stuck}); PRJI has five ({accept, implement,
rereview, reframe, stuck}). The mapping from per-criterion
compliance to the right non-accept decision is workflow-specific
and depends on failure-class taxonomy (code-level vs framing-level)
and cycle history.

What does generalize across both workflows is the **accept
boundary**: no route should accept an artifact with non-compliant
required criteria. Everything else is route-specific policy.

The split:

- **F2.5a** (this commit): universal criterion enumeration +
  schema-shape validation + runtime accept-consistency invariant.
  iterate also gets the stronger bidirectional check (non-accept
  decisions imply at least one required criterion is non-compliant)
  because F2's stuck wording already encodes that invariant in
  prose; F2.5a makes it structurally enforced.
- **F2.5b** (queued): PRJI route-specific non-accept derivation.
  Substantial design work; deferred until F2.5a is in production
  and there is data on what real failure-class taxonomy looks like.

## F2.5a: what landed

Six pieces:

1. **Config layer** (`orchestra/config.py`). New `CriterionDecl`
   dataclass: `id`, `description`, `required: bool = True`.
   `OrchestraConfig` gains a top-level `criteria` field. Per-element
   validation: id non-empty, ids unique within the array.
   Backward compatible: scenarios without the field have empty
   `criteria` and the runtime check is a no-op.

2. **Verdict schemas**
   (`orchestra/workflows/schemas/{iterate,prji}_judge_verdict.json`).
   Both gain a `criteria_compliance` property: array of objects with
   required fields `criterion_id` (string), `observed_value` (string),
   `compliant` (boolean). The JSON Schema enforces shape; it does
   not enforce id uniqueness or id-match-config (those are runtime
   semantic checks). The field is not in the schema's `required`
   array because pre-F2.5a workflows must continue to validate
   without it; runtime presence-and-coverage is gated on the
   `criteria` config being non-empty.

3. **Runtime decision-consistency check**
   (`orchestra/executor/criteria.py`). Pure function
   `check_decision_consistency(decision, criteria_compliance,
   configured, mode)` returning a `DecisionConsistencyResult` with
   ok/reason/missing_ids/extra_ids/duplicate_ids/noncompliant_required_ids.
   Two modes:

   - `ACCEPT_ONLY`: forbids `decision="accept"` when any required
     criterion is non-compliant. Non-accept decisions pass through
     unchecked. PRJI's mode.
   - `STRICT_BIDIRECTIONAL`: also requires that any non-accept
     decision corresponds to at least one non-compliant required
     criterion. iterate's mode.

   Coverage failures (missing/extra/duplicate ids) take precedence
   over decision-vs-compliance failures, since the latter is
   undefined when coverage is wrong.

   The check is invoked in `Executor._apply_schema_layer` after
   schema validation succeeds. On violation: discard the tentative
   writes, log a `decision_consistency` event with
   `outcome=violation` and the reason slug, return an `ErrorRecord`.
   The state exits via the error outcome; no terminal accept happens.

4. **Scenario lint** (`orchestra/calibration/lint_scenario.py`).
   `lint_scenario(scenario_dir) → LintResult`. Verifies every
   configured criterion id appears as a whole-word token in
   `task.md` (regex `\b<id>\b`). Word-boundary matching is the
   right balance: tighter than substring (which would false-positive
   on short ids inside larger words like `length`), looser than
   requiring an explicit syntactic marker (which would burden
   task.md authors). CLI:
   `python -m orchestra.calibration.lint_scenario <dir>`.

5. **Migrated calibration scenarios**
   (`tests/fixtures/calibration/{iterate,prji}/<scenario>/`). The
   8 Phase 2 calibration scenarios moved here; the iter-anchor pair
   gained `criteria` arrays in their `.orchestra/config.json` and
   ids referenced explicitly in `task.md` prose. The other six are
   pre-F2.5a fixtures with no criteria; lint passes them with a
   warning.

6. **Judge templates**
   (`orchestra/workflows/templates/{iterate,prji}_judge.md`). Both
   templates now require a `criteria_compliance` field in the
   verdict and instruct the judge to enumerate every configured
   criterion, observe the artifact directly, and report compliance
   per criterion. The "Current artifact beats prior feedback and
   reviewer restatement" T1+T2 invariant stays.

## Test surface

- `tests/test_decision_consistency.py`: pure-function tests for
  the runtime check (~13 cases). Pins coverage failures, accept
  boundary, mode-specific behavior, and the workflow-to-mode
  lookup.
- `tests/test_calibration.py`: extended with lint cases (positive,
  negative, word-boundary edge case, missing-criteria, missing
  task.md) plus a parametric test that every scenario in
  `tests/fixtures/calibration/` lints clean.
- `tests/test_e2e_decision_consistency.py`: 3 end-to-end runs
  through the iterate workflow with mock_model adapters scripted
  to return verdicts with valid and invalid `criteria_compliance`.
  Asserts terminal state, decision_consistency log records, and
  reason slugs.

## F2.5b: what is deferred

Route-specific non-accept decision derivation for PRJI. Concrete
open questions to resolve when F2.5b begins:

1. **Failure-class taxonomy.** PRJI's non-accept routes
   (`implement`, `rereview`, `reframe`, `stuck`) each correspond to
   a different failure category: code-level defect, ambiguous
   reviewer finding, framing-level defect, or persistence. Where
   does the taxonomy live?
   - Annotated on each `criteria_compliance` entry (a new
     `failure_class` field per criterion when `compliant=false`)?
   - Derived from a per-criterion `class` declaration in
     `.orchestra/config.json`?
   - Judge-emitted alongside `compliant=false`, runtime maps to
     decision?

2. **Cycle history input.** F2 plumbs `judge_decision` and
   `judge_feedback` from the prior cycle into the next prompt. Is
   that sufficient input for a runtime decision-derivation rule, or
   does PRJI route policy need an explicit cycle counter,
   per-failure-class history, or both?

3. **Per-route invariants beyond accept.** Implement requires
   `fix_instructions` populated. Reframe requires the framing to
   change. These are partly enforced today via the workflow IR;
   audit and gap-fill what F2.5b should add.

The decision on F2.5b's scope happens after F2.5a runs in
production for at least one calibration cycle. Real trajectory
data on which non-accept routes the judge picks (and how often it
picks "wrong") will inform what taxonomy and runtime policy are
worth encoding.

## Observability

The new log event `decision_consistency` fires on every successful
schema validation when `criteria` is configured. Fields:

- `artifact`: the verdict artifact name.
- `outcome`: `"ok"` or `"violation"`.
- `decision`: the verdict's decision string.
- `mode`: the consistency mode (`accept_only` or
  `strict_bidirectional`).
- On violation: `reason` (slug like `accept_with_noncompliant`,
  `non_accept_with_full_compliance`, `missing_ids`, `extra_ids`,
  `duplicate_ids`), plus `missing_ids`, `extra_ids`,
  `duplicate_ids`, `noncompliant_required_ids` arrays for triage.

Post-run analysis can filter on
`event=="decision_consistency" && outcome=="violation"` to find
runs the runtime caught.
