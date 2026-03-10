# Duplo — Phase 2: Phase Completion and Next-Phase Generation

Duplo currently handles first-run (scrape, extract features, select, generate plan) and incremental updates (detect new files, re-scrape, append gap tasks). What is missing is the phase-completion loop: when all tasks in PLAN.md are done, duplo should track what was implemented, present the remaining work, and generate a scoped next-phase plan.

This phase adds feature annotations in generated plans, deterministic status tracking in duplo.json, a next-phase flow with interactive feature selection and issue injection, and fixes to the state machine bugs that prevent any of this from working on existing projects.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all code generation. Ruff for linting, pytest for tests. All AI calls go through `claude -p` (no direct API calls).

---

- [x] Fix phase-title regex to handle app-name prefixed headings
  - [x] `append_phase_to_history` uses `r"^#\s*(Phase\s+\d+[^\n]*)"` which fails on headings like `# McWhisper — Phase 1: Core`. The same regex pattern appears in `_complete_phase`, `_advance_to_next`, `_detect_next_phase_number`, and `_subsequent_run`. All instances must be relaxed to find a phase number anywhere in the first `#` heading line, e.g. `r"^#\s*.*?(Phase\s+\d+[^\n]*)"` or extract the phase number with `r"Phase\s+(\d+)"`.
  - [x] Add tests covering headings in both formats: `# Phase 1: Core` and `# McWhisper — Phase 1: Core`

- [x] Add feature annotations to generated plans
  - [x] Modify the planner system prompt in `generate_phase_plan` so that every generated task line includes a `[feat: "Feature Name"]` annotation listing which features from the input list it addresses. Tasks addressing multiple features list them comma-separated: `[feat: "Push-to-talk recording", "Global keyboard shortcuts"]`. Tasks for bug fixes or issues use `[fix: "description"]`. Scaffolding or structural tasks that do not map to any feature use no annotation.
  - [x] Modify `generate_next_phase_plan` with the same annotation requirement
  - [x] Add a test that verifies generated plans contain `[feat: ...]` or `[fix: ...]` annotations on task lines

- [x] Add `status`, `implemented_in`, and `issues` fields to feature tracking
  - [x] Each feature dict in `duplo.json` gets two optional fields: `status` (one of `pending`, `implemented`, `partial`) and `implemented_in` (phase label string). New features default to `status: "pending"`.
  - [x] Add a `save_feature_status(name, status, implemented_in)` function to saver.py that updates a feature by name
  - [x] Add a top-level `issues` list to `duplo.json` for implementation problems not tied to a specific feature
  - [x] Add `save_issue(description, source, phase)` and `resolve_issue(description)` functions to saver.py
  - [x] Existing features in duplo.json files that lack a `status` field should be treated as `pending` by all code that reads them

- [x] Implement deterministic phase-completion tracking
  - [x] At phase completion (all checkboxes checked), parse PLAN.md for checked task lines
  - [x] For each checked line with a `[feat: ...]` annotation, mark the referenced features as `implemented` with the current phase label
  - [x] For each checked line with a `[fix: ...]` annotation, mark the corresponding issue as resolved
  - [x] For checked lines without annotations (user-added tasks or pre-annotation plans), batch them into a single `claude -p` call with the full feature list. Claude matches each task to an existing feature or confirms it is genuinely new. Mark matched features as implemented. Add genuinely new items as new feature entries with `status: "implemented"` and `implemented_in` set to the current phase.
  - [x] Add tests for the annotation parser covering annotated lines, unannotated lines, and mixed plans

- [x] Prompt for issues at phase completion
  - [x] After status tracking, before advancing to the next phase, prompt the user for known issues with the completed phase
  - [x] Multi-line input, blank line to finish, skippable
  - [x] Each line becomes an entry in the `issues` list in `duplo.json` with `source: "user"` and the current phase label
  - [x] This is where the user reports bugs (e.g. "waveform shows static bars during recording") or incomplete wiring (e.g. "qwen3-asr-swift dependency is unused")

- [x] Generate roadmap from remaining features when missing or consumed
  - [x] At the start of the next-phase flow, if `duplo.json` has no `roadmap` or the existing roadmap has been fully consumed (current_phase is past the last entry), generate a new one using `generate_roadmap` with only the remaining unimplemented features
  - [x] Pass the completion history (list of phase labels and what they implemented) as context so the roadmap builds on what exists rather than starting from scratch
  - [x] Save the new roadmap to `duplo.json`, resetting `current_phase` to 0 relative to the new roadmap
  - [x] Add a test that verifies roadmap generation excludes implemented features

- [x] Redesign `_subsequent_run` state machine
  - [x] Replace the current branching (in_progress checks, roadmap lookups, history-based detection) with a clean flow:
    - If PLAN.md exists and has unchecked items: print status summary and "Run mcloop to continue building." Exit.
    - If PLAN.md exists and all items are checked: run phase-completion flow (annotation parsing, status tracking, issue prompt). Delete PLAN.md. Fall through to next-phase flow.
    - If no PLAN.md: run next-phase flow.
  - [x] Remove the separate `_advance_to_next` code path. The single next-phase flow handles everything.
  - [x] Remove the dependency on `in_progress` for flow control. The `in_progress` key can be removed entirely or repurposed for crash recovery only.

- [ ] Implement next-phase flow with feature selection
  - [x] Re-scrape the product site, re-extract features, merge new ones into `duplo.json` (already works)
  - [ ] Partition features into implemented and remaining based on `status` field
  - [ ] If roadmap is missing or consumed, generate a new one from remaining features (previous item)
  - [ ] Use the next phase entry from the roadmap as the default recommendation during feature selection
  - [ ] Present remaining features to the user using `select_features` (numbered, grouped by category), with the roadmap recommendation labeled (e.g. "Recommended for Phase 2: 3, 7, 12, 15")
  - [ ] Show open issues from `duplo.json` and ask which should be addressed in this phase (same numbered selection pattern)
  - [ ] Update `generate_phase_plan` to accept issues alongside features. The system prompt should instruct Claude to include fix tasks for issues alongside feature-implementation tasks, ordering fixes before new feature work when there are dependencies.
  - [ ] Generate the next PLAN.md scoped to selected features + selected issues. Heading format: `# <AppName> — Phase N: <Title>`. All task lines include `[feat: ...]` or `[fix: ...]` annotations. Phase number derived from `phases` history length + 1.

- [ ] Print status summary on every run
  - [ ] Before doing any work, print: current phase number, features implemented vs. remaining, open issues count
  - [ ] Example output: `McWhisper: Phase 1 complete. 14/52 features implemented, 3 open issues.`
  - [ ] If no phases have been completed yet, print feature count and "Phase 1 in progress" or "Ready to generate Phase 1"

---

## Manual verification (user must test)

- [ ] Run duplo in the mcwhisper directory. Confirm it detects Phase 1 as complete, runs the unannotated-task matching via Claude, marks implemented features, prompts for issues, generates a roadmap from remaining features, presents feature selection with a recommendation, and generates a Phase 2 PLAN.md with proper annotations.
- [ ] Run duplo again immediately (Phase 2 not started). Confirm it prints the status summary and tells you to run mcloop.
- [ ] After mcloop completes Phase 2, run duplo again. Confirm annotated tasks are tracked deterministically (no Claude call needed), issues prompt appears, roadmap is regenerated if consumed, and Phase 3 is ready.
