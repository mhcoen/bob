# Synthesizer output contract

This note captures a structural lesson the Slice C smokes
produced: when a synthesizer's output encodes state the runtime
must consume, the state belongs in JSON, not in markdown
conventions or other prose-side protocols.

## Problem

Two synthesizer-side failures across two Slice C real-API smoke
runs prompted two template tightenings.

The first was a file-write confusion. The synthesizer treated
Write/Edit/Bash tools as the deliverable mechanism instead of
writing into its response. The fix landed in 34d5509 as a "How
you deliver your output" section that explicitly tells the model
the response IS the deliverable and that file-write tools will be
rejected.

The second was a preserved-id-with-supersedes-claim. The
synthesizer mixed states encoded via markdown headers and HTML
comments: a phase id from the prior plan kept its id (so the
parser saw "preserved") while a `<!-- supersedes: ... -->`
comment claimed the phase replaced an older one. The pipeline's
fail-closed validator caught the contradiction, but the verdict
JSON had to be hand-untangled to see what the synthesizer had
intended. The fix landed in 044cafb (orchestra schema + template)
plus d528011 (duplo consumer): lineage moved out of the markdown
into a JSON object alongside decision / feedback / etc.

## Pattern

This sequence has a familiar shape. Phase 2's iterate and PRJI
work product walked the same path. F1 (5c989e3) added tolerant
JSON-object extraction so models could emit verdicts in fenced
prose. F2 (3be5d99) split decision/feedback into structured
fields. T1 + T2 (e983312) pinned the "prior claims are
hypotheses" framing in the prompt. F2.5a (07e28c7) finally made
criteria_compliance a structured array with an
accept-consistency invariant: any "accept" verdict must satisfy
every declared criterion, enforced in the runtime, not in the
prompt.

The progression each time: a prose-side discipline (a tighter
template paragraph, an extra rule, an added warning) accumulates
against a structural problem until the right answer is to move
the rule into the schema. Each violation prompts a tighter
prompt; eventually the prompt's surface area is the design
surface area, which means the prompt is doing the work the
schema should do. F2.5a is the structural lesson the council_four
synthesizer template kept needing to relearn.

The Slice C smokes ran the same pattern in miniature against a
different surface. Two violations, two template tightenings, then
the structural move.

## The lesson

When synthesizer outputs encode state that the runtime consumes,
the state goes in JSON, not in markdown. Markdown is for prose
deliverables a human reads. JSON is for state the runtime
parses. Mixing them creates protocols the model can violate
while still producing plausible prose; the validator detects the
violation but cannot help the synthesizer produce a clean output
the next time, because the contract is in a place the schema
cannot reach.

## The contract

For workflows that route through a synthesizer state (council_four
today, any future workflow that uses claude_code_text in a
synthesis role inherits the same contract):

(a) The synthesizer's prose deliverable goes in markdown in the
    response body.

(b) Any state the runtime consumes (decisions, lineage,
    criteria_compliance, agreements, disagreements,
    rejected_options, etc.) goes in a single fenced JSON block at
    the end of the response.

(c) The JSON shape is JSON-Schema-validated. Required fields,
    enums, and nested object shapes are catch-at-parse failures.

(d) Slice-C-shape semantic validation runs after schema
    validation when invariants depend on prior state or external
    context (cross-plan id matching, exactly-once accounting,
    workspace-mutation rules, etc.). Semantic validation
    fail-closed: a violation rolls back any tentative artifact
    writes and surfaces the offending invariant by name.

## What this is NOT

This is not "every synthesizer output must be JSON." Markdown
deliverables stay markdown. Plans, design documents, prose
analyses, code review writeups remain markdown deliverables and
that is the whole point of having a synthesizer rather than a
parser. The lesson applies to the *machine-consumed parts* of the
response only: state the runtime reads to make routing decisions,
to write events to the ledger, to compare against criteria, or to
trigger follow-up workflow steps. Encode those parts in JSON;
leave the rest in markdown.

## Workflow boundary, not prose-instruction layer

The Slice D smoke surfaced a related lesson on the same
structural pattern. The original `council_four` workflow served
both Duplo's canonical-mode plan authoring (output consumed by
McLoop's task-driver, which expects checklist tasks under phase
headers) AND Duplo's re-author mode (output consumed by Slice C's
lineage validator, which expects narrative-prose phase bodies
plus a JSON lineage sidecar). One workflow, one synthesizer
template, two distinct consumer contracts inferred from "is the
council brief's `ledger_slice` empty?"

The canonical-mode smoke produced four phases of narrative prose
with zero `- [ ]` task lines. McLoop saw a plan it could not run.
No validator existed at the canonical-output boundary; the
synthesizer's template could correctly satisfy the re-author
contract while silently violating the canonical contract.

The fix split the workflow at the boundary visible to the
runtime, not at the prose layer:

- `council_four_canonical.orc` + `council_synthesizer_canonical.md`
  + `council_synthesis_verdict_canonical.json`. The canonical
  template instructs McLoop-executable output: phase headers in
  Slice C form, per-phase `- [ ]` checklist tasks. The schema
  has no `lineage` field. Duplo's `author_phase_plan` invokes
  this name and runs a deterministic markdown-format validator
  on the synthesized body before returning.
- `council_four_reauthor.orc` + `council_synthesizer_reauthor.md`
  + `council_synthesis_verdict_reauthor.json`. The re-author
  template is the previous (Slice C lineage-discipline)
  template. The schema keeps the `lineage` field. Duplo's
  `_invoke_council_for_reauthor` invokes this name and runs the
  Slice C lineage validator.
- The merged `council_four` name persists as a deprecated alias
  for one release with a `DeprecationWarning`; new callers must
  pick the explicit name.

Generalization: when one synthesizer state would serve multiple
consumer contracts, split the workflow rather than branch the
prompt. Single-workflow-with-mode-detection is the anti-pattern;
explicit workflow-per-contract is the structural enforcement.
The mode boundary is then visible at four layers (workflow file,
template path, schema path, caller name) rather than implicit in
"how did the synthesizer guess the contract from the brief
shape?"

References: 34d5509 / 044cafb / d528011 (the prior synthesizer-
output failures that established the JSON-vs-markdown lesson),
plus the Slice D smoke that surfaced the workflow-boundary
lesson.

## Per-call model authority is the wrong ownership boundary

A second smoke (Slice D follow-up after the workflow split landed)
surfaced a related but deeper structural lesson. The canonical
synthesizer, invoked once per phase across a multi-phase build,
produced `phase_001` for both the first AND second invocation,
then `phase_002` / `phase_003` / `phase_004` for the rest. The
re-author lineage validator caught the duplicate at `phase_001`
and McLoop hard-stopped on `lineage_invalid` — the closed loop
fired correctly — but the upstream cause was the synthesizer
authoring a phase identifier each time without context about
prior invocations.

The lesson: per-call model authority over persistent identifiers
is the wrong ownership boundary. The model proposes semantic
content; the model does NOT author execution/ledger facts.

Semantic content (the model's proper authority):

  - Task text. Acceptance criteria phrasing. Ordering suggestions.
    Risk notes. Rationale. Disagreements. Rejected options.
    Verdict prose.

Execution/ledger facts (Duplo's and Plan Ledger's authority,
NOT the model's):

  - Phase IDs (and any zero-padded numeric identifiers).
  - Lineage IDs and per-phase lineage transitions.
  - Append positions in PLAN.md.
  - Prior plan IDs (lineage validator's input).
  - Canonical object identity across re-authoring.
  - Ledger timestamps and event ordering.
  - Threshold-crossing state.
  - Re-authorization provenance (which crossing fired which run).

The pattern Duplo and Plan Ledger now follow:

  1. The model proposes semantic content.
  2. Duplo wraps it in deterministic structure (phase_id,
     lineage envelope, append location, source-phase
     provenance).
  3. The validator checks both the local fragment the model
     emitted and the accumulated document on disk.
  4. McLoop consumes only validated canonical structure.

Concretely for canonical-mode synthesis: Duplo computes
`required_phase_id` deterministically from the existing
PLAN.md (highest `phase_NNN` plus one, zero-padded; or
`phase_001` when no PLAN.md exists). The framer surfaces it
as a constraint in the council brief. The proposer template
instructs all four proposers to use it verbatim. The
synthesizer template instructs verbatim use as well. Duplo's
canonical-format validator enforces the constraint
post-synthesis: the synthesized phase header MUST use exactly
the supplied `required_phase_id`, all phase_ids in the body
MUST be unique, and they MUST be monotonically increasing.

This is the same lesson as the canonical/reauthor workflow
split: don't ask the model to maintain protocol state by
prose. The earlier split moved the workflow boundary from
prompt-implied to file-explicit; this move pushes the
identifier boundary from model-authored to runtime-computed.
Both are instances of the broader rule: state the runtime
needs to consume belongs to the runtime, not the model.

References: ee44ba5 + e5d72f5 (the canonical/reauthor split
that landed structural enforcement at the workflow boundary)
plus the Slice D follow-up smoke that produced the
phase-id-coordination lesson.

## References

The F1 / F2 / T1+T2 / F2.5a sequence in the orchestra Phase 2
work product is the template for "prose discipline accumulates,
structural enforcement is the answer." The relevant commits, in
order:

- 5c989e3 F1: tolerant JSON-object extraction for schema-backed
  model output
- 3be5d99 F2: stuck enum branch + judge_decision plumbing
- 4e59e84 F2 follow-up: iterate routes iterate verdict to
  propose, not review
- e983312 F2 follow-up: prior claims are hypotheses, not facts
  (T1 + T2)
- 07e28c7 F2.5a: structured criteria_compliance + accept-
  consistency invariant

The two Slice C smokes that produced the present lesson:

- 34d5509 council_synthesizer: pin output medium so the
  synthesizer doesn't try to write files
- 044cafb council_synthesizer: declare lineage in JSON sidecar,
  drop HTML-comment protocol (orchestra side)
- d528011 reauthor: consume JSON lineage sidecar; drop HTML-
  comment parser (duplo side)

Codex's framing of the moment to make the structural move, from
the Slice C round-4 review:

> This is no longer a wording problem. The validator is proving
> the contract is too easy for the synthesizer to violate while
> still producing plausible prose. More template warnings would
> just increase prompt surface area without reducing the
> structural ambiguity.
