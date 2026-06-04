# Notes

## Follow-ups

### Corrupt prior PLAN.md cannot self-heal through reauthor — 2026-05-10

duplo/duplo/plan_document.py:parse_plan is strict about the
canonical PLAN.md structure (H1 envelope + H2 phase header pairs as
units; no intervening text; one H2 per H1; no embedded fenced
verdict JSON inside unit bodies). Prior plans that violate the
contract — whether from earlier reauthor passes that ran before
the structural-ownership fix landed, manual edits, or any other
source — fail at the parser boundary.

Cause: the new parser enforces invariants the old H2-only parser
silently tolerated.

Consequence: re-running mcloop on a corrupt PLAN.md raises
ReauthorError("prior PLAN.md ... cannot be parsed as a canonical
plan document: ...") at the start of reauthor_plan. The reauthor
flow does not attempt to rewrite the corrupt prior; it pauses for
manual intervention rather than amplifying the corruption across
another pass.

Recovery options:

  1. Roll back to a known-good PLAN.md commit. The fastest path
     when one is available. Example for the fswatch-run-smoke
     fixture: ``git -C /Users/mhcoen/proj/experiments checkout
     <commit-with-clean-PLAN> -- fswatch-run-smoke/PLAN.md``.
  2. Manually rewrite PLAN.md to canonical structure. Each phase
     becomes one unit:

         # <project> — Phase N: <human title>
         ## Phase phase_NNN: <human title>

         <body>

     No intervening text between the H1 and H2; H1 ordinals are
     contiguous starting at 0; phase_id matches /[A-Za-z0-9_]+/;
     verdict JSON does not appear anywhere in unit bodies.
  3. (Future) Run a one-shot duplo CLI command that rewrites a
     structurally-corrupt plan into canonical form against a
     supplied lineage. Surfaced as a separate todo below.

### [todo] duplo plan repair CLI — 2026-05-10

A ``duplo plan repair --in PLAN.md --out PLAN.md`` command would
take a structurally-corrupt prior plan plus a target lineage (or
an inferred lineage from the existing H2 ids) and produce a
canonical PLAN.md. Out of scope for the structural-ownership fix
that introduced plan_document; surfaced here as a follow-up so a
future change picks it up.

### [todo] Migrate mcloop's checklist.py check-off path to plan_document — 2026-05-10

Duplo's plan_document module owns the canonical PLAN.md structural
contract (H1 envelope + H2 phase header pairs as units, deterministic
render, sanitize / validate_structure invariants). mcloop's
checklist.py still walks PLAN.md with its own H2-only regex
(STAGE_RE in mcloop/checklist.py line 12) when checking off completed
tasks. The duplication is benign as long as both regexes agree, but
the canonical structural ownership lives in plan_document and
checklist.py should consume it rather than re-implement structural
parsing.

Out of scope for the plan-document fix that introduced
plan_document. Surface here as a follow-up so a future change picks
it up. Touch points:

  - mcloop/checklist.py L~12: STAGE_RE definition.
  - mcloop callers of STAGE_RE (parse_plan, find_next_task, etc.).
  - Add a duplo dependency to mcloop's existing pyproject extras
    (or import from a shared vendored module) before swapping.

The migration is structurally safe (plan_document's parser is a
strict superset of STAGE_RE's matches: anything STAGE_RE accepted
either parses cleanly into a Plan or raises ParseError on a
genuinely corrupt structure). Schedule alongside the next time
mcloop touches checklist.py.

## Observations

### [2] [T-000002] scope_include features can be dropped or stranded in the scaffold by the roadmap LLM — 2026-06-04

generate_roadmap delegates phase allocation to the LLM, which may omit
a required scope_include feature entirely or list it only under Phase 0
(scaffold-only, no feature tasks). Added _reconcile_scope_into_roadmap
to roadmap.py: after parsing, any scope_include name not already in a
build phase (non-scaffold, case-insensitive) is appended to the last
build phase, or a synthesized build phase if the roadmap is
scaffold-only. This mirrors extractor._reconcile_scope_include, which
guarantees the same names exist as Features; together they ensure a
guaranteed user scope item always reaches a buildable phase. The fix
appends all missing scope items to a single (last) build phase rather
than distributing them, to keep allocation deterministic.

### [9.9] [T-000791] `run_role` reports a converged validation-gated run as CAPPED; the adapter now decides fail-closed from the gate, not the label — 2026-06-04

The first true end-to-end run of the iterative authoring path (real
Orchestra executor, only the proposer/reviewer/judge LLM leaves mocked)
surfaced a disposition bug that every prior task missed because they all
mocked at the `orchestra.run_role` boundary and hand-built
`IterativeDesignResult(termination="CONVERGED")`.

`run_role`'s `_derive_termination` (orchestra/api/transcript.py) classifies
a run purely from the last `transition` record's `(outcome, target)`:
`outcome == "done"` -> CONVERGED, any other `outcome` into `done` -> CAPPED.
But `plan_author.orc` reaches the `done` terminal through the `validate`
transform state, and a transform's outcome is ALWAYS the generic
`complete` (see `_executor_transition._derive_outcome`). Both the success
transition (`on complete when validation_ok == true => done`) and the
cap-exhausted fallthrough (`on complete => done`) therefore emit an
identical `(complete, done)` pair. `_derive_termination` cannot tell them
apart, so it labels EVERY terminating plan_author run CAPPED -- including a
run whose final draft passed the gate. Empirically: a (WRONG-then-RIGHT)
convergence returns `termination=CAPPED, rounds_completed=2,
final_artifact=<the valid RIGHT body>`.

Consequence before the fix: `run_plan_author` trusted the label and raised
`PlanAuthorCappedError` on every run, so `generate_phase_plan` (iterative
default since T-000790) could NEVER author a plan -- a latent
total-failure of the default path.

Fix (duplo-local, in `plan_author_adapter.run_plan_author`): on a CAPPED
label, do not trust it -- re-run the gate's own check,
`typed_plan_from_synthesizer_text(final_artifact, required_phase_id)`
(exactly what `validate_plan_body` runs), on the final body. Passes -> that
body IS the converged plan, return it. Never passed -> true cap, fail
closed with `PlanAuthorCappedError`. CONVERGED (if `run_role` ever emits it)
and ERROR are unchanged, so the adapter's existing unit tests (which mock
`run_role`) keep their fixtures. This re-validation is also exactly the
adapter's documented contract ("a body that never passes canonical
validation within max_rounds is never returned for PLAN.md"), so it is
defense-in-depth rather than a workaround bolted on.

Kept in duplo deliberately: Orchestra never imports duplo, and duplo is the
only consumer of a validation-gated workflow, so the engine fix would touch
a shared component for a single consumer. A cleaner root-cause fix, if a
second validation-gated consumer ever appears, is for `run_role` to refine
the disposition from the final `validation_ok` artifact value (already in
`result.artifacts`): `true` -> CONVERGED, `false` -> CAPPED. Surfaced here
as a follow-up.

### [9.8] [T-000790] `generate_phase_plan` default route is now the iterative adapter; `--use-council`/`is_enabled()` is orphaned from this path — 2026-06-04

`planner.generate_phase_plan` no longer consults `council.is_enabled()`
(`DUPLO_USE_COUNCIL`). Iterative authoring via
`plan_author_adapter.run_plan_author` is the unconditional default; the
converged body flows through the unchanged
`council.typed_plan_from_synthesizer_text` -> `save_plan` tail with no
change to `save_plan`. Council is reachable only through the explicit
`escalate_to_council: bool = False` keyword on `generate_phase_plan`,
which routes to `council.author_phase_plan`. A CAPPED authoring run
raises `PlanAuthorCappedError` (not swallowed here), so PLAN.md is never
written with an unvalidated body.

Worth revisiting: `main.py` still wires the `--use-council` / `--no-council`
/ `--council-config` CLI flags to `council.set_enabled()` /
`set_config_path()`, and `council.is_enabled()` still reads the env var,
but NOTHING in the normal authoring path consults `is_enabled()` anymore.
So `duplo --use-council` is currently a silent no-op for phase authoring
(it only matters to any future explicit escalation/experiment caller that
passes `escalate_to_council=True`, plus council's preflight/audit/reauthor
value). Per the task NOTE the env var was intentionally NOT inverted, and
the env-var path was intentionally demoted; the dangling CLI flag is a
follow-up decision for the user (wire it to `escalate_to_council`, or drop
the flag). `pipeline.py` calls `generate_phase_plan` without
`escalate_to_council`, so the pipeline is always iterative now.

### [9.7] [T-000789] `run_plan_author` adapter: fail-closed on CAPPED; `final_artifact` needs a typed local for mypy — 2026-06-04

Added `duplo/plan_author_adapter.py` (`run_plan_author`) as the PLAN.md
authoring counterpart to `duplo.design`. It builds `query` via
`council._build_state_text(prompt=..., system=...)` (system directive
folded into the query text) and `history` from a compact
`PriorPhaseContext` (prior phase ids/titles, completed-phase summaries,
files created, prior validation failures) — never the current phase's
source/spec, which stays in `query`. It dispatches
`orchestra.run_role("plan_author", ..., required_phase_id=...,
registry_customizer=register_validate_plan_body(required_phase_id))`.

Termination translation, worth carrying forward:

  - CONVERGED -> return `result.final_artifact` (the converged proposal
    body).
  - CAPPED -> FAIL CLOSED: raise `PlanAuthorCappedError`, return no body
    for PLAN.md. CAPPED is T-000786's validation-cap disposition (body
    never validated within `max_rounds`); the best-so-far is attached to
    the exception for audit ONLY and must never be used as a plan. This
    is the key behavioral difference from `duplo.design`, which tolerates
    CAPPED and returns its best-so-far body.
  - ERROR -> raise `PlanAuthorRunError` carrying the `ErrorRecord` and
    the on-disk transcript path. A pre-run `WorkflowApiError` (role/config
    missing, no transcript) is also surfaced as `PlanAuthorRunError` with
    kind `config_missing` and an empty transcript path.

mypy gotcha (same as `duplo.design`): orchestra ships no `py.typed`
marker, so `result.final_artifact` is seen as `Any`. Returning it
directly trips `[no-any-return]`; assign to a `str`-annotated local
(`converged_body: str = result.final_artifact`) first. `test_mypy_clean`
caught this in the first run of the full suite.

### [9.6] [T-000788] `plan_author` compound role: leaf keys are workflow role names; proposer/judge may share an actor — 2026-06-04

Defined the duplo-owned `plan_author` compound role in
`duplo/plan_author_role.py` and emit it into the project-local
`.orchestra/config.json` `role_bindings` table from `duplo.init`
(`_ORCHESTRA_COUNCIL_CONFIG`). Three points worth carrying forward:

  - Leaf-binding keys must be the WORKFLOW's role names, not the
    informal labels in the task. `plan_author.orc` declares roles
    `proposer`, `reviewer`, `judge_role`; the config binds
    `judge_role` (NOT `judge`). `_resolve_workflow_role_bindings`
    resolves the workflow's `state.role` names against the leaf table,
    so a key named `judge` would leave `judge_role` unbound.

  - `_validate_design_distinct_actors` is gated on `role_name ==
    "design"` only. `plan_author` is therefore NOT required to have
    pairwise-distinct actors: proposer=opus and judge_role=opus
    deliberately collapse to the same `(claude_code_text, opus)`
    actor. Only the reviewer is bound to a distinct actor
    (`codex` -> `(codex_text, gpt-5-codex)`) so the critique is
    independent of the authoring/judging model -- "distinct-enough",
    not fully distinct.

  - Extension point A is the criteria path
    (`CompoundRoleBinding.criteria` -> dispatch `derived_criteria`
    -> Executor); extension point B is the registry_customizer
    transform-registration path (T-000787). The `plan_author`
    criteria encode ONLY judgment-level rules (granularity 5-15,
    [BATCH]/[USER]/[AUTO], [feat:]/[fix:]); the hard structural rules
    are left to the `validate_plan_body` gate so they are checked
    mechanically, not re-litigated by the judge.

### [9.5] [T-000787] `validate_plan_body` enforces "no `## Bugs`" via `typed_plan_from_synthesizer_text`, not in the transform — 2026-06-04

The `validate_plan_body` transform is intentionally thin: it delegates
to `council.typed_plan_from_synthesizer_text` and maps
`PlanSyntaxError`/`PlanValidationError` to `validation_ok=false`. But
that function previously SILENTLY DROPPED a `## Bugs` section (it builds
the constructed plan with `bugs=None`), so a `## Bugs` body validated ok
— contradicting T-000787's required test and T-000788's contract that the
validation transform enforces "no `## Bugs`, no project H1". Fix: the
canonical chokepoint `typed_plan_from_synthesizer_text` now raises
`PlanValidationError` when `parsed.bugs is not None`, so the gate fails
and the synthesizer gets named feedback to remove it rather than losing
the content silently.

Blast radius checked: only `council.py` (council `author_phase_plan`) and
`planner.py:618` (non-council `generate_phase_plan`) call this function;
`reauthor.py` does NOT. No existing test feeds a `## Bugs` body to it
expecting success — `planner._strip_bugs_section`/`save_plan` handle bugs
on the persistence side, never via this function. Project-H1 rejection is
NOT yet added here (T-000787 only requires `## Bugs`); T-000788 owns the
H1 rule and can extend this same chokepoint.

### [9.1] [T-000786] plan_author validation state reads only `proposal`; `required_phase_id` reaches the transform via the registration closure — 2026-06-04

The task text says the validation state's `validate_plan_body` transform
"reads the `proposal` body and `required_phase_id`". Orchestra's loader
forbids that literally: `_validate_transform_state`
(`orchestra/loader/validator.py`) requires every transform read to be a
declared *artifact*, and external inputs are explicitly rejected ("External
inputs are not admissible reads for transforms in Slice B"). Declaring
`required_phase_id` as both an `external_input` and an `artifact` is also
rejected (Phase 4 name-uniqueness). Empirically, a fork whose validate
state reads `proposal, required_phase_id` fails `load_workflow` with
"input 'required_phase_id' is not a declared artifact".

Resolution (matches T-000787/T-000789's design): `required_phase_id` stays
an `external_input` so `run_role(..., required_phase_id=...)` accepts it,
but the validation state reads only `proposal`. The transform obtains
`required_phase_id` via the closure duplo builds when it registers
`validate_plan_body` through `run_role`'s `registry_customizer` hook
(T-000787: `typed_plan_from_synthesizer_text(body, required_phase_id=...)`,
where the kwarg is bound at registration time, not read from the store).
So the transform's workflow-level `input_schema` is `{proposal: str}`. This
keeps the fork fully loadable today (verified with a stub transform in
`tests/test_plan_author_workflow.py`) and leaves `required_phase_id`
declared but unread at the workflow level, which Orchestra permits.

### [9.1] [T-000786] `validation_ok` is a `json` artifact, not a `bool` type — 2026-06-04

Orchestra's core registry registers artifact types `text, json, messages,
prompt, schema, document` only; there is no `bool` artifact type. A
transform output declared `bool` maps to the `json` artifact type
(`transforms._TYPE_TO_ARTIFACT_TYPE`). So the workflow declares `artifact
validation_ok json` (with `initial false`) while the transform's
`output_schema` uses Python `bool`; the guard `validation_ok == true`
compares the stored bool against the `true` literal. `validation_feedback`
is `text` (transform output `str`).

### [9.1] [T-000786] workflow assets ship via package-data and must travel together — 2026-06-04

`plan_author.orc` lives at `duplo/workflows/` with its `templates/` and
`schemas/` siblings. Orchestra resolves `prompt template "..."` and
`schema "..."` paths relative to the `.orc` file's directory, so the three
must deploy together to `<project>/.orchestra/workflows/`. Added a
`[tool.setuptools.package-data]` block so the non-`.py` assets ship in the
built package (editable installs read the source tree directly, so tests
passed before this was added; a wheel would not have included them).

### [4.7] [T-000026] call_log test coverage already broad; only call_site thread-through was missing — 2026-05-28

Auditing the requested assertions against existing tests, nearly all
were already covered: run_id shape + lazy run-dir creation
(`tests/test_call_log.py`), `query`/`query_with_images` full-fidelity
records on success/error/timeout (`TestQueryCallLogging` in
`tests/test_claude_cli.py`), stream-json token parsing
(`TestStreamJsonUsage`), and `duplo logs` aggregation
(`tests/test_logs.py`). The only gap was asserting the `call_site`
label threads from `generate_phase_plan` into `query`; added
`TestGeneratePhasePlanCallSite` in `tests/test_planner.py` (label is
`phase_plan:<required_phase_id>`, where the id comes from
`council.compute_required_phase_id` over the target_dir PLAN.md, so it
is `phase_plan:phase_001` against an empty dir and tracks `highest+1`
when prior phase headers exist).

Pre-existing/unrelated failure observed in the same full run:
`packages/orchestra/tests/test_fan_out_executor.py::test_resume_open_fan_out_relaunches_only_incomplete_children`
(advise_c minted attempt_seq 2 instead of 1 on first entry). It is in
orchestra's fan-out resume path, untouched by this duplo-only change,
and appears order/seed-sensitive under pytest-randomly — a sibling of
the orchestra failure noted in the [4.2] session.

### [4.6] [T-000025] `duplo logs` run-log summary helper — 2026-05-28

New read-only module `duplo/logs.py` plus a `duplo logs [RUN_ID]`
subcommand (wired in `main.py` before the `reauthor` block) reads
`.duplo/logs/<run_id>/calls.jsonl` and prints a per-call table —
`call_site` in record order, model, path, duration, and token counts —
closed by a `TOTAL` line. With no `RUN_ID` it summarizes the latest run
(run ids are timestamp-prefixed, so the lexical max is newest); `--dir`
points at a project other than cwd.

Design decisions worth carrying forward:

  - The `cache` column **combines** `cache_creation_input_tokens` +
    `cache_read_input_tokens` into one bucket, matching the
    input/cache/output framing of the task. If a future quota analysis
    needs creation vs. read split, the underlying record still carries
    both fields — only the report collapses them.
  - Council pointer rows have no `model`/`duration`/`usage` (the real
    calls live in the referenced orchestra run, per [4.5]'s deliberate
    fidelity asymmetry), so they render as `-` and contribute zero to
    the totals. The orchestra `run_id` is appended to the `call_site`
    cell (`phase_plan:phase_002 -> orc-run-xyz`) so a reader can pivot
    to the orchestra transcript for the underlying per-actor calls.
    Consequence: token totals reflect only the legacy path; a
    council-heavy run will under-report true consumption from this view
    alone.
  - `load_records` skips blank and malformed JSON lines rather than
    aborting, so a partially written log (e.g. a crashed run) still
    summarizes.

### [4.5] [T-000024] council phases indexed in the duplo run log by reference — 2026-05-28

`council.author_phase_plan` now appends one `call_log` record per
council-authored phase via `call_log.log_council_phase`, so a single
`.duplo/logs/<run_id>/calls.jsonl` is the complete index of every LLM
call regardless of path. The record is a pointer, not a captured
round-trip: `call_site` (`f"phase_plan:{required_phase_id}"`, the same
label the legacy `query` path uses for a phase), `path="council"`,
`orchestra_run_id` (`result.run_id`), `transcript_path`
(`result.log_path`), and `extra.audit_dir` (the `.duplo/audits/council/<run_id>`
dir holding the human-readable proposals/brief/plan).

Two asymmetries worth carrying forward:

  - Fidelity is captured differently per path by design. Legacy
    `query` calls are recorded inline at full fidelity (prompt,
    system, response, usage). The council route's per-actor calls
    (framer + 4 proposers + synthesizer, ~6 calls) are captured by
    orchestra inside its own run dir; duplo records a single pointer
    rather than duplicating that transcript. So one council phase = one
    duplo record but six underlying orchestra calls. A reader must
    follow `transcript_path` to enumerate the per-actor calls.

  - The council pointer uses orchestra's `WorkflowRunResult.log_path`
    as `transcript_path`. That is the JSONL workflow log, not the
    `IterativeDesignResult.transcript_path` field (which only exists on
    the `run_role`/design path, not the `run_workflow`/council path).
    Token usage is not surfaced on the duplo-side council record; it
    lives in the orchestra log if at all.

Legacy `log_call` records now also carry an explicit `path="legacy"`
field so the index is uniform across both routes.

### [4.4] [T-000023] call_site labels threaded into query/query_with_images — 2026-05-28

Threaded `call_site` into every duplo generation site that calls the legacy
`query`/`query_with_images` path: `extract_features` -> `"extract_features"`,
`generate_roadmap` -> `"generate_roadmap"`, `generate_phase_plan` ->
`f"phase_plan:{required_phase_id}"` (the deterministic runtime phase id, e.g.
`phase_plan:phase_001`), `extract_verification_cases` -> `"verification_cases"`,
and `investigator.investigate` -> `"investigate"` (both the text-only `query`
and the `query_with_images` branch).

Design extraction (`design_extractor.extract_design`) is the one site in the
task list that does NOT reach `query`: it routes through
`duplo.design.run_iterative_design` -> `orchestra.run_role("design", ...)`,
which is a separate LLM call path that does not write `call_log` records and
takes no `call_site`. There is therefore no `query` call to label for
`"extract_design"`; threading a label there would have no effect on the
call_log. If design-loop calls ever need to appear in the call_log, orchestra
itself would have to emit records — out of scope for this task.

Note also a second `query` site in `planner.py` (`generate_next_phase_plan`,
the feedback-driven next-phase generator) that the task did not enumerate; it
was left unlabeled (`call_site=""`) since only `generate_phase_plan` was named.

### [4.3] [T-000022] claude_cli now emits stream-json and records token usage — 2026-05-28

Both `claude -p` invocations in `duplo/claude_cli.py` (`_query_once` and
`_query_with_images_once`) now pass `--output-format stream-json --verbose
--include-partial-messages` (collected in `_STREAM_JSON_FLAGS`). Stdout is
JSONL and is run through the new `_parse_stream_json(stdout)` helper, which
returns `(response_text, usage)`:

  - `usage` sums the four token fields across turns: `input_tokens` /
    `cache_creation_input_tokens` / `cache_read_input_tokens` come from
    `message_start` events' `message.usage`; `output_tokens` comes from
    `message_delta` events' `usage`. `message_start.output_tokens` (the
    initial =1/=2 partial) is deliberately ignored to avoid double-count.
  - Response text is taken from the terminal `result` event's `result`
    field when present, otherwise reconstructed from streamed `text_delta`
    chunks.

`--include-partial-messages` is what surfaces `message_start`/`message_delta`
at all; without it the CLI emits only aggregated `assistant`/`result` typed
messages. This matches the flag set mcloop's runner uses (see
`packages/mcloop/mcloop/runner.py:857`).

Graceful fallback: if no line parses as a JSON object, `_parse_stream_json`
returns the raw stdout stripped with `usage=None`, so a format surprise
records the call without token counts rather than failing it. `usage` is
also `None` when JSON parsed but carried no token counts; `call_log.log_call`
omits the `usage` key entirely in that case (`if usage:`). The existing
plain-text test fixtures exercise this fallback path, which is why they kept
passing unchanged.

Decision worth revisiting: usage is summed across ALL turns in the stream,
giving total consumption for an agentic call (matters for `query_with_images`
which runs the Read tool, possibly multiple turns). If per-turn breakdown is
ever needed, the parser would have to return a list instead of a single sum.

### [BUGS.md#5] claude_cli.py retry logic already in place — 2026-04-19

BUGS.md entry 5 claimed `query()` / `query_with_images()` in
`duplo/claude_cli.py` make a single attempt with no retry. In reality
the retry behavior the task describes is fully implemented:
`_MAX_ATTEMPTS = 3`, `_RETRY_SLEEP_SECONDS = 5.0`, a shared
`_with_retry(...)` wrapper at `duplo/claude_cli.py:37-57` that catches
`ClaudeCliError`, sleeps 5s between attempts, and writes
`"claude CLI attempt N/3 timed out, retrying..."` to stderr. Both
`query()` and `query_with_images()` route through `_with_retry`. Tests
covering the two required scenarios already exist:
`test_retries_on_failure_then_succeeds` /
`test_raises_after_three_failed_attempts` (for `query()`) and
`test_retries_on_timeout_then_succeeds` /
`test_raises_after_three_timeouts` (for `query_with_images()`) in
`tests/test_claude_cli.py`. The feature was introduced by commit
`18df8b8` (see bottom of this file). BUGS.md entry 5 is stale and
should be cleared.

### [BUGS.md] main.py split into pipeline.py + status.py — 2026-04-19

Split `duplo/main.py` (was 2345 lines / 81KB) into three modules:
`duplo/main.py` (CLI dispatch + signal/crash setup, ~376 lines),
`duplo/pipeline.py` (`_subsequent_run` orchestration + `_fix_mode` and
helpers, ~1801 lines), and `duplo/status.py` (display/progress helpers
including `UpdateSummary`, `_partition_features`,
`_print_feature_status`, `_print_status`, `_print_summary`,
`_plan_is_complete`, `_plan_has_unchecked_tasks`, `_plan_ready`,
`_current_phase_content`).

main.py now calls `_pipeline._fix_mode(args)` /
`_pipeline._subsequent_run()` via the imported module rather than via
`from duplo.pipeline import _fix_mode, _subsequent_run`. This keeps
`monkeypatch.setattr("duplo.pipeline._fix_mode", ...)` working — a
`from … import` would have rebound the name in main and broken those
patches.

Legacy re-exports kept in main.py for backward-compat patch targets:
`select_features`, `select_issues` (from selector),
`save_reference_screenshots` (from screenshotter), `_check_migration`
(from migration). These are no longer called by main itself but are
patched by ~70 existing tests via `patch("duplo.main.X")`.

`_partition_features` exists in both status.py (canonical) and
pipeline.py (thin wrapper that delegates to status). The wrapper
exists so pipeline-internal callers can keep importing it from
pipeline without a circular import.

Bulk test-patch retarget: a one-shot script
(`/tmp/claude/update_test_patches.py`) rewrote 1015 string patches
and 34 import names across `tests/test_main.py`,
`tests/test_phase{5,6,7}_integration.py`,
`tests/test_platform_integration.py`, and `tests/test_build_prefs.py`
to point at the new module locations. Two additional fixes were
needed by hand: the multi-line `from duplo.main import (extract_features as ..., fetch_site as ..., select_features as ..., select_issues as ...)` block in
`test_phase6_integration.py:1216`, and `import duplo.main as m`
patterns in `test_main.py` (11 occurrences) which were retargeted to
`import duplo.pipeline as m`.

### [BUGS.md#1] First-run all-phases loop was already in place — 2026-04-19

BUGS.md entry 1 claimed the first-run plan-generation path still called
`generate_phase_plan()` for a single `phase_info`. In reality the
mcloop checkpoint `2c810fe` already unified the first-run and
subsequent-run paths into one loop over `roadmap` in
`_subsequent_run()` (duplo/main.py:~1977), and the per-iteration
phase number already falls back to `phase_dict.get("phase", idx)` when
`phases_completed == 0`, so the scaffold phase is labelled "Phase 0".
The real gap was missing test coverage: no existing test exercised the
branch where `generate_roadmap()` is invoked in-run (all existing
tests pre-populated `roadmap` in `_BASE_DATA`). Added
`test_first_run_fresh_roadmap_generates_all_phases` in
`tests/test_main.py` to cover that branch, and changed the
post-`save_roadmap()` block to use the freshly generated roadmap
directly (`roadmap = new_roadmap`) rather than relying on the
save/reload round-trip to repopulate it.

### [8.3] PlatformEntry dataclass added, parser still missing — 2026-04-18

8.3 added the `PlatformEntry` dataclass and `platform_entries` field
on `ProductSpec` so `parse_build_preferences` can accept structured
entries. The field defaults to `[]` and the spec parser does NOT
populate it yet — list-item rows under `## Architecture` (e.g.
`- platform: macos` / `language: swift` / `build: spm`) are still
only captured as free-form prose in `spec.architecture`. Until the
8.1 parser work lands, the structured-entries branch in
`parse_build_preferences` is only exercised when a caller builds
entries manually (tests do this). Re-open 8.1 to wire up the
list-item parser so real SPEC.md files populate
`spec.platform_entries`.

Downstream callers in `main.py` currently collapse the list to the
first entry via `_primary_prefs()` before passing to
`generate_roadmap` / `generate_phase_plan`. 8.4 (wire resolver) is
the planned iteration that consumes the full list.

### [8.2] Documented structured platform entries without a parser — 2026-04-18

CURRENT_PLAN.md marks 8.1 complete (`[x]`), but `duplo/spec_reader.py`
still captures the `## Architecture` body as a single prose string.
There is no `PlatformEntry`/`architecture_entries` field on
`ProductSpec`, no list-item parser under Architecture, and no tests
covering `platform:`/`language:`/`build:` fields. 8.2 just updated
`SPEC-template.md` and `SPEC-guide.md` to describe the structured
syntax promised by 8.1, so the docs now describe a format the parser
does not yet accept. Either re-open 8.1 to implement the parser, or
amend the docs to say the structured form is forthcoming. Also affects
downstream tasks 8.3+ that assume the structured field is available on
`ProductSpec`.

### [7.9.1-7.9.4] Dead code sweep — 2026-04-17

Removed functions / constants with no production callers:

- `duplo/saver.py`: `write_claude_md`, `save_screenshot_feature_map`,
  `save_issues`, `add_issue`, `load_issues`, `clear_issues`,
  `save_code_examples`. Also the `CLAUDE_MD` module constant and
  `_CLAUDE_MD_CONTENT` template string (only used by `write_claude_md`).
- `duplo/fetcher.py`: `is_docs_link`, `detect_docs_links`,
  `_is_platform_domain`. Also the `_DOCS_LINK`, `_PLATFORM_DOMAINS`,
  `_PRODUCT_DOC_PATHS` module-level constants (only used by the deleted
  helpers). Deep-crawl link prioritisation still works via `score_link`
  / `_HIGH_PRIORITY` / `_LOW_PRIORITY`; the dropped helpers were
  docs-discovery scaffolding from the old cross-domain crawling design
  that `fetch_site` no longer follows (same-origin only).
- `duplo/extractor.py`: no dead code found. `_parse_features` is
  internal to `extract_features` and therefore live.

Restored `load_sources` after an initial removal pass — it has no
production callers but is used by `tests/test_main.py` integration
tests (`TestRemovedSourceIdempotent`) to assert `save_sources` merge
semantics. Treated as live through test usage.

Kept `save_selections` despite having no production callers. Used as a
bootstrap fixture by ~40 tests in `tests/test_saver.py` (`TestDeriveAppName`,
`TestSaveFeedback`, `TestAppendPhaseToHistory`, etc.) that set up an
initial `duplo.json` before exercising other saver functions. Removing
it would cascade into large-scale test rewrites disproportionate to the
cleanup value. Candidate for a future pass that introduces a dedicated
test helper.

### Dead file candidate: `duplo/initializer.py`

`project_name_from_url` and `create_project_dir` have no production
callers (Phase 7.5 audit confirmed) — only `tests/test_initializer.py`
imports them. `tests/test_main.py` § 12249-12282 explicitly asserts the
production modules do NOT import them. Not removed in this pass because
the project-wide rule forbids file deletion. User should decide whether
to delete `duplo/initializer.py` and `tests/test_initializer.py`.

Tests dropped from `tests/test_saver.py` alongside the removed saver
functions: `TestWriteClaudeMd`, `TestSaveScreenshotFeatureMap`,
`TestLoadSources`, `TestIssues`. From `tests/test_fetcher.py`:
`TestIsDocsLink`, `TestDetectDocsLinks`.

Verified: `pytest` → 2863 passed, 103 skipped. `ruff check duplo/ tests/`
→ clean.

### [7.7.1] Removed URL-in-text-file scanning — 2026-04-17

Under the old model, `scanner._extract_urls_from_file` read every text
file (and every non-source file) in the project, pulled HTTP(S) URLs
out with `_URL_RE`, and `_analyze_new_files` in `main.py` then handed
those URLs to `fetch_site`. Under the SPEC-driven model, URLs live
exclusively in SPEC.md's `## Sources` section and reach the scraper
via `scrapeable_sources(spec)` → `_scrape_declared_sources(spec)`.

Code change:

- `duplo/scanner.py`: dropped `_URL_RE`, `_SOURCE_EXTS`,
  `_SOURCE_NAMES`, `_extract_urls_from_file`, the `seen_urls` threads
  through `_classify_file` / `scan_files` / `scan_directory`, and the
  `urls: list[str]` field on `ScanResult`. `ScanResult` now exposes
  only `images`, `videos`, `pdfs`, `text_files`, `roles`.
- `duplo/main.py`: deleted the `if scan.urls:` URL-fetch block in
  `_analyze_new_files`, the `_load_existing_urls` helper, the
  `UpdateSummary.urls_fetched` field, the summary print, and the
  `summary.urls_fetched = analysis.urls_fetched` assignment in
  `_subsequent_run`. Updated `_analyze_new_files` docstring.
- `tests/test_scanner.py`: removed URL-extraction tests (extract,
  dedup, trailing punctuation, JSON/HTML/YAML skip, dedup across
  text/non-text, binary-file skip). Updated
  `TestScanDirectoryRefInventoryOnly.test_output_fields_are_inventory_only`
  to expect `{"images","videos","pdfs","text_files","roles"}`.
- `tests/test_main.py`: replaced `test_fetches_new_urls` and
  `test_skips_already_fetched_urls` with one negative test
  (`test_text_file_with_url_does_not_trigger_fetch`). Dropped
  `urls=[]` kwargs from `ScanResult(...)` mock returns. Dropped
  `assert result.urls == []` in `TestIntegrationRefOnlySpec`.

Confirmed live URL path for `_subsequent_run`: `scrapeable_sources(spec)`
(line 1537) → `_scrape_declared_sources(spec)` (line 1538) → `fetch_site`
(line 515). No remaining reference to `scan.urls`, `_load_existing_urls`,
or `urls_fetched` in the `duplo/` or `tests/` trees.

### [7.6.2] Vacuous: no legacy scoring code to delete from scanner.py — 2026-04-17

Task conditional ("If any legacy scoring functions or constants remain in
scanner.py that are no longer called, delete them") is not triggered.
Re-verified today with the same greps as 7.6.1 plus a full sweep of every
symbol in `duplo/scanner.py`:

- Grep `_MIN_IMAGE|dimension|threshold|too_small|too_large|getsize|
  st_size|relevance|_assess_|FileRelevance|MIN_BYTES|MAX_BYTES` in
  `duplo/scanner.py` → zero hits.
- Grep `scan\.relevance|scan_result\.relevance|result\.relevance|
  ScanResult\.relevance` across the repo → zero hits in live code (only
  the 7.6.1 NOTES.md entry itself).
- All symbols defined in `duplo/scanner.py` today (`ScanResult`,
  `scan_files`, `scan_directory`, `_classify_file`,
  `check_unlisted_ref_files`, `_build_reference_index`, `_lookup_roles`,
  `_extract_urls_from_file`; constants `_IMAGE_EXTS`, `_VIDEO_EXTS`,
  `_PDF_EXTS`, `_TEXT_EXTS`, `_SKIP_DIRS`, `_URL_RE`, `_IGNORE_EXTS`,
  `_SOURCE_EXTS`, `_SOURCE_NAMES`) are consumed on the live scan path —
  the extension/name sets feed `_classify_file`; the rest are entry
  points or index/lookup helpers reached from `scan_files` /
  `scan_directory` / `check_unlisted_ref_files`. Nothing orphaned.

CURRENT_PLAN.md line 53 therefore has no code change — same status as
line 52 (7.6.1). Next checkbox (presumably a Tests verification in
7.6.3+) can proceed.

### [7.6.1] Confirmed: scanner.py has no legacy scoring code — 2026-04-17

Verification task (no code change). Commit `ffc66ea` (2026-04-13 "Drop
the relevance scoring (image dimensions, file size). Roles are declared
in ## References, not inferred.") already removed every scoring artifact
from `duplo/scanner.py`. Re-verified today:

- No scoring constants: grep for `_MIN_IMAGE|dimension|threshold|MIN_|
  MAX_|too_small|too_large|getsize|st_size|\.size` in `duplo/scanner.py`
  returns zero hits.
- No scoring functions: `_assess_image`, `_assess_video`, `_assess_pdf`,
  `_assess_text` are gone (confirmed via `git show ffc66ea --
  duplo/scanner.py`).
- No `FileRelevance` dataclass and no `ScanResult.relevance` field
  (both removed in the same commit).
- No callers reference the removed field: grep for
  `scan\.relevance|scan_result\.relevance|result\.relevance` across the
  repo returns zero hits.
- Sole `_MIN_IMAGE_BYTES` that remains lives in `duplo/fetcher.py:34`
  and gates newly-downloaded embedded-media images during web scraping
  (`download_media` at `fetcher.py:443-476`). Unrelated to scanner
  reference-material scoring; not in scope for this task.

CURRENT_PLAN.md line 52 ("If any legacy scoring functions or constants
remain in scanner.py ... delete them") is therefore vacuously satisfied
— nothing to delete.

**Stale documentation flagged, not in scope:** `AGENTS.md:82-95` still
describes the removed `FileRelevance` dataclass, the `relevance` field,
and the "Assesses relevance of each file: flags tiny images (<1KB),
empty PDFs, empty/very-short text files as irrelevant" behavior. This is
documentation drift, not live code. User may want to refresh that entry
when touching AGENTS.md — duplo/CLAUDE.md's one-liner for `scanner.py`
is already accurate ("Roles are declared in SPEC.md `## References`, not
inferred."). No code path depends on AGENTS.md.

### [7.5.5] Pinned no-initializer-imports invariant in test suite — 2026-04-17

Added `TestNoInitializerImportsInPipeline` at `tests/test_main.py:12269`
with four `hasattr`-style assertions proving `duplo.main`, `duplo.init`,
`duplo.orchestrator`, and `duplo.saver` do not expose
`create_project_dir` or `project_name_from_url` in their module
namespaces. Mirrors the 7.3.5 / 7.4.4 pattern
(`TestNoAskPreferencesInPipeline`). All four tests pass.

The initializer files (`duplo/initializer.py`, `tests/test_initializer.py`)
still exist — per the no-delete rule captured in the 7.5.3 note above,
the physical deletion remains a user-executed follow-up. This test
class pins the live invariant (nothing in the pipeline imports the
dead functions) so the residual files cannot silently regain a caller.

Full suite: 2925 passed, 103 skipped (+4 passes vs. the 2921/103 after
7.4.4; no newly skipped tests).

### [7.5.4] project_name_from_url conditional does not fire — 2026-04-17

Task condition: "If project_name_from_url is used by derive_app_name or
another live path, keep only that function and delete the rest."

The condition is false. Verified via re-grep today:

- `derive_app_name` (`duplo/saver.py:91-159`) does not call
  `project_name_from_url`. Its resolution order is product.json
  `app_name` → duplo.json `app_name` → product.json `product_name` →
  `td.resolve().name` (directory). No hostname-derived naming anywhere
  in the chain.
- No other importer of `duplo.initializer` exists in `duplo/`. Only
  references to `project_name_from_url` outside `duplo/initializer.py`
  are test callers in `tests/test_initializer.py` (10, 15, 18, 21, 24,
  27), plan checklist text in `PLAN.md` / `CURRENT_PLAN.md`, prose in
  `AGENTS.md:356`, and prior NOTES.md entries.

Action: no "keep only that function" operation applies. Full-file
deletion remains blocked by the no-delete rule — already captured in
the 7.5.3 note above. CURRENT_PLAN.md line 45 marked complete because
the conditional's premise is false, not because any code changed.

### [7.5.3] Initializer deletion blocked by no-delete rule — 2026-04-17

The deletion prerequisite ("no remaining callers exist after _first_run
removal") is satisfied. Re-verified today:

- `create_project_dir` and `project_name_from_url` have zero importers
  in `duplo/**/*.py`. Only remaining references are the definitions in
  `duplo/initializer.py` (lines 10, 20) and test callers in
  `tests/test_initializer.py`.
- `AGENTS.md:356` mentions `project_name_from_url()` in prose only; no
  code reference.
- `PLAN.md:977-979` and `CURRENT_PLAN.md:42-45` reference the function
  names inside the plan checklist text; not executable code.

Per the project's absolute no-delete rule (CLAUDE.md: "Never delete any
file"), I cannot execute `duplo/initializer.py` or
`tests/test_initializer.py` deletion. Both files are dead and safe to
remove; leaving in place for the user to delete manually.

Suggested user actions when ready:

```
git rm duplo/initializer.py tests/test_initializer.py
```

After deletion, `CURRENT_PLAN.md` line 44 can be checked off. Lines
45-46 are already satisfied (line 45's branch is not triggered —
`project_name_from_url` is not in any live path per 7.5.1; line 46 is
covered by the existing grep showing no remaining production imports).

### [7.5.2] Confirmed: duplo init does not call create_project_dir — 2026-04-17

Verification of the model statement on CURRENT_PLAN.md line 43. Two
independent checks:

1. Grep for `create_project_dir` and `project_name_from_url` across
   `duplo/` returns only the definitions at `duplo/initializer.py:10,20`.
   No importer anywhere in `duplo/`. `duplo/init.py` does not import
   from `duplo.initializer` at all.
2. `duplo/init.py` uses `Path.cwd()` at every entry point
   (`_run_no_args` at 178, `_run_url` at 275, `_run_description` at 451,
   combined-flow at 516) — the user's existing working directory. No
   directory creation, no `git init`, no hostname-derived naming.

Consistent with the 7.5.1 audit (NOTES.md above): both functions are
dead in production after the 7.2.1 `_first_run` deletion. Their only
remaining references are inside `tests/test_initializer.py` (which
tests the functions themselves) and `duplo/initializer.py`'s own
definitions.

No code change required for this checkbox — it is a confirmation step
asserting the new-model invariant is already in effect. The next
checkbox (CURRENT_PLAN.md line 44, deletion of `duplo/initializer.py`
and `tests/test_initializer.py`) remains blocked by the project's
absolute no-delete rule and must be executed by the user.

### [7.4.4] Removed dead interactive-prompt code from questioner.py — 2026-04-17

The conditional ("If select_features is still needed") is vacuously
satisfied on the "leave in questioner.py" branch because
`select_features` is already in `duplo/selector.py` (and never lived
in `questioner.py` — confirmed by 7.3.4 / 7.4.1 audits). Nothing to
move. The actionable half of the task is "remove only the dead code",
i.e. strip `questioner.py` down to its one live symbol
(`BuildPreferences`).

Removed from `duplo/questioner.py`:

- `ask_preferences(...)` function (zero production callers after
  `_first_run` was deleted in 7.2.1).
- `_ask_platform`, `_ask_language`, `_ask_list`, `_print_summary`
  helpers (only reachable via `ask_preferences`).
- `_PLATFORMS` constant (only consumed by `_ask_platform`).

Kept:

- `BuildPreferences` dataclass (live; 12 importers across `duplo/`
  and `tests/`). A future task (CURRENT_PLAN.md § "BuildPreferences
  migration") will relocate it to `duplo/build_prefs.py` and retarget
  callers; the dataclass still has a home in `questioner.py` until
  then.

Test-file handling (followed the 7.2.x skip-don't-delete convention):

- `tests/test_questioner.py`: top-level import reduced to
  `BuildPreferences`; added module-level
  `pytestmark = pytest.mark.skip(...)`; moved the references to the
  removed helpers from the top-level import into each test class's
  `_run` method so import-time resolution doesn't hit the deleted
  names. The 18 tests that covered removed symbols are now skipped.
- `tests/test_main.py::TestNoAskPreferencesInPipeline::test_pipeline_does_not_call_ask_preferences`:
  marked `@pytest.mark.skip(...)`. The `monkeypatch.setattr(q,
  "ask_preferences", ...)` call would raise `AttributeError` now
  that the function is gone. The two sibling tests in the same class
  (`test_main_module_has_no_ask_preferences`,
  `test_orchestrator_module_has_no_ask_preferences`) still cover the
  invariant that the pipeline does not import `ask_preferences`.

Verification: `ruff check duplo/ tests/` passes. Full test suite
reports 2921 passed, 103 skipped (vs. 2937/84 before this task — the
19-skipped delta matches the 18 + 1 tests newly skipped, with no new
failures). Grep for `ask_preferences|_ask_platform|_ask_language|_ask_list|_print_summary|_PLATFORMS`
in `duplo/` returns only unrelated hits (`main._print_summary` for
`UpdateSummary`, `diagnostics.print_summary`, `questioner.py`'s own
docstring referencing the removed names, `build_prefs.py`'s module
docstring mentioning the superseded flow).

CURRENT_PLAN.md line 37 ("Tests: no remaining imports of deleted
functions; existing next-phase flow tests still pass") is the
remaining 7.4.x subtask. It is effectively satisfied by the full-suite
run above — no unskipped test imports `ask_preferences` or the `_ask_*`
helpers, and all non-skipped tests pass — but a dedicated one-line
assertion test would be a natural home for the invariant and is
deferred to the next checkbox.

### [7.4.3] Not executed: precondition not met + absolute no-delete rule — 2026-04-17

Task 7.4.3 ("If questioner.py can be deleted: delete it and
tests/test_questioner.py") was **not executed**. Two independent
blockers:

1. **Precondition not met.** `duplo/questioner.py` still defines
   `BuildPreferences` (lines 11-16), which is live code imported by
   nine non-test sites (`duplo/main.py:245`, `duplo/roadmap.py:10`,
   `duplo/planner.py:11`, `duplo/saver.py:19`,
   `duplo/build_prefs.py:22`) plus four test files
   (`tests/test_main.py:42`, `tests/test_planner.py:27`,
   `tests/test_roadmap.py:8`, `tests/test_saver.py:22`,
   `tests/test_build_prefs.py:17`, `tests/test_phase5_integration.py:23`).
   Deleting `questioner.py` now would break imports across the
   `_subsequent_run` path. Per the 7.4.2 execution order, the rename
   (PLAN.md § "BuildPreferences migration", lines 960-964) must run
   first — the deletion in 7.4.3 / PLAN.md § "questioner.py removal"
   (lines 966-972) is the *second* step.

2. **Absolute no-delete rule.** The session-level task prompt says:
   "Never delete any file. Do not use rm, git rm, os.remove, unlink,
   shutil.rmtree, or any other file deletion mechanism… If you
   believe a file should be removed, leave it and note it in NOTES.md
   for the user to decide." This overrides the conditional deletion
   in 7.4.3's wording regardless of blocker #1.

**Action required by user:** perform steps 1-3 of the 7.4.2 execution
order (move `BuildPreferences` to `duplo/build_prefs.py`, retarget the
12 importers, then manually delete `duplo/questioner.py` and
`tests/test_questioner.py`). Step 4 (drop or retarget
`tests/test_main.py:12223`'s `import duplo.questioner as q`) follows.

### [7.4.2] Determination: delete questioner.py after moving BuildPreferences to build_prefs.py — 2026-04-17

Decision for CURRENT_PLAN.md line 34 based on the 7.4.1 audit:

**questioner.py can be deleted entirely.** `select_features` is not an
open question because it is not defined in `questioner.py` and never
has been (verified 7.3.4, 7.4.1) — it lives in `duplo/selector.py` and
its one live caller in `_subsequent_run` at `duplo/main.py:1876` stays.
The phrasing of the checkbox ("whether select_features should be
migrated to selector.py") is moot: there is nothing to migrate. This
matches the decision already pre-committed in PLAN.md lines 960-972
("BuildPreferences migration" → "questioner.py removal").

Inventory of `duplo/questioner.py` symbols and their fate:

- `BuildPreferences` (dataclass, used by 12 importers: 5 in `duplo/`,
  7 in `tests/`) — **move** to `duplo/build_prefs.py`. Cannot be
  deleted with the module; it is live code on the `_subsequent_run`
  path (`_prefs_from_dict`, `_load_preferences`).
- `ask_preferences` — **delete** (zero production callers; only
  consumers are `tests/test_questioner.py` which tests the function
  itself, and `tests/test_main.py:12223` which asserts non-call).
- `_ask_platform`, `_ask_language`, `_ask_list`, `_print_summary`,
  `_PLATFORMS` — **delete** (only reachable via `ask_preferences`;
  only external references are from `tests/test_questioner.py`).

Execution order (required to keep the suite green at every step):

1. Add `BuildPreferences` to `duplo/build_prefs.py` and re-export from
   `duplo/questioner.py` (one-line `from duplo.build_prefs import
   BuildPreferences`) so importers keep working across the rename.
2. Retarget the 12 `from duplo.questioner import BuildPreferences`
   sites to `from duplo.build_prefs import BuildPreferences`.
3. Delete `duplo/questioner.py` and `tests/test_questioner.py`.
4. Retarget or drop `tests/test_main.py:12223`'s `import
   duplo.questioner as q` (two sibling tests —
   `test_main_module_has_no_ask_preferences` and
   `test_orchestrator_module_has_no_ask_preferences` — already cover
   the invariant, so dropping is the simpler path).

This maps directly onto the remaining 7.4.x checkboxes
(CURRENT_PLAN.md lines 35-37) and PLAN.md § "BuildPreferences
migration" / § "questioner.py removal".

### [7.4.1] `duplo.questioner` import audit: 13 sites; only `BuildPreferences` and `ask_preferences` family imported — 2026-04-17

Full grep for `duplo.questioner` / `import questioner` across the
repo. Every import listed; nothing else names the module.

Production code (`duplo/`) — 5 imports, all `BuildPreferences` only:

- `duplo/main.py:245` — `from duplo.questioner import BuildPreferences`
- `duplo/planner.py:11` — `from duplo.questioner import BuildPreferences`
- `duplo/roadmap.py:10` — `from duplo.questioner import BuildPreferences`
- `duplo/saver.py:19` — `from duplo.questioner import BuildPreferences`
- `duplo/build_prefs.py:22` — `from duplo.questioner import BuildPreferences`

Tests (`tests/`) — 8 import sites:

- `tests/test_main.py:42` — `from duplo.questioner import BuildPreferences`
- `tests/test_main.py:12223` — `import duplo.questioner as q` inside
  `TestNoAskPreferencesInPipeline::test_pipeline_does_not_call_ask_preferences`;
  used to `monkeypatch.setattr(q, "ask_preferences", ...)` so the test
  asserts no call. This is the one test that needs `ask_preferences` to
  exist on the module.
- `tests/test_planner.py:27` — `BuildPreferences`
- `tests/test_roadmap.py:8` — `BuildPreferences`
- `tests/test_saver.py:22` — `BuildPreferences`
- `tests/test_build_prefs.py:17` — `BuildPreferences`
- `tests/test_phase5_integration.py:23` — `BuildPreferences`
- `tests/test_questioner.py:5` — `from duplo.questioner import
  BuildPreferences, _ask_list, _ask_platform, ask_preferences`
  (the only importer of `ask_preferences`, `_ask_list`, `_ask_platform`)

Symbols actually defined in `duplo/questioner.py` (verified by reading
the file, 128 lines):

- `BuildPreferences` (dataclass) — imported by 12 of the 13 sites.
- `ask_preferences` — imported only by `tests/test_questioner.py`
  (explicit) and referenced by `tests/test_main.py` line 12223 via the
  module-alias import (to prove it is NOT called).
- `_ask_platform`, `_ask_language`, `_ask_list`, `_print_summary`,
  `_PLATFORMS` — only `_ask_list` and `_ask_platform` leave the module,
  and only via `tests/test_questioner.py`.

Non-import mentions (prose/comments, ignored by import audit):

- `duplo/build_prefs.py:4` — module docstring mentions
  `questioner.py` as the thing this module replaces. Not an import.
- `AGENTS.md:268,527`, `PIPELINE-design.md:991`, `PLAN.md` multiple,
  `CURRENT_PLAN.md` multiple, `NOTES.md` prior 7.3.x entries — design
  docs and prior task notes.

Implications for CURRENT_PLAN.md line 34 (decide whether questioner.py
can be deleted):

1. `ask_preferences` has zero production callers (confirmed 7.3.3). The
   only live consumer is `tests/test_questioner.py` which tests the
   function itself, plus the module-alias in `test_main.py` line 12223
   which only asserts non-call.
2. `select_features` is not defined in questioner.py and never has been
   (confirmed 7.3.4) — the `tests/test_questioner.py` import list above
   does not include it.
3. To delete `duplo/questioner.py`, `BuildPreferences` must first move
   to `duplo/build_prefs.py` (as PLAN.md § "BuildPreferences migration"
   plans). That is a rename touching 12 `from duplo.questioner import
   BuildPreferences` sites. Mechanically simple.
4. After the move, `tests/test_questioner.py` becomes the only thing
   keeping questioner.py alive; it tests dead interactive-prompt code.
   Deleting it alongside questioner.py is consistent with PLAN.md
   § "questioner.py removal" line 971.
5. `tests/test_main.py` line 12223's `import duplo.questioner as q`
   would need to change after deletion. Options: (a) replace with a
   module-attribute check that still asserts the function isn't wired
   into the pipeline (e.g. assert `ask_preferences` name not in
   `duplo.main` / `duplo.orchestrator`, which two sibling tests
   `test_main_module_has_no_ask_preferences` /
   `test_orchestrator_module_has_no_ask_preferences` already do), or
   (b) delete the test since the two sibling tests cover the same
   invariant.

Net: no blocker to the removal path in CURRENT_PLAN.md lines 32-37.
The sequence is (a) add `BuildPreferences` to `build_prefs.py`,
(b) retarget the 12 importers, (c) delete `duplo/questioner.py` and
`tests/test_questioner.py`, (d) either drop or retarget
`test_main.py:12223`. All subsequent 7.4.x subtasks are well-defined
once this audit is ratified.

### [7.3.4] No-op: `questioner.select_features` doesn't exist; `selector.select_features` stays — 2026-04-17

Grep-verified (2026-04-17): `duplo/questioner.py` defines no
`select_features`. The only `select_features` lives in
`duplo/selector.py` and has one live call site at `duplo/main.py:1876`
inside `_subsequent_run`'s phase-planning block (imported at
`main.py:300` from `duplo.selector`). That call is explicitly retained
per CURRENT_PLAN.md line 27 / task 7.3.2. Task 7.3.4's conditional is
vacuously satisfied on the "elsewhere" branch — leave it. No code
change required.

### [7.3.3] No-op: no `ask_preferences` call to remove — 2026-04-17

Grep across `duplo/` for `ask_preferences(` returns only two hits:
the definition in `duplo/questioner.py:19` and a docstring mention
in `duplo/build_prefs.py:3` (module-header prose, not a call).
Zero call sites in `duplo/main.py`, `duplo/orchestrator.py`, or any
other pipeline module. The last caller was `_first_run`, deleted in
7.2.1. All remaining `duplo.questioner` imports across the package
(`main.py:245`, `build_prefs.py:22`, `saver.py:19`, `planner.py:11`,
`roadmap.py:10`) bring in only the `BuildPreferences` dataclass.
Nothing to remove for this checkbox; whether `questioner.py` itself
(and the `BuildPreferences` home) should move is tracked by
CURRENT_PLAN.md § "Evaluate questioner.py for removal".

### [7.3.2] New-model wiring is already live in main.py — 2026-04-17

Confirmed the model statement on CURRENT_PLAN.md line 27 matches the
code as of 7.2.x:

- `BuildPreferences` flow: `duplo/main.py:242` imports
  `parse_build_preferences`; `_load_preferences` (`main.py:344-361`)
  re-parses from `spec.architecture` whenever `architecture_hash(spec.architecture)`
  differs from the stored hash, persists via `save_build_preferences`,
  and also calls `validate_build_preferences` for warnings. No
  `ask_preferences` fallback exists in that path (grep: zero hits in
  `duplo/main.py`).
- Feature-selection flow: `duplo/main.py:300` imports
  `select_features` from `duplo.selector`. The one live call site
  (`main.py:1876-1878`) fires inside `_subsequent_run`'s phase-planning
  block: `remaining = _unimplemented_features(data)` then
  `select_features(remaining, recommended=phase_info["features"], phase_label=...)`,
  and rewrites `phase_info["features"]` from the user's selection.
  This is the per-phase confirmation path and is explicitly retained.

Implication: CURRENT_PLAN.md line 28 (remove `ask_preferences` calls
from the pipeline) is already a no-op in `main.py` — it was effectively
completed by the 7.2.1 `_first_run` deletion, which was the last
caller. The import that remains from `duplo.questioner` is only
`BuildPreferences` (the dataclass); fate of the module as a whole is
tracked by CURRENT_PLAN.md § "Evaluate questioner.py for removal".



`questioner.ask_preferences` — zero callers in `duplo/main.py`. The
only `duplo.questioner` import in main.py is `BuildPreferences` (the
dataclass), at `main.py:245`, consumed by `_prefs_from_dict` and
`_load_preferences` (`main.py:324`, `main.py:334`) as a type. No call
to `ask_preferences` exists in main.py (grep-verified). Already dead
at the source after 7.2.1's `_first_run` deletion.

`questioner.select_features` — does not exist. `select_features` lives
in `duplo.selector`, not `duplo.questioner`. `duplo/questioner.py`
defines only `ask_preferences`, `_ask_platform`, `_ask_language`,
`_ask_list`, `_print_summary`, and `BuildPreferences`. The audit item
in CURRENT_PLAN.md line 26 presupposes a function that was never in
questioner.py — the wording should read "selector.select_features",
matching line 27 which correctly attributes it to `selector`.

`selector.select_features` in main.py has one live caller at
`main.py:1876` inside the next-phase / `_subsequent_run` phase-planning
flow (confirmed/adjusted feature list before PLAN.md generation). Per
CURRENT_PLAN.md line 27 this call is explicitly NOT being removed.
Imported at `main.py:300` from `duplo.selector`.

`duplo/orchestrator.py` — zero hits for `ask_preferences`,
`questioner`, or `select_features`. Satisfies the CURRENT_PLAN.md
line 30 test condition preemptively.

Implication for the remaining 7.3 checkboxes: CURRENT_PLAN.md line 28
(removing `ask_preferences` calls from the pipeline) is a no-op for
main.py — no such call exists. Only action left for this subsection
is line 30's verification test. Whether `BuildPreferences`'s import
path stays on `duplo.questioner` or moves to `build_prefs.py` is a
follow-up question for "Evaluate questioner.py for removal"
(CURRENT_PLAN.md line 32).

### [7.2.4] Cleared `_first_run` textual references from tests — 2026-04-17

Renamed `SKIP_FIRST_RUN` → `SKIP_LEGACY_PIPELINE` in tests/test_main.py
and tests/test_phase5_integration.py. Renamed 12 skip-marked methods from
`test_first_run_*` to `test_legacy_*`. Reworded docstrings, class
comments, and skip-reason strings to drop the `_first_run` name. Zero
occurrences of `_first_run` remain under `tests/` (grep-verified).
Full suite: 2937 passed, 84 skipped — unchanged from before.

No test body was deleted. The skipped classes still import or patch
removed helpers (`_validate_url`, `_confirm_product`, `_init_project`,
`ask_preferences`, etc.); they remain runnable only via `@pytest.mark.skip`
and will be revisited during the Phase 7 dead-code audit
(CURRENT_PLAN.md § "Dead code audit"), consistent with the prior 7.2.x
tasks' note that full rewrites are deferred.

Dispatch tests for 7.2.3 (fresh directory exits 0 with init message;
SPEC.md alone routes to `_subsequent_run`; duplo.json + SPEC.md routes
to `_subsequent_run`) were already in place before this task at
tests/test_main.py::`test_migration_pass_without_duplo_json_prints_init_message`,
`test_spec_only_proceeds_to_subsequent_run`, and
`test_migration_pass_proceeds_to_subsequent_run` (plus
`test_exits_when_no_reference_materials` in TestMainFirstRun). Verified
passing; no new tests added for this checkbox.

### [7.2.2] Deleted _confirm_product, _validate_url, _init_project — 2026-04-17

Audit results (grep + in-file AST scan via `ast.walk` checking every `Call`
node's function name) confirmed these three helpers had zero callers in
`duplo/` after `_first_run` was removed in 7.2.1. Deleted from `main.py`.

Associated import cleanup in `duplo/main.py` (each verified by AST scan to
have no other in-file caller):

- `from duplo.validator import validate_product_url` — was only used by
  `_validate_url`.
- `from duplo.test_generator import (detect_target_language,
  generate_test_source, save_test_file)` — all three were only used by
  `_init_project`; whole block removed.
- `from duplo.screenshotter import map_screenshots_to_features,
  save_reference_screenshots` — both only used by `_init_project`; whole
  line removed.
- From `duplo.saver` import block: dropped `save_selections`,
  `save_screenshot_feature_map`, `write_claude_md` (the rest of the saver
  imports are still in use).
- Module-level `_SECTION_URL_RE = re.compile(...)` — only consumed by
  `_init_project`; removed along with it.

Other in-file helpers not called anywhere in `duplo/` are retained
because they are out of scope for this task:

- `_visual_target_video_frames` was never called by `_first_run` (git
  history at `80ba6e7` and `f40e24b` dropped its call site earlier). It
  has direct tests in `test_main.py` and is unrelated to `_first_run`
  removal; left in place.
- `_excepthook`, `_signal_handler`, `_handle_signal` are nested closures
  inside `_mcloop_setup_crash_handlers` / `main()` and are wired into
  `sys.excepthook` / `signal.signal`; not dead code.

Test-file updates:

- `tests/test_main.py` dropped `_init_project` from its top-level import
  list and added `pytestmark = pytest.mark.skip(...)` to `TestInitProject`
  (8 tests). A module-level placeholder `_init_project = None` was added
  so ruff's F821 check still passes on the (now-skipped) test bodies that
  still reference the name as a free function. Removing it will require
  deleting or rewriting those tests, which is out of scope here.
- `tests/test_validator.py` gained `import pytest` and class-level
  `pytestmark = pytest.mark.skip(...)` on `TestValidateUrlInMain` (12
  tests) and `TestConfirmProduct` (8 tests). Each test imports the
  removed symbol inside the test body, so the imports are never resolved
  under the skip mark.
- `tests/test_phase5_integration.py` only referenced `_confirm_product`
  in a docstring on an already-class-skipped test and needed no change.

Verification: `ruff check duplo/ tests/test_main.py tests/test_validator.py`
passes. Full test suite `pytest -q` reports 2936 passed, 84 skipped (up
from 62 skipped, reflecting the 20 `TestValidateUrlInMain`/`TestConfirmProduct`
tests and 8 `TestInitProject` tests newly skipped by this task minus
overlap with prior skips; no new failures).

PLAN.md § "_first_run removal" line 951 ("no test references
_first_run, _confirm_product, _validate_url, or _init_project") is
still partially open: references remain in skipped tests. That is
consistent with the 7.2.1 convention of deferring test rewrites; a
later task can delete the skipped classes outright.

### [7.2.1] _first_run function deleted — 2026-04-17

Deleted the function body and its preceding removal-audit comment block
(formerly main.py:1035-1474). The file parses (`python3 -c "import ast;
ast.parse(...)"` returns OK).

The original plan was to leave the dispatch untouched until 7.2.3, but
`ruff check duplo/main.py` failed with F821 on the dangling
`_first_run(url=args.url)` call at main.py:799. A minimal dispatch
stub had to land here: the `not duplo_path.exists()` branch now prints
"Run `duplo init` first to create SPEC.md." and exits 1. This anticipates
task 7.2.3's shape (CURRENT_PLAN.md line 20) — 7.2.3 will refine it to
distinguish fresh directory (no SPEC.md) from partial reset
(SPEC.md present, `.duplo/` gone) and route the latter into
`_subsequent_run`. For now, both cases hit the exit.

Intermediate broken state this commit leaves in place, to be cleaned up
by subsequent 7.2.x tasks:

- Tests that exercised `_first_run` (either by patching
  `duplo.main._first_run`, or by patching internals like
  `ask_preferences`/`scan_directory` and calling `main()` in a
  fresh-directory setup) have been marked `@pytest.mark.skip` with
  reason pointing to Phase 7.2.4. A module-level `SKIP_FIRST_RUN`
  marker was added to tests/test_main.py and
  tests/test_phase5_integration.py. Affected: 35 tests in test_main.py,
  22 tests in test_phase5_integration.py (3 classes class-skipped in
  test_main.py, 5 classes class-skipped in test_phase5_integration.py,
  remainder individual decorators). The 4 dispatch-oriented tests in
  TestMigrationDispatchOrder were updated in place: 3 had their
  `_first_run` setattr removed (the behavior they test doesn't need
  it); test_migration_pass_proceeds_to_first_run was renamed/rewritten
  to assert the new init-message-and-exit-1 behavior.
- Helpers `_confirm_product` (main.py:2027-2058 new numbering),
  `_validate_url` (main.py:2059-2133), `_init_project` (main.py:2134-2212)
  remain in place. They were only called from `_first_run`; they are
  now dead code but are removed in 7.2.2 rather than here to keep
  this commit scoped strictly to the function-deletion step.
- Imports of `ask_preferences` and `scan_directory` in main.py were
  removed in this commit (they had no remaining in-file callers after
  `_first_run` deletion). `ruff check` would flag them as F401 if left.

### [7.1.3] Migration gate fully prevents old-format projects from reaching _first_run — 2026-04-17

Audit confirms no old-format project can reach `_first_run`. Dispatch in
`main.py:655-805` has three entry branches:

- `init` subcommand (main.py:665-716) — bypasses `_check_migration`, calls
  `run_init` (duplo/init.py). `run_init` never calls `_first_run` (grep confirms).
- `fix`/`investigate` subcommand (main.py:718-795) — bypasses
  `_check_migration`, calls `_fix_mode`. `_fix_mode` never calls `_first_run`.
- Default no-subcommand path (main.py:796-803) — calls `_check_migration(Path.cwd())`
  first (main.py:797), then dispatches on `duplo.json` existence:
  `_first_run(url=args.url)` if absent, `_subsequent_run()` if present.

`_first_run` is called from exactly one site (main.py:799) — grep for
`_first_run(` returns only the definition (main.py:1035) and that one call.
No internal recursion; `_subsequent_run` does not invoke it.

`needs_migration` (migration.py:37-64) fires only when `.duplo/duplo.json`
exists AND no new-format SPEC.md is present. By definition:
- Old-format project has `.duplo/duplo.json` → either migration fires and
  `sys.exit(1)` before dispatch, or it passes (new-format SPEC.md present)
  and `duplo_path.exists()` is True → `_subsequent_run`, not `_first_run`.
- Therefore `_first_run` is reachable only when `.duplo/duplo.json` does
  NOT exist, which is by definition NOT an old-format project.

Existing tests pin this:
- `test_old_layout_prints_message_exits_skips_runs` (test_main.py:6316)
  asserts `first_run_called == []` for old layout.
- `test_init_skips_check_migration` (test_main.py:6420) asserts
  `first_run_called == []` on init bypass.
- `test_fix_old_layout_bypasses_migration_dispatches_fix` (test_main.py:6441)
  confirms fix bypass routes to `_fix_mode`.
- `test_migration_pass_proceeds_to_first_run` (test_main.py:6395) pins
  that `_first_run` runs only when no `duplo.json`.

Conclusion: no code path allows an old-format project to reach `_first_run`.
No gating or removal needed for bypass. The next checkbox (document at
removal site) can proceed.

**Edge case flagged, not a reachability problem**: a user who manually deletes
`.duplo/duplo.json` but leaves other old artifacts (`.duplo/product.json`,
`screenshots/`, legacy `references/`) falls through to `_first_run`. By
MIGRATION-design.md:168-172 this is intentional ("they can always delete
`.duplo/` and start fresh"). `_first_run` even consumes a lingering
`.duplo/product.json` via `load_product()` at main.py:1111. This partial-reset
path is a feature of the current design, not a missed old-format case.

### [7.1.2] duplo init + _subsequent_run coverage of _first_run — 2026-04-17

Confirmed the coverage claim in CURRENT_PLAN.md line 13.

**URL input** (was `_first_run(url=args.url)` at main.py:1035, 1050-1053, and the
`_validate_url` interactive disambiguation at 1141-1143). `duplo init` accepts the URL
at `args.url` (init.py:155) and dispatches to `_run_url` (init.py:261) which
canonicalizes (url_canon.canonicalize_url) and fetches shallow-by-default via
`fetch_site`. Disambiguation is replaced by a non-interactive `validate_product_url`
call inside `_identify_product` (init.py:203-223) — unidentified products fall back to
a pre-filled `## Sources` entry without prompting. `duplo init <url>
--from-description PATH` (combined) and `--from-description PATH` alone are also
handled. Equivalent or stronger than `_first_run` URL handling.

**SPEC.md generation** (new; `_first_run` never wrote SPEC.md — it consumed one
if present and wrote an autogen design block). `duplo init` always writes
SPEC.md via `format_spec(_build_draft_spec(...))` (init.py:197, 321, 331, 340,
473, and the combined path). Existing files in `ref/` are inventoried by
`_scan_existing_ref_files` and each gets a role via `_propose_file_role`, so the
drafted `## References` is pre-filled for the user to confirm.

**Feature extraction** (was in `_first_run` at main.py:1194-1207 via
`extract_features` followed by interactive `select_features`). `_subsequent_run`
calls `extract_features` at main.py:2060-2066 with the same `spec_text`,
`scope_include`, `scope_exclude` arguments, then merges new features into
duplo.json via `save_features` (2078). The per-phase interactive
`select_features` at 2294 still runs before phase PLAN.md generation — the only
change is it runs per-phase, not once at bootstrap.

**PLAN.md generation from SPEC.md** (was in `_first_run` at main.py:1412-1438,
generating Phase 1 from the fresh roadmap). `_subsequent_run`'s State 3 branch
(main.py:2246-2349) generates a PLAN.md for the current phase: regenerates a
roadmap if none exists (2252-2276), runs `generate_phase_plan` (2330-2339), and
appends verification cases from both video frames (2340-2348) and behavior
contracts (same structure as `_first_run`).

Conclusion: the three responsibilities named in the plan (URL input, feature
extraction, PLAN.md generation) are fully covered by the split
`duplo init` + `_subsequent_run`. The claim is accurate.

### [7.5.1] Audit of initializer callers — 2026-04-17

`initializer.create_project_dir`:
- Defined in `duplo/initializer.py:20`.
- Zero production callers. Grep confirms no imports of
  `create_project_dir` in `duplo/**/*.py` (only `duplo/initializer.py`
  itself).
- Test callers in `tests/test_initializer.py` only (lines 10, 35, 44,
  52, 61, 68, 74, 86, 92, 99, 106).

`initializer.project_name_from_url`:
- Defined in `duplo/initializer.py:10`.
- Zero production callers. `saver.derive_app_name` does NOT use it:
  its fallback (`saver.py:151-153`) is `td.resolve().name` (directory
  name), not a URL-derived hostname. The resolution order
  (1 product.json `app_name` → 2 duplo.json `app_name` →
  3 product.json `product_name` for product-reference sources →
  4 directory name) never calls `project_name_from_url`.
- Test callers in `tests/test_initializer.py` only (lines 10, 15, 18,
  21, 24, 27).

No remaining production callers for either function after the
`_first_run` removal in 7.2.1. The CURRENT_PLAN.md branch at line 45
("If project_name_from_url is used by derive_app_name or another live
path, keep only that function and delete the rest") is not triggered —
neither function is in a live path.

Next checkbox (CURRENT_PLAN.md line 44) calls for deleting
`duplo/initializer.py` and `tests/test_initializer.py`. Per the "never
delete any file" project rule, this is flagged here for the user to
decide. Leaving both files in place until explicitly directed.

### [3] mypy not installed; type-error sweep deferred — 2026-04-19

Task 3 asked to add `[tool.mypy]` to pyproject.toml, run mypy against
the codebase, and fix any type errors that surface. The mypy config
block and `tests/test_mypy.py` (which runs `python -m mypy duplo` via
subprocess, skipping if mypy is unavailable) are in place. However,
`mypy` is not installed in `.venv` or on PATH, and the project
instructions forbid installing tools. Concretely:

- `/Users/mhcoen/proj/duplo/.venv/bin/pip list | grep -i mypy` → empty
- `which mypy` → not found
- `python -m mypy --version` → `No module named mypy`

Consequences:
- The "run mypy and fix errors that surface" step could not be
  performed. No type annotations were adjusted.
- `tests/test_mypy.py::test_mypy_clean` will currently be skipped in
  CI. Once mypy is installed (`pip install mypy`), the test will
  execute and may report real type errors that still need to be fixed.

Recommended user follow-up:
1. `pip install mypy` (or add to dev dependencies).
2. Run `python -m mypy duplo` and address the reported errors with
   type-annotation-only fixes, matching the intent of task 3.
3. Re-run `pytest tests/test_mypy.py` to confirm zero errors.

### [1] Pre-existing test_claude_cli timeout test was stale — 2026-04-19

`test_raises_claude_cli_error_on_timeout` mocked `time.monotonic` to
return values topping out at 302.0s, but `_TIMEOUT_SECONDS` in
`claude_cli.py` is now 600 (a previous task raised it from 300). The
mocked iterator therefore exhausted before the timeout check ever
fired, producing `StopIteration` rather than the expected
`ClaudeCliError`. Bumped the mocked values to 601.0/602.0 so the
timeout branch is exercised. This was not part of the design-section
fix but blocked the mandatory `pytest` check.

### [4] `duplo fix` still emits `## Bugs` via saver.append_to_bugs_section — 2026-04-19

Task [4] required removing the `## Bugs` section from `save_plan()` output
in `planner.py`, which was the `_subsequent_run` path. That is done:
`_inject_bugs_section` is now `_strip_bugs_section` and only removes the
heading if the LLM produced one.

However, `duplo/saver.py::append_to_bugs_section` still creates a `## Bugs`
section when none exists (used by `_fix_mode` in pipeline.py). The task
statement ("There must NEVER be a ## Bugs section in any PLAN.md file
generated by duplo") arguably covers this path too, but the task scope
explicitly named `_subsequent_run`. Left the `duplo fix` path unchanged
to keep the change targeted; if `duplo fix` should also stop emitting
`## Bugs`, that is a follow-up with its own test-file churn
(test_saver.py, test_pipeline.py fix-mode tests).

### [1] `_strip_trailing_commentary` fix was already present from earlier task — 2026-04-20

Task [1] in BUGS.md ("add a _strip_trailing_commentary(content) function
...after _ensure_h1_heading()") appears to duplicate work already landed
in commit 4e616f4. The function exists in `planner.py`, is called from
`generate_phase_plan()` after `_ensure_h1_heading()`, and the fenced +
trailing-prose scenario described in BUGS.md is already covered by
`TestStripTrailingCommentary::test_truncates_after_last_task_with_fence_and_commentary`
in `tests/test_planner.py`.

An attempted "spec-strict" rewrite (always normalize to exactly one
trailing newline) broke `TestGeneratePhasePlanH1Heading::test_preserves_existing_h1`,
which asserts that LLM output without a trailing newline is preserved
verbatim. The early-return branch `last_task_idx == len(lines) - 1`
in `_strip_trailing_commentary` is therefore load-bearing: when there
is no trailing commentary to strip, the function must preserve input
formatting exactly. Extended tests (`test_preserves_input_when_last_task_is_final_line`
and a second fence-plus-prose case) were added to lock in this
invariant so a future "helpful normalization" does not regress it.

## Hypotheses

### [7.1.2] Dispatch and _subsequent_run assumptions block _first_run removal — 2026-04-17

Coverage confirmed above is prospective — the next checkbox (CURRENT_PLAN.md
line 20) must flip the dispatch. Gaps to resolve when that lands:

1. **Dispatch still routes to `_first_run`.** main.py:797-799 calls
   `_first_run(url=args.url)` whenever `.duplo/duplo.json` is absent.
   `duplo init` writes SPEC.md but NOT `.duplo/duplo.json`, so
   `duplo init` followed by `duplo` still hits `_first_run`, not
   `_subsequent_run`. Until the dispatch is updated, the coverage above is only
   latent.

2. **`_subsequent_run` assumes duplo.json exists in several places.** After the
   dispatch flips, the first `duplo` run against a fresh `duplo init` directory
   will have no `.duplo/duplo.json`. Spots that need hardening (FileNotFoundError
   paths not yet guarded):
   - main.py:2056 `old_data = json.loads(Path(_DUPLO_JSON).read_text(...))` —
     except clause catches only `JSONDecodeError`.
   - main.py:2073 same pattern, same gap.
   - main.py:2079 `updated_data = json.loads(...)` — relies on `save_features`
     having created the file, which it does, but worth verifying for the
     feature-less fresh-init case.
   - main.py:2213-2218 outer `data` reload — same `except json.JSONDecodeError`
     only.
   - The top-level read at 1973-1976 DOES catch `OSError`, so that one is fine.

3. **Three `_first_run` responsibilities not in either replacement:**
   - Interactive app_name prompt for appshot (main.py:1360-1363). No
     equivalent in `duplo init` or `_subsequent_run`. `derive_app_name(spec)` is
     used in `_subsequent_run` at 2219, which reads from duplo.json then
     directory name — so `app_name` ends up as the sanitized directory name by
     default. If the old interactive prompt is deemed dead, this is already
     covered; if it's meant to stay, it needs a home.
   - `ask_preferences()` fallback when `spec.architecture` is empty
     (main.py:1355-1356). `validate_for_run` should reject a spec with an
     unfilled `## Architecture` before this point, making the fallback
     unreachable. Worth asserting that fact before deleting `ask_preferences`.
   - Interactive roadmap approval prompt (main.py:1400-1405). `_subsequent_run`
     State 3 saves the new roadmap without a confirmation prompt (2272-2273).
     This is a behavior change, not a gap — worth calling out so it is an
     explicit decision rather than a silent drop.



All six test cases called out in 6.15.8 were added incrementally during
6.15.1-6.15.7 and pass. Mapping:

- description read from file: `TestRunInitDescriptionFile::test_reads_description_from_file_and_writes_spec`
- description read from stdin (mocked): `TestRunInitDescriptionStdin::test_reads_description_from_stdin_pipe`
- file not found exits 1: `TestRunInitDescriptionFile::test_missing_file_prints_error_and_exits_1`
- URL extracted with `proposed: true`: `TestRunInitDescriptionUrlExtraction::test_like_url_in_prose_becomes_proposed_product_reference`
- inferred role correct: same test (product-reference) + `test_unlike_url_in_prose_becomes_proposed_counter_example_scrape_none` (counter-example)
- Notes section contains verbatim prose: asserted inside `test_reads_description_from_file_and_writes_spec`

No new tests were written for this subphase because adding redundant
assertions would duplicate the existing coverage. All 48 tests in
`tests/test_init.py` pass.

### [6.15.5] URL-from-prose extraction already in place — 2026-04-17

Task 6.15.5 asks for URL extraction from the prose description, with role
inferred via `_infer_url_role` and `proposed: true` on the resulting
`SourceEntry`. This was already implemented as part of task 6.15.1 (see
the note below). `_build_draft_spec` (spec_writer.py lines ~1073-1096)
handles extraction, canonicalization, role inference, counter-example
scrape coercion, and dedup against an explicit `inputs.url`. `_run_description`
already calls `_build_draft_spec`, so the init flow gets the behavior for
free. Tests covering the behavior: `TestExtractProseUrls`,
`TestBuildDraftSpecProseUrls` (in test_spec_writer.py), and
`TestRunInitDescriptionUrlExtraction` (in test_init.py). All pass.

INIT-design.md vs DRAFTER-design.md discrepancy: INIT-design.md § "duplo init
--from-description" lines 185-187 says the prose-extracted source entry gets
"a note explaining the URL was extracted from the description." DRAFTER-design.md
§ "Inferring URL roles" does not mention a note. The task description says
"Per DRAFTER-design.md" and the plan header specifies DRAFTER-design.md is
authoritative for spec_writer.py extensions, so the current implementation
(no note) follows the authoritative source. Flagging here per the plan's
"flag the discrepancy for resolution" rule — a later task or user decision
may want to add `notes="extracted from description"` on these entries.

### [6.15.1] draft_spec refactored to expose a ProductSpec-returning core — 2026-04-17

`_run_description` needs to inspect the drafted `ProductSpec` to decide which
per-section bullets to print ("Pre-filled ## Purpose, ## Design from prose.",
"## Architecture left as <FILL IN>", etc.). Rather than re-parsing the
serialized SPEC.md, split `draft_spec` into `_build_draft_spec(inputs) ->
ProductSpec` + a thin `draft_spec = format_spec ∘ _build_draft_spec` wrapper.
Existing `draft_spec` tests all still pass because `draft_spec` is
behaviorally identical. The new internal API is what init.py consumes.

Also added URL extraction from description prose to the drafter
(`_extract_prose_urls`), so descriptions like "like https://numi.app" now
produce a `proposed: true` Sources entry with `role: product-reference`
inferred via `_infer_url_role`. Counter-example roles get `scrape: none`
coerced at write time (mirrors the parser and `append_sources` rules).
An explicit `inputs.url` suppresses any duplicate prose-extracted entry
so Sources stays single-entry-per-URL when the combined case arrives in
Phase 6.15.2.

### [6.7.1] DraftInputs added in this task, not in 6.1.1-6.1.2 — 2026-04-17

Task 6.7.1 implements `_draft_from_inputs(inputs: DraftInputs)` whose first
argument type was supposed to be defined by tasks 6.1.1-6.1.2. git log shows a
checkpoint commit `b64753b` (next: 6.1.1-6.1.2) followed directly by `a743f64`
(next: 6.2.1) — no "Complete: [BATCH] 6.1.1-6.1.2" commit between them — yet
CURRENT_PLAN.md marks 6.1.1-6.1.2 as `[x]` done. The DraftInputs dataclass was
never actually written. Added it in this task so `_draft_from_inputs` has its
parameter type. If the workflow runs 6.1.1-6.1.2 again it will find DraftInputs
already present; tests pass either way.

### [6.8.5] Step 4 emits reference entries without a role when vision_proposals is incomplete — 2026-04-17

`draft_spec` step 4 uses `inputs.vision_proposals.get(path, "")` so a ref/
file that is not a key in `vision_proposals` gets a `ReferenceEntry` with
`roles=[]`. `format_spec` emits this as `- <path>` + `proposed: true` with
no `role:` line. The parser then drops such entries into
`dropped_references` because a role is required. In practice this should
not happen — the caller (`duplo init`) calls `_propose_file_role` for
every file in `existing_ref_files`, and that function always returns a
role (falling back to `"ignore"` for unknown extensions). The defensive
`.get(path, "")` fallback is therefore a silent data-loss path if a
caller ever constructs `DraftInputs` with mismatched `existing_ref_files`
/ `vision_proposals`. Options if this becomes a concern: (a) assert every
`existing_ref_files` entry is present in `vision_proposals`, (b) default
missing entries to `"ignore"` so they survive the parser round-trip, or
(c) log a diagnostic. Test
`test_step4_ref_file_missing_from_vision_proposals_emitted_without_role`
pins the current writer behavior.

### [6.7.1] Error-handling discrepancy between plan and design doc — 2026-04-17

CURRENT_PLAN.md bullet 6.7.7 says `_draft_from_inputs` should "fall back to
empty ProductSpec (template-only draft) with a diagnostic" after retries.
DRAFTER-design.md § "Error handling" says the function should raise
`DraftingFailed` and the caller (`draft_spec`) catches it. Tasks 6.9-6.10
plan to add `DraftingFailed` and catch it in `draft_spec`. Implemented per
the plan for now (return empty ProductSpec + record_failure); if 6.9/6.10
migrate to exception-based handling, `_draft_from_inputs` will need to be
refactored to raise instead of return, and tests updated.

### [6.3.1] Parser re-ingests content from format_spec comment hints — 2026-04-17

Round-trip testing revealed that `_parse_spec` picks up "example" content
from inside the HTML comment hints that `format_spec` emits for empty
optional sections. Specifically: the Sources/References/Scope/Behavior
parsers operate on raw body text (not comment-stripped), and their regexes
match the example list items embedded in the `<!-- Example: ... -->` blocks.
For an empty `ProductSpec`, `parse(format_spec(spec))` yields non-empty
`sources`, `references`, `scope_include`, `scope_exclude`, and
`behavior_contracts` populated from the template examples. Notes and
Architecture strip comments before extraction and are not affected.
Impact: the round-trip property only holds for specs that populate these
sections with real content (so `format_spec` skips the comment hints).
Round-trip fixtures in `tests/test_spec_writer.py::TestRoundTrip` all have
at least one entry in each "pickup-prone" section. Eventual fix would be
to either (a) comment-strip bodies in the Sources/References/Scope/Behavior
parsers, or (b) omit example content from the comment hints. Deferred —
not in scope for 6.3.1.

### [6.3.1] Round-trip comparator excludes more fields than DRAFTER-design.md lists — 2026-04-17

DRAFTER-design.md's `_ROUND_TRIP_EXCLUDED_FIELDS` example lists only `raw`,
`dropped_sources`, and `dropped_references`. In practice the comparator also
must exclude `scope`, `behavior`, `fill_in_purpose`, `fill_in_architecture`,
and `fill_in_design` because these are derived from the serialized body by
the parser: `scope` / `behavior` hold the raw body string and always
change after round-tripping; the `fill_in_*` flags are parser-set when the
body contains `<FILL IN>` markers. DesignBlock's `has_fill_in_marker` is
similarly parser-set and excluded by comparing only `user_prose` and
`auto_generated` in the DesignBlock sub-comparison. Design doc lists the
minimum; this is the practical set.

### [1.6] `extract_json` preferred inner object over outer array — 2026-04-16

Adding round-trip parser tests for the four migrated modules surfaced a latent
bug in `duplo.parsing.extract_json`: for prose-prefixed input like
`"Here are the features:\n[{...}]"`, the balanced-span scanner iterated `{...}`
first and returned the first valid object (the inner dict), instead of the
outer array. `_parse_features` then saw a dict, failed its `isinstance(data,
list)` check, and returned `[]` — no round-trip. Fixed by switching
`extract_json` from "first valid span wins" to "longest valid span wins": for
arrays of objects the outer `[...]` is longer than any inner `{...}`, so the
array is returned; for objects containing arrays the outer `{...}` is longer,
so the object is returned. Existing tests in `test_parsing.py` (including
`test_extract_json_multiple_objects`) continue to pass because their
assertions are satisfied by either span.

### [1.5] `strip_fences` + `json.loads` migration is incomplete — 2026-04-16

Phases 1.1–1.4 migrated `extractor.py`, `gap_detector.py`, `build_prefs.py`, and
`validator.py` to use `extract_json`. Five modules still contain the old
pattern and are allow-listed in `tests/test_parsing_invariant.py`
(`ALLOWED_UNMIGRATED`): `roadmap.py`, `verification_extractor.py`,
`investigator.py`, `task_matcher.py`, `saver.py` (3 occurrences in saver).
The regression test catches reintroduction into migrated files today; when
each remaining module is migrated, its entry should be removed from
`ALLOWED_UNMIGRATED` so the guard covers it too. A companion test
(`test_allowed_unmigrated_list_is_accurate`) fails loudly if a file is
migrated without removing its allowlist entry.

### [5.38.2] Diagnostic logging added to frame_describer — 2026-04-16

Added `record_failure` calls to all three parse-error exit paths in `_parse_descriptions`. Each records the raw LLM response (first 2000 chars) and the extracted text to `.duplo/errors.jsonl`. The next manual run with video frames will capture the actual response that the parser is choking on. No existing `frame_describer` entries were found in `errors.jsonl` because the logging wasn't present during the [5.38.1] manual run.

### [5.39.2] Design extraction chain had silent failure paths — 2026-04-16

Traced the full chain in `_subsequent_run` after `extract_design` is called. Four
places in `main.py` run the extract→format→update pipeline. Two of them
(`_subsequent_run`'s spec_sources path and `_rescrape_product_url`) were missing
the `else` branch for when `design.colors/fonts/layout` are all empty — extraction
would fail silently with no message or diagnostic. All four paths were missing
diagnostics for two inner steps: `format_design_block` returning empty despite
non-empty design fields, and `update_design_autogen` returning unchanged text.
Added `record_failure` calls at both inner failure points in all four paths, and
added the missing "Could not extract" messages in the two paths that lacked them.
The most likely cause of the [5.39.1] issue: `extract_design` returned a
`DesignRequirements` with populated `source_images` but empty colors/fonts/layout
(from a `ClaudeCliError` or parse failure), and `_subsequent_run` silently skipped
writing to SPEC.md because there was no else branch.

### [5.39.1] design_extractor had the same strip_fences fragility — 2026-04-16

`design_extractor._parse_design` used `strip_fences` + `json.loads`, the same pattern fixed in `frame_describer`/`frame_filter` during [5.38.3]. When the Vision LLM returned JSON preceded by prose (e.g. "Here is the design analysis:\n\n{...}"), `strip_fences` was a no-op, `json.loads` raised `JSONDecodeError`, and `_parse_design` returned an empty `DesignRequirements`. The caller in `main.py` then skipped writing `## Design` to SPEC.md because `design.colors` was empty. No diagnostic was logged because the error path returns silently. Fixed by switching to `extract_json`. This was noted as a latent risk in [5.38.1] ("Other modules using `strip_fences` + `json.loads` … have the same latent vulnerability").

### [5.38.1] LLM JSON extraction fragility in Vision modules — 2026-04-16

`frame_describer` and `frame_filter` both used `strip_fences` to clean LLM output before `json.loads`. When the LLM returns JSON wrapped in conversational prose without markdown code fences, `strip_fences` is a no-op and parsing fails. Fixed by adding `extract_json` to `parsing.py` (tries `strip_fences` first, then scans for outermost `{...}` / `[...]`). Applied to `frame_describer` and `frame_filter`. Other modules using `strip_fences` + `json.loads` (extractor, gap_detector, build_prefs, validator, etc.) have the same latent vulnerability but weren't hit in practice — they use `query` (text-only), not `query_with_images` (tool-augmented), so the LLM is less likely to produce prose-wrapped JSON.

### [5.27.7] `save_raw_content` default `target_dir` bug — 2026-04-14

`saver.py:save_raw_content` uses `target_dir: Path = Path.cwd()` as a default argument (line 1213). Unlike every other function in `saver.py` which uses `target_dir: Path | str = "."`, this one evaluates `Path.cwd()` at import time, not call time. In production this works because duplo's cwd doesn't change between import and use. In tests using `monkeypatch.chdir(tmp_path)`, the default points to the original cwd instead of `tmp_path`. Integration tests must either pass `target_dir` explicitly or call `save_raw_content` directly rather than through `_persist_scrape_result`. Consider aligning with the `"."` convention used everywhere else.

### [6.10.3] `## ` inside AUTO-GENERATED design body is read as a new section — 2026-04-17

While adding the edit-safety property test for `update_design_autogen`, a
body containing a literal `## swatches` line mid-content did not round-trip
through `_parse_spec` — everything from that line onward was treated as a
new section heading, truncating `design.auto_generated`. The parser is
line-based on `^## ` and does not recognize the `<!-- BEGIN/END
AUTO-GENERATED -->` markers as an opaque region. In practice design
auto-generation never emits `## ` lines (bodies are bullet lists and
simple `key: value` pairs), so this is a latent edge case rather than a
live bug. If the Vision extractor starts producing Markdown headings in
bodies, either the parser needs to respect the AUTO-GENERATED markers or
the writer must escape `## ` on emit. The pathological body was removed
from `_NEW_DESIGN_AUTOGEN_BODIES` to keep the property test focused on
edit-safety rather than parser limits.

### [6.23.2] `_run_url` now guards `fetch_site` against exceptions — 2026-04-17

Real `fetch_site` catches all network/parse errors internally (see `fetcher.py:249-256`) and returns an empty tuple, so the `fetch_ok = bool(records)` branch in `_run_url` already covered real-world fetch failures. The Phase 6 integration test for `TestInitUrlFetchFailureWritesScrapeNone` deliberately mocks `fetch_site` with `side_effect=_fetch_site_network_error` to simulate an exception escaping — PLAN.md § "test_init_url_fetch_failure_writes_scrape_none" demands this shape. `_run_url` now wraps the `fetch_site` call in `try/except Exception` and records a diagnostic so the URL-flow can still produce the template-with-`scrape: none` SPEC.md on that path. The try/except is defensive against a future `fetch_site` variant that forgets the internal catch (or a deeper exception like `SystemExit`-adjacent errors that slip through), not load-bearing in production today.

### [4.4.5] `## Sources` false positive in fenced code blocks — 2026-04-13

The multiline regex `^## Sources\s*$` in `needs_migration()` matches even when `## Sources` appears inside a fenced code block (e.g. a Markdown example in the SPEC.md top-matter comment). This is a known false positive, accepted as intentional: a file containing `## Sources` in an example is close enough to new-format that force-migrating it would be worse than letting it through. Pinned with `test_sources_inside_fenced_code_block`. If fence-aware parsing is added later, the test will break to flag the behavior change.

## [2.2] Follow links — 2026-03-05

- Low-priority pages (blog, pricing, legal, login, etc.) are skipped entirely rather than deprioritized. The rationale: they add no signal about the product's features/architecture and would waste the max_pages budget. This is a deliberate design decision worth revisiting if we find we need breadth over depth.
- `score_link` checks both URL path and anchor text so a link to `/page` with anchor "API Reference" is still classified as high-priority. URL path alone would miss many navigation links.
- Duplicate links in the queue are prevented via a `queued` set (in addition to the `visited` set), so the same URL won't be enqueued multiple times from different pages.
- `fetch_site` silently skips pages that fail to fetch (network errors, non-2xx), so a single broken link doesn't abort the crawl. Consider logging skipped URLs in a future pass.
- The seed URL is given a score of 2 (higher than any discovered link) to ensure it is always visited first.
- `_LOW_PRIORITY` and `_HIGH_PRIORITY` are checked in that order; a URL matching both (unlikely but possible, e.g. `/docs-pricing`) would be classified as low-priority. This could be reconsidered.

## [1.3] Verify pip install -e . works and duplo command runs — 2026-03-05

- The `.venv` was created without `setuptools`, which is required by `setuptools.build_meta`. Plain `pip install -e .` fails with `BackendUnavailable`. Fix: install setuptools first (`pip install setuptools`).
- SSL certificate verification fails in the Claude Code sandbox environment (`OSStatus -26276`). Workaround: `--trusted-host pypi.org --trusted-host files.pythonhosted.org`. This is a sandbox/environment issue, not a project issue; normal installs outside the sandbox work fine.
- `pip install -e .` also requires `--no-build-isolation` once setuptools is installed in the venv, otherwise pip tries to re-download setuptools into an isolated build env and hits the SSL error again.
- Consider documenting the install steps in a README or Makefile for first-time setup.

## Hypotheses

### [6.15.1] Per-section bullet wording drift from INIT-design.md example — 2026-04-17

INIT-design.md § "duplo init --from-description description.txt" shows one
specific output example where Architecture is filled and Behavior is empty.
The current implementation generates bullets dynamically based on the
drafted `ProductSpec`: always one bullet per required/optional section
indicating filled vs. not. This is more informative but does drift from
the example shapes in the design doc. If the doc is read as prescriptive
(exact wording for exact cases) rather than illustrative, the wording
may need tightening. Left as-is pending user review of the rendered
output during the combined-case implementation (6.15.2+).

### [5.38.2] `claude -p --tools Read` output format — 2026-04-16

`query_with_images` runs `claude -p --tools Read`. The most likely cause of universal parse failure is that `claude -p` with `--tools` outputs in a structured format (e.g., streaming JSON, JSONL with tool-use messages, or a result wrapper object) rather than plain text. If the output contains multiple JSON objects (one per tool use + final response), `extract_json` would find the first `{` and last `}` across the entire output, producing an invalid JSON candidate that spans multiple objects. This would fail `json.loads` and hit the "parse error" path. The diagnostic logging added in 5.38.2 will capture the actual raw response to confirm or eliminate this. Potential fixes: (a) add `--output-format text` to the `claude -p` command, (b) parse the structured output to extract only the final text block, or (c) split the output by lines and extract JSON from only the last text block.

## Eliminated

### [5.39.4] Frame describer ↔ design extractor entanglement — 2026-04-16

Investigated whether the frame_describer bug (all frames getting "unknown" state) could cause the design extractor to produce empty output. **They are independent pipelines.** `extract_design` receives raw image paths (via `collect_design_input`) and sends them directly to Vision — it never consumes frame descriptions. Frame descriptions are consumed only by `extract_verification_cases` for PLAN.md verification tasks. Both bugs shared the same root cause (`strip_fences` + `json.loads` fragility, fixed in [5.38.3] and [5.39.1] by switching to `extract_json`), but they cannot cause each other. Eliminated by code path tracing: `collect_design_input` → `extract_design` → `query_with_images` (image paths); vs. `describe_frames` → `load_frame_descriptions` → `extract_verification_cases` (frame descriptions).

1f047e7: Added structured platform entries to support multi-stack projects. The parser now accepts a list of PlatformEntry objects, bypassing LLM extraction when structured data is present. Build preferences are now stored and validated per stack, with caching that includes both prose and structured entries. Downstream code temporarily uses only the first entry until the parser is fully wired.

852f88d: Added platform profile resolution to the main pipeline. The system now matches build preferences against registered platform profiles, deduplicates results, and announces matched profiles or notes when none are found. This enables platform-specific rules to influence roadmap generation and planning.

4abf331: Added platform-specific rules to the planner's system prompts. The phase plan and next-phase plan functions now accept an optional platform addendum parameter, which appends platform knowledge (like using Swift Package Manager) to the system prompt when provided. This ensures generated plans adhere to platform constraints.

37f3c2b: Added platform scaffolding to generate project files like run.sh and .gitignore entries during the first phase. This ensures tasks can reference these artifacts instead of recreating them. The scaffolding respects existing files and avoids duplication.

63356ea: Added automatic generation of CLAUDE.md file to keep platform rules synchronized with the resolved stack. The file includes project name, stack configuration, platform-specific rules, and optional local overrides. It's regenerated whenever platform profiles are present to ensure consistency across runs.

36ce4ab: Added support for a user-owned `local.md` file that holds project-specific overrides. The file is automatically git-ignored and its contents flow into both the planner prompt and CLAUDE.md, allowing per-project customization without affecting the shared configuration.

8168e7e: Changed plan generation to produce a complete multi-phase PLAN.md covering all roadmap phases at once, rather than interactively selecting features for a single phase. Verification tasks are now added only to the first phase's plan. The pipeline now loops through each roadmap phase, generating and saving its plan sequentially, and prints a summary when all phases are ready.

271c4ab: Replaced subprocess.run with Popen to stream output and show progress dots during long-running Claude CLI calls. Added timeout handling, proper stdin writing, and concurrent stream draining. Updated tests to mock Popen behavior and verify progress indicators.

7f71fca: Fixed a bug where visual design requirements were placed before the phase heading in PLAN.md, causing mcloop's phase parser to treat them as a preamble and break task dispatch. Now design sections are inserted after the Phase 0 heading, ensuring they are correctly recognized as part of the phase body.

de79987: Added a helper to ensure generated phase plans start with a proper H1 heading, improving compatibility with downstream parsing. Updated the plan generation to prepend a formatted heading when missing, using project name, phase number, and title. Added comprehensive tests to verify heading detection, fallback behavior, and edge cases.

d0ee5a9: The monolithic main.py was split into three modules: main.py (CLI dispatch and signal handling), pipeline.py (orchestration and fix mode), and status.py (display helpers). This improves code organization and maintainability while preserving backward compatibility for existing test patches. A bulk script updated over 1000 test patches to point to the new module locations.

f772c16: Fixed a stale test that mocked time values below the updated CLI timeout, causing a StopIteration error instead of the expected timeout exception. Removed design requirement injection into PLAN.md, as visual design specifications are now written to CLAUDE.md to avoid breaking mcloop's phase parser. Updated tests to reflect that design blocks are no longer inserted into the plan.

dc48670: Added deduplication to verification case extraction, preventing duplicate input/expected pairs from multiple frames. Fixed a stale test mocking outdated CLI timeouts and moved design specifications from PLAN.md to CLAUDE.md to avoid parser conflicts. Updated tests to verify deduplication behavior and reflect the design block relocation.

18df8b8: Added retry logic to the Claude CLI wrapper functions. Both query() and query_with_images() now attempt up to 3 retries with a 5-second delay between attempts when they encounter timeouts or non-zero exit codes. This improves reliability for transient failures.

782a12e: Added a top-level project header to PLAN.md so it starts with the app name, description, and platform/constraints line before any phase content. This matches duplo's own structure and ensures mcloop compatibility. The header is only written when PLAN.md is created fresh, preserving existing files during resumption.

b934d90: Added a project header to PLAN.md for mcloop compatibility, ensuring it starts with app name, description, and platform/constraints. Updated planner instructions to forbid repeating platform/language boilerplate in each phase, as that information is now only in the header. Added a corresponding test to verify the new rule.

ad4498b: Changed verification section headers from Markdown H2 to HTML comments to avoid cluttering the verification document. Updated corresponding tests to check for the absence of H2 headers instead of their presence.

0d85fcf: Removed the automatic injection of a "## Bugs" section in PLAN.md files generated by duplo, as that is an mcloop convention. The planner now strips any "## Bugs" heading from LLM output while preserving tasks that were under it. Tests were updated to verify the section is never emitted.

4e616f4: Added a new helper to strip trailing commentary after the last task in generated plans, fixing cases where LLMs append extra text after code fences. Updated the plan generation pipeline to apply this cleanup after stripping fences and adding headings. Added comprehensive tests for the new behavior.

73dd3e1: Fixed a bug where a function intended to strip trailing commentary after tasks was incorrectly normalizing formatting, breaking existing tests. The fix ensures the function only strips actual trailing content and preserves exact input formatting when no commentary exists. Added tests to lock in this behavior and prevent regression.

de2b816: Added guidance for marking tasks requiring visual confirmation or manual interaction with [USER] tags. Updated test to verify this rule is included in system prompts.

6371c67: Updated the file creation detection regex to capture any file path inside backticks, not just those starting with "Sources/" or "Package". This allows the pipeline to correctly track all files created in earlier phases, including test files, preventing duplicate creation in later phases.

## Observations

[2] [T-000002] `mypy .` is not fully green on the baseline tree: ~6 pre-existing
`[no-any-return]` / `[var-annotated]` errors live in unrelated test files
(test_pipeline.py:78, test_planner.py:92, test_init.py:249, test_task_matcher.py:82,
test_status.py:53, and similar). Confirmed by `git stash` + `mypy .` on the clean
tree. The T-000002 regression tests (tests/test_plan_author_role.py,
tests/test_plan_author_e2e.py) add zero mypy errors; "keep mypy green" here means
introducing no new errors, not clearing the pre-existing unrelated backlog.
