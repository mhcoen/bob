<!-- bob-plan-format: 1 -->

# bob

The bob ecosystem workspace: deterministic control plane for stochastic
AI coding agents. This PLAN.md drives cross-package work that the
per-package PLAN.md files in `packages/<name>/` cannot cleanly host.

Run `mcloop` from this directory to drive these tasks. Check commands
run against the full workspace. Each task should leave the workspace in
a passing state: `pytest` across all packages and `ruff check` must both
pass before a commit is made.

**Phase numbering is partial ordering, not strict sequencing.** Phase 1
(plan-document foundation) is independent of Phases 2 and 3 and can be
worked in parallel with them — it is workspace-coherence infrastructure
that Phases 2 and 3 do not depend on. Phase 3 (duplo wiring) does
depend on Phase 2 (iterative design pattern) and must be completed
after Phase 2: Phase 3 calls `orchestra.run_role`, returns the
`IterativeDesignResult` defined in Phase 2, and integrates with the
`design_loop` workflow Phase 2 establishes.

## Phase 1: Plan-document foundation
<!-- phase_id: phase_001 -->

- [x] T-000001: Add optional `created_at` (ISO 8601 UTC) field to `Task` in `bob_tools.planfile.model`; populate it in `add_task` and `add_phase_task`; preserve it round-trip through the canonical parser and renderer (encoded as an HTML-comment annotation on the task line) <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000002: Backfill `created_at` for existing tasks in every PLAN.md in the workspace, best-effort from `git log --diff-filter=A` on the task line; leave null where git cannot resolve it <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000003: Add namespace prefix support to task IDs: extend the canonical ID grammar from `T-NNNNNN` to `T-XX-NNNNNN` where `XX` is a 2-letter per-file namespace declared once in the PLAN.md preamble as `<!-- task_namespace: XX -->`; legacy unprefixed IDs continue to parse but the canonical validator warns once per file <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000004: Add cross-file ID resolver `bob_tools.planfile.resolve_global(id)`: given a fully-qualified `T-XX-NNNNNN`, walk PLAN.md files under the workspace root and return the (file, task) pair or raise <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000005: Document the namespace scheme and the resolver in `bob_tools/README.md` <!-- created_at: 2026-05-26T07:11:58Z -->

## Phase 2: Iterative design pattern (modify `iterate_until_acceptable`)
<!-- phase_id: phase_002 -->

**Implementation note (added after Task 1 landed as commit `8f1d450c`):**
Task 1 created `design_loop.orc` as a new file alongside
`iterate_until_acceptable.orc` rather than renaming it, because rename
would have broken or required rewriting ~870 lines of test coverage
for the old workflow that this phase's later tasks explicitly cover.
The rename is now an explicit task (see "Retire `iterate_until_acceptable`"
below), scoped to land alongside the new `design_loop` test suite in
Task 10.

**Runtime status during Phase 2:** `design_loop.orc` parses and loads but
is non-runnable until Tasks 3 (reviewer schema), 4 (judge schema), 5
(judge prompt), and 6 (reviewer prompt) land — the state machine wants
`produce`/`revise`/`done` vocabulary but currently shares the old
`accept`/`iterate`/`stuck` schema. Do not invoke `design_loop` from
production paths until those four tasks complete.

- [x] T-000006: Edit `packages/orchestra/orchestra/workflows/iterate_until_acceptable.orc`: rename to `design_loop.orc` and restructure so the judge role runs first as the producer, the reviewer emits critique only (no convergence decision), and the judge's subsequent invocation reads the critique and either produces a revised artifact (continue) or declares done (terminate CONVERGED) <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000007: Add `orchestra.run_role(role_name: str, **kwargs)` to `packages/orchestra/orchestra/api.py` as a public entry point that reads the role binding from `~/.orchestra/config.json` (with project-local override), dispatches to the bound workflow, and returns an `IterativeDesignResult` dataclass with fields: `termination: Literal["CONVERGED", "CAPPED", "ERROR"]`, `rounds_completed: int`, `final_artifact: str` (the most recent judge-produced artifact, or empty string if ERROR before first artifact), `transcript: list[Turn]` (in-memory ordered history), `transcript_path: Path` (JSONL on disk), `run_id: str`, `error: ErrorRecord | None` (populated iff termination == "ERROR"). Define `Turn` and `ErrorRecord` in `orchestra/api.py` alongside `IterativeDesignResult`. **Termination resolution:** orchestra's executor only recognizes `done` and `stop` as terminal states, so the CONVERGED/CAPPED/ERROR distinction is inferred by `run_role` from the *transition outcome* that led to the terminal state, not from the terminal state name. Mapping: judge emits `done` action → CONVERGED; cap-hit transition with `iterate` action → CAPPED; `stuck`, `error`, or `timeout` transition → ERROR. Extend the config schema to support a nested role binding of the form `{"pattern": "design_loop", "judge": {"model": ...}, "reviewer": {"model": ...}, "max_rounds": N}`. Existing `run_workflow` callers continue to work unchanged. <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000008: Drop the `ready` boolean from the reviewer's output schema; reviewer emits only `issues` (each with `severity`, `summary`, `detail`) and a `rationale`. Define the schema at `workflows/schemas/design_loop_review.json` <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000009: Update the judge verdict schema at `workflows/schemas/design_loop_judge.json` with two invocation variants. **First invocation** (no prior critique exists) must emit `{action: "produce", artifact: <text>}` — `done` is invalid on first turn since no artifact exists yet. **Subsequent invocations** (after at least one reviewer critique) emit either `{action: "revise", artifact: <text>}` or `{action: "done", rationale: <text>}`. The workflow enforces the first-turn rule and rejects a first-turn `done` as malformed. <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000010: Write judge prompt template at `workflows/templates/design_loop_judge.md`: includes register-lock language for what qualifies as a continue-revising condition (structural / behavioral / unrecoverable issues remain) versus done (only stylistic, naming, scope-expansion items in the critique); also specifies the invocation-state contract (first turn produces, subsequent turns revise or done) <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000011: Write reviewer prompt template at `workflows/templates/design_loop_reviewer.md`: includes the symmetric register-lock language restricting issues to the three qualifying severities <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000012: Configurable round cap: replace `on iterate when attempts.judge < 6 => propose` with a parameter read from the role binding (`max_rounds`, default 4 if not specified, refuses to start if ≤ 0). The workflow guard reads this at workflow-start from the resolved role binding passed in by `run_role`; per-call override available via `orchestra.run_role("design", max_rounds=N, ...)`. Terminate `CAPPED` (distinct from `done`) when the cap is hit. <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000013: Verify the orchestra workflow executor already provides: (a) malformed-output retry-once-then-fail behavior for invalid JSON or schema-violating role output; (b) adapter-failure handling that terminates the workflow with an ERROR result while preserving the transcript up to the failure point; (c) incremental transcript writing (one Turn appended to a JSONL file per role completion, not buffered until end-of-run). If any of (a)/(b)/(c) is missing, add it as part of this phase. <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000014: Configuration binding in `~/.orchestra/config.json`: define a `design` role with judge bound to a strong model (default `opus`) and reviewer bound to a different model (default `codex`); the workflow refuses to start if both bindings resolve to the same model. The `"model"` string in each binding (`opus`, `codex`, `kimi`, etc.) resolves to an executable adapter through orchestra's existing `ProfileRegistry` — the same resolution path used by `.orc` workflow definitions when they declare `model m_proposer` etc. At workflow start, `run_role` looks up each bound model identifier in the registry; an identifier not registered fails startup with a clear error naming the missing identifier and the available identifiers. Document the resolution path in `orchestra/README.md` so PLAN-level callers know what identifiers are acceptable. <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000015: Mechanical tests in `packages/orchestra/tests/`: round threading with cap=3 converging at round 3; cap enforcement at exactly `max_rounds` (terminates `CAPPED`); same-model rejection at start; first-turn `done` rejected as malformed; malformed reviewer output recoverable on retry; malformed reviewer output fatal on second failure; malformed judge output (invalid JSON or schema violation) recoverable on retry; malformed judge output fatal on second failure; judge emits `action: "produce"` on a subsequent invocation (when `revise` or `done` is required) rejected as malformed; adapter failure preserves transcript and terminates `ERROR` (covering both judge and reviewer adapter failures); transcript JSONL is incremental (one turn per role completion) <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000016: Retire `iterate_until_acceptable.orc` and its tests. `git rm packages/orchestra/orchestra/workflows/iterate_until_acceptable.orc` and the schema/template files that exclusively belong to it. Delete or migrate the tests that depend on the old workflow name: `tests/test_workflows_iterate.py`, `tests/test_e2e_decision_consistency.py`, and the name-string references in `tests/test_decision_consistency.py` and `tests/test_calibration.py`. Remove the registrations in `orchestra/api.py`, `orchestra/executor/criteria.py`, and `orchestra/calibration/{iterate_runner.py, extract_labels.py}`. Pair this with the new `design_loop` tests from the prior task so the F2.5a decision-consistency invariant is preserved by the new test suite rather than lost. <!-- created_at: 2026-05-26T07:11:58Z -->

## Phase 3: Wire to duplo
<!-- phase_id: phase_003 -->

- [x] T-000017: Add `duplo.design.run_iterative_design(seed_input) -> str` that calls `orchestra.run_role("design", seed_input=seed_input)` and returns the final artifact text. Define duplo's behavior on each terminal state: **CONVERGED** returns the artifact normally; **CAPPED** returns the most recent artifact and logs a warning to duplo's progress channel that the design did not converge within `max_rounds`; **ERROR** raises `duplo.design.IterativeDesignError` wrapping the underlying `ErrorRecord`, with transcript path included for postmortem. <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000018: Update duplo's existing design-phase entry point to call `run_iterative_design` instead of the current single-model invocation; preserve the prior call signature so callers don't change <!-- created_at: 2026-05-26T07:11:58Z -->
- [x] T-000019: Integration test: invoke `duplo design` on a small fixture spec; assert the resulting artifact and that a transcript JSONL was written to the orchestra run directory; also assert that a forced-CAPPED scenario (using a mock workflow that always returns CAPPED) returns the artifact with the expected warning, and that a forced-ERROR scenario raises `IterativeDesignError` <!-- created_at: 2026-05-26T07:11:58Z -->

## Phase 4: Duplo LLM-call logging
<!-- phase_id: phase_004 -->

**Motivation:** duplo currently writes no record of its LLM calls. The
legacy path funnels every call through `duplo.claude_cli.query` /
`query_with_images` (plain `claude -p`, no stream-json, so not even token
counts are captured); the council path goes through orchestra adapters,
which do log. There is no way to answer "what workflow generated each
PLAN.md phase, with what prompt/response/model/tokens" after a run. This
phase adds full per-call logging to duplo, modeled on mcloop's existing
stream-json logs (which made the quota forensic analysis possible).

- [x] T-000020: Add a structured per-run log directory for duplo at `.duplo/logs/<run_id>/` (durable repo-internal path, never `/tmp`). Establish a `run_id` at duplo process start (timestamp + short random suffix) and a module-level logger in a new `duplo/call_log.py` that owns the run directory creation and JSONL append. One JSONL record per LLM call.
- [x] T-000021: Instrument `duplo.claude_cli.query` and `query_with_images` (the legacy path) to emit one `call_log` record per call with: `run_id`, ISO-8601 UTC `timestamp`, `call_site` (a caller-supplied label identifying which phase/feature/step invoked it — thread the label through as a keyword arg, defaulting to `""`), `model`, full `system` prompt, full `prompt`, full `response`, `duration_seconds` (wall-clock), `attempt` number, and `outcome` (`"ok"` | `"timeout"` | `"error"` with the error text). Records are written even on failure. No prompt/response truncation — full fidelity.
- [x] T-000022: Switch the legacy `claude -p` invocation in `claude_cli.py` to `--output-format stream-json --verbose` and parse the stream to extract token usage (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`) from the `message_start` / `message_delta` events, adding them to each `call_log` record. This is what made mcloop's quota analysis possible; without it duplo runs blind on consumption. Preserve the existing dot-progress-to-stderr behavior and the retry/timeout semantics; the only change is output format + usage extraction. If stream-json parsing fails, fall back to recording the call without token counts rather than failing the call.
- [x] T-000023: Thread a `call_site` label from each duplo phase/feature generation site into the `query` calls: `extract_features` → `"extract_features"`, `generate_roadmap` → `"generate_roadmap"`, `generate_phase_plan` → `"phase_plan:<required_phase_id>"`, design extraction → `"extract_design"`, verification-case extraction → `"verification_cases"`, investigator → `"investigate"`. The label must identify which workflow step and (for phase plans) which phase the call belongs to, so a reader can reconstruct the call pattern behind each PLAN.md phase.
- [x] T-000024: For the council path, ensure the same per-call fidelity is captured. The orchestra adapters already log to the orchestra run directory; add a `call_log` record (or a pointer/symlink) under `.duplo/logs/<run_id>/` that references the orchestra transcript path for each council-authored phase, so a single duplo run directory is the complete index of every LLM call regardless of path (legacy vs council). Record per phase: `call_site`, `path` (`"legacy"` | `"council"`), and the orchestra `run_id`/`transcript_path` when council.
- [x] T-000025: Write a `duplo logs` summary helper (or extend an existing status command) that reads a run directory and prints a per-run report: each `call_site` in order, model, path (legacy/council), duration, and token counts (input/cache/output), with a run total. Mirrors the kind of aggregation done manually on mcloop logs for the quota analysis. Read-only over the JSONL.
- [ ] T-000026: Tests in `packages/duplo/tests/`: assert a run directory is created with the expected `run_id` shape; assert `query` / `query_with_images` each emit a well-formed JSONL record with all required fields on success AND on timeout/error (mock the subprocess so no real `claude` call happens); assert token counts are parsed from a canned stream-json fixture; assert the `call_site` labels thread through from a mocked `generate_phase_plan` run; assert the `duplo logs` summary aggregates a fixture run directory correctly. No real LLM calls in any test.

## Bugs
