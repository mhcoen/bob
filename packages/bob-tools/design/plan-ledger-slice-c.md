# Plan Ledger Slice C: Duplo Re-Author Mode

## Purpose

Slices A and B produce the ledger and detect threshold crossings.
Slice C is the response: when a threshold crossing warrants
re-authoring, Duplo consumes the ledger plus current PLAN.md and
produces an updated PLAN.md preserving lineage from old phases to
new.

This is where Plan Ledger stops observing and starts acting.

## Position relative to existing work

Tonight's Duplo→council integration (commit 429e0e9) wired
`--use-council` to invoke `council_four.orc` for plan authoring.
That handles the canonical mode (fresh authoring from reference
material, no prior plan).

Slice C extends that integration to handle the **repair mode**:
existing PLAN.md, ledger evidence that part of the plan is
invalidated, re-author with both as input.

The council workflow already accepts `ledger_slice` and
`design_context` as framer inputs — those slots were wired empty
in the canonical path, anticipating this integration. Slice C
populates them.

## Scope boundary

**In scope:**

- Duplo gains a re-author entry point that takes
  (current PLAN.md, ledger directory or ledger slice, triggering
  threshold crossing) and produces a new PLAN.md.
- Council workflow receives `ledger_slice` (events + projected
  state since last re-author) and `design_context` (extracted
  rationale from prior PLAN.md and any
  design_reasoning_recorded events).
- Output preserves lineage: every new phase ID either matches an
  old one or carries explicit supersession metadata.
- Slice C emits one or more lifecycle events
  (`phase_superseded` / `phase_split` / `phase_merged` /
  `phase_abandoned`) BEFORE the `plan_reauthored` event so a
  projector replaying the log encounters lineage changes with
  their full payload before the meta-event that ties them
  together. `plan_reauthored.ledger_slice_event_ids` references
  the just-appended lifecycle events plus the triggering
  threshold_crossed.

`record_crossings(storage, crossings)` already shipped as Slice B
part 2 (commits 873eec2 and 9c84d8d on bob-tools). Slice C
consumes its outputs.

**Out of scope:**

- McLoop integration. Slice D pauses McLoop on threshold
  crossing and invokes Duplo re-author. Slice C just ships the
  re-author capability; it does not yet auto-fire from McLoop.
- Auto-detect for repair mode. The user (or McLoop in Slice D)
  explicitly invokes re-author with the triggering crossing.
  Slice C does not poll for crossings.
- Concurrent re-authoring. Slice C handles one re-author at a
  time; locking and coordination across concurrent re-authors
  is a future concern.

## API shape

### CLI

    duplo reauthor \
      --plan PATH/TO/PLAN.md \
      --ledger-dir PATH/TO/.duplo/ledger \
      --crossing-event-id <event_id> \
      [--council-config PATH] \
      [--out PATH/TO/UPDATED_PLAN.md]

Defaults:
- `--plan` defaults to `./PLAN.md`.
- `--ledger-dir` defaults to `./.duplo/ledger` (Slice C decides the
  canonical location; subject to confirmation).
- `--crossing-event-id` is required: identifies which
  threshold_crossed event triggered this re-author. Without it,
  Slice C does not run (no implicit re-authoring).
- `--out` defaults to overwriting `--plan`.

### Python API

    from duplo.reauthor import reauthor_plan

    new_plan_path = reauthor_plan(
        plan_path: Path,
        ledger_dir: Path,
        crossing_event_id: str,
        out_path: Path | None = None,
        council_config_path: Path | None = None,
    ) -> Path

Returns the path to the written plan. Side effect: writes a
`plan_reauthored` event to the ledger.

## Internal flow

1. **Read inputs.** Load PLAN.md text, Storage.read_all() events,
   project() to get current PlanState. Load the
   threshold_crossed event identified by `crossing_event_id`.
   Verify the event exists and is type=threshold_crossed.
   `severity` and `recommended_action` are NOT in the persisted
   payload (Slice A schema is `{rule_id, triggering_event_ids,
   summary}`); the consumer derives them from `rule_id` via the
   constant mapping defined in `bob_tools.ledger.thresholds`.

2. **Build ledger_slice.** Events since the previous
   plan_reauthored event (or since the beginning if this is the
   first re-author). The slice includes both lifecycle events
   (phase_started, phase_abandoned, etc.) and reasoning events
   (design_reasoning_recorded, assumption_falsified,
   invariant_declared). Format: a structured summary, not the
   raw JSONL — the council framer needs context, not 500 lines
   of envelope.

3. **Build design_context.** Extract from current PLAN.md the
   rationale, constraints, and rejected approaches that informed
   the existing plan. Two sources:
   (a) `design_reasoning_recorded` events in the ledger
       (authoritative for prior decisions).
   (b) Free-text inspection of PLAN.md for rationale sections,
       "decisions captured" blocks, etc.
   (a) is structured and trustworthy; (b) is best-effort. Slice C
   prioritizes (a) and falls back to (b) for plans authored
   before Slice C existed.

4. **Invoke council_four.** Pass:
   - `state` = PlanState summary (not raw JSON; framer-readable
     summary of phases and their status).
   - `question` = "Re-author the plan in light of triggering
     event: {crossing_summary}. Preserve lineage where phases
     remain valid; supersede or split phases that the ledger
     evidence has invalidated."
   - `ledger_slice` = the structured summary from step 2.
   - `design_context` = the structured summary from step 3.

5. **Receive synthesized plan.** Council outputs the plan via
   the synthesizer's `plan` artifact. Slice C reads it, writes
   to `out_path`.

6. **Emit lifecycle events FIRST (option (a) ordering).** Compute
   the lineage diff from the synthesizer's metadata
   (`supersedes:`, `split_from:`, `merge_from:` on each new
   phase header) plus the elision check (any prior phase id with
   no successor claim from any new phase). Emit
   `phase_superseded`, `phase_split`, `phase_merged`, and
   `phase_abandoned` events as appropriate, in deterministic
   order (sorted by the phase id they target). Each lifecycle
   event captures the change in its own payload; the projector
   can replay them independently.

7. **Emit plan_reauthored event.** Append to the ledger per the
   Slice A schema:
   - `from_plan_commit` = git HEAD before the re-author (or null
     if the consumer is not in a git checkout).
   - `to_plan_commit` = git HEAD after the re-author (or null).
   - `ledger_slice_event_ids` = the lifecycle event_ids emitted
     in step 6 plus the triggering `crossing_event_id`.
   - `trigger_event_id` = `crossing_event_id`.
   - `council_run_id` = orchestra run id from the council
     invocation (or null on a manual / mocked run).

   The Slice C design previously listed `prior_plan_sha256`,
   `new_plan_sha256`, and `lineage_changes` here; those were
   design-doc artifacts that drifted from the actual Slice A
   schema. Lineage changes are captured in the preceding
   lifecycle events, not in `plan_reauthored` itself.

## Council workflow consumption

The council_four.orc workflow already accepts `ledger_slice` and
`design_context` as framer inputs. Slice C populates them with
real content. The framer template should be reviewed once Slice C
provides realistic inputs to make sure its handling of populated
vs empty slots is correct.

Likely refinement: framer template might need an explicit
"this is a re-authoring scenario" branch. Currently the template
treats empty `ledger_slice` and `design_context` as fresh-author
mode. Slice C tests should verify that populated slots correctly
shift the framer (and downstream proposers) into re-author
posture. If the current framer template handles populated slots
sensibly, no change needed; if it does not, the framer template
gets a small update as part of Slice C.

## Lineage preservation

The hardest part of Slice C is making sure phase IDs persist
across re-authorings. If phase_a in the original plan stays valid
in the new plan, its ID must not change — otherwise Slice A's
projector loses continuity (lineage pointers would target
non-existent phases).

Two enforcement paths:

(a) Synthesizer-side discipline. The synthesizer template gets
    explicit instructions to preserve phase IDs where possible
    and to use supersession/split/merge metadata when changing
    them. Reliability depends on model compliance.

(b) Slice C-side validation. After receiving the synthesized
    plan, Slice C parses it, extracts new phase IDs, and rejects
    or repairs the output if it violates lineage rules (new IDs
    appearing without supersession metadata pointing at the IDs
    they replace).

Recommend both. (a) sets the model's expectation; (b) catches
violations the model produces anyway. (b) requires phase IDs to
be syntactically extractable from the plan, which means the plan
format must include phase ID metadata in a parseable way.

This is a meaningful design question: how does PLAN.md encode
phase IDs?

Three options:

(i) Markdown headers with embedded IDs:
    `## Phase phase_001: <name>`
(ii) YAML frontmatter or a structured sidecar file
     (PLAN.lock.json):
     `{ phases: [{id: phase_001, name: ...}] }`
(iii) Parser inference from header text + heuristics, no
      explicit ID syntax.

(iii) is fragile. (i) is human-friendly but couples ID syntax
to markdown formatting. (ii) is structured but doubles the
plan-of-record artifacts.

Recommend (i) for Slice C: explicit phase ID prefix in markdown
headers, parsed via a simple regex. PLAN.lock.json or similar
structured sidecar can come later if needed.

## Tests

### Unit tests for reauthor_plan

- Triggering event lookup: missing event_id → raises clearly.
  Wrong event type (not threshold_crossed) → raises clearly.
- Ledger slice extraction: events since prior plan_reauthored
  event are included; events before it are not. First re-author
  includes all events.
- Design context extraction: design_reasoning_recorded events
  for current phases are included. Falsified assumptions and
  declared invariants since prior re-author are included.
- Council invocation: mock orchestra.api.run_workflow to verify
  the framer state slots are populated correctly.
- Plan output: synthesized plan written to out_path; PLAN.md not
  overwritten if --out is set.
- Plan_reauthored event emitted: ledger contains the event with
  correct fields; sha256 matches.
- Lineage change detection: old plan with phase_a, new plan with
  phase_a (preserved) + phase_b superseded by phase_b_v2 → the
  emitted plan_reauthored event records the supersession.

### Integration tests

- End-to-end: write a fixture ledger with a triggering crossing,
  run reauthor_plan against a fixture PLAN.md, mock the council
  to return a known synthesized plan, verify the output is
  correct, the ledger has new plan_reauthored event, and any
  lineage changes are recorded.
- Real council invocation: gated like the canonical-mode
  council smoke test, manual run only. Not in CI.

### Lineage-validation tests

- Synthesizer output preserving phase IDs: passes validation.
- Synthesizer output with new phase IDs and explicit
  supersession metadata: passes.
- Synthesizer output with new phase IDs and NO supersession
  metadata: fails validation; Slice C either repairs or raises.

Estimated test count: 20-25.

## Files to create

    duplo/reauthor.py              new
    duplo/reauthor_lineage.py      new (or merged into reauthor.py)
    duplo/tests/test_reauthor.py   new

Plus updates to:

    duplo/main.py                  add `reauthor` subcommand
    orchestra/workflows/templates/council_synthesizer.md
                                   add lineage-preservation
                                   instructions
    bob_tools/ledger/__init__.py   re-export record_crossings
                                   if it lands here
    bob_tools/ledger/storage.py    add record_crossings helper
                                   (deferred from Slice B)

## Quality gates

ruff, mypy --strict, pytest. Same discipline as Slices A and B.
Real-API council smoke test at the end (manual, not CI), per the
existing canonical-mode pattern.

## Open questions for Codex review

1. Phase ID encoding in PLAN.md: option (i) markdown headers
   with embedded IDs. Concur, or argue for (ii) structured
   sidecar?

2. Lineage preservation enforcement: both (a) synthesizer-side
   discipline AND (b) Slice C-side validation. Acceptable, or
   one sufficient?

3. ledger_slice format: structured summary (not raw JSONL).
   What's the right shape for the framer to consume? My read:
   a markdown-formatted brief listing events grouped by phase
   with key fields, similar to how Phase 2's REPORT.md
   addenda were structured. Confirm or push back.

4. design_context fallback: when prior plan was authored before
   Slice C existed (no design_reasoning_recorded events), Slice C
   inspects PLAN.md text for rationale. How robust does this
   need to be? Best-effort string extraction, or formal parser?
   My read: best-effort for Slice C; formal parser only if
   evidence shows it matters.

5. record_crossings landing: roll into Slice C, or land
   separately as Slice B part 2 first? Codex previously
   approved deferring from Slice B; Slice C is the natural
   consumer, so rolling in seems clean. Confirm.
