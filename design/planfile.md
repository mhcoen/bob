# `bob_tools.planfile`: deterministic PLAN.md handling

Status: draft for discussion. Scope: define a single library that owns
PLAN.md syntax, parsing, serialization, and operations across McLoop,
Duplo, and human editors, so that PLAN.md handling stops being
LLM-mediated and starts being deterministic.

This document is structured for cold review. Every claim about
existing behavior cites the file and function it was verified against.
Every proposed addition is marked as such. Open questions are flagged
explicitly at the end.

---

## 1. Problem

PLAN.md is currently produced, parsed, and mutated by three actors:
McLoop's runner, Duplo's planner, and humans editing directly.
McLoop has a real parser
(`mcloop/checklist.py:parse`, line 151); Duplo writes through an
LLM and a partial postprocess; humans edit freely. The three actors
do not share a single contract, and the LLM-mediated paths have
produced corruption that McLoop's parser explicitly refuses to
auto-fix (`PlanCorruptionError`, `mcloop/checklist.py:27`; the
fail-closed message is assembled at lines 142-148).

The goal is to make PLAN.md handling deterministic without removing
the human-editable property. Concretely:

- One library owns parsing, serialization, validation, and operations.
- McLoop and Duplo call the library; the LLM never owns plan structure.
- Humans may still hand-edit, but tools refuse to "best-effort" parse
  malformed input. Errors are line/column-located.
- The library coexists with the Plan Ledger
  (`bob_tools/ledger/`); it is not a replacement for the audit/event
  surface that already exists.

## 2. Current state (audit)

This section records what is already on disk, with citations, so the
design does not invent state that already exists or contradict
behavior that the runner depends on.

### 2.1 McLoop's parser

`mcloop/checklist.py` is the working parser. It defines:

- `CHECKBOX_RE = r"^(\s*)- \[([ xX!])\] (.+)$"` (line 9). Four markers
  are admitted: space (todo), `x`/`X` (done), `!` (failed).
- `STAGE_RE = r"^#+\s+.*?\b(?:stage|phase)\s+(\d+)\b"` (line 12). Any
  heading level whose title contains `Stage N` or `Phase N` is a
  section header. The `\d+` is mandatory — bare digits only.
- `BUGS_RE = r"^#+\s+Bugs\s*$"` (line 14). Any heading level titled
  exactly `Bugs` is the bug-priority section.
- Four operational tags: `[USER]` (`_USER_TAG`, line 22),
  `[BATCH]` (`_BATCH_TAG`, line 23), `[AUTO:<action>]`
  (`_AUTO_TAG_RE`, line 24), and `[RULEDOUT]` as a non-task
  indent-attached line (handled in `parse`, lines 189-204).
- `PlanCorruptionError` (line 27) is raised on duplicate H1 titles,
  multiple `## Bugs` sections, or duplicate phase/stage numbers.
  Auto-fixing is deliberately not attempted (the docstring at line
  26 records the rationale).

The status enum is **three-valued**: `[ ] / [x] / [!]`. The `[USER]`
token is an orthogonal in-text tag, not a checkbox state.
`is_user_task` (line 627) substring-searches the task text.

### 2.2 McLoop's execution semantics

`mcloop/main.py:run_loop` consumes the parsed tree and dispatches on
the tags:

- `[AUTO:<action>]` tasks call `_handle_auto_task(label, action, args)`
  and are checked off automatically. The action vocabulary
  (`run_cli`, `run_gui`, `screenshot`, etc.) is interpreted by
  `mcloop/investigate_cmd.py`.
- `[USER]` tasks pause the run, print formatted instructions, and
  accept free-form text input. A failed `[USER]` verification files
  a new bug into BUGS.md and sets `terminal_failure`; the `[USER]`
  task itself stays unchecked for re-verification after the bug is
  fixed.
- `[BATCH]` parents collect consecutive unchecked children via
  `get_batch_children` (line 654) and run them as a single
  session. `[USER]` or `[AUTO:…]` children halt batch collection.
- `[RULEDOUT]` lines feed `get_eliminated(tasks, task)` whose result
  is appended to the task prompt as a "RULED OUT APPROACHES" block,
  suppressing repeat strategies (verified in
  `mcloop/runner.py`, lines 376-385, 422-431, and 482-491).

`find_next` (line 434) implements the actionable-task algorithm:

1. Bug tasks (under `## Bugs`) have absolute priority.
2. Otherwise, only tasks in the first incomplete stage are returned.
3. Failed subtasks (`[!]`) block all later siblings under the same
   parent (implicit sequential dependency,
   `_search_tasks`, lines 369-376).
4. A failed child prevents parent completion
   (`_auto_check_parents`, lines 730-756).
5. Auto-check parent when all children are done.

`clear_failed_markers` (line 593) resets `[!]` back to `[ ]` on
`--retry`, anchored to checkbox syntax so prose containing the
literal `- [!]` is not corrupted (lines 607-610).

### 2.3 The Plan Ledger

`bob_tools/ledger/` is already shipped and integrated into McLoop:

- `bob_tools/ledger/SCHEMA.md` defines the on-disk format
  (`PLAN.events.jsonl`, append-only, one JSON object per line,
  envelope with `event_id` UUIDv7, `seq`, `writer_id`, `run_id`,
  `payload`).
- `mcloop/main.py` imports from `mcloop.ledger_config`,
  `mcloop.ledger_emit`, `mcloop.ledger_pause` and calls
  `_ledger_settle(label, TaskOutcome(...))` after every task in
  the standard code-edit success/failure path. Exit code 5 is
  reserved for ledger-induced hard stops (`HardStop`, `main.py`,
  lines 218-244).
- `mcloop/ledger_config.py` gates the ledger with, in precedence
  order, CLI flags, `MCLOOP_NO_PLAN_LEDGER` /
  `MCLOOP_NO_AUTO_REAUTHOR`, `.orchestra/config.json`, and finally
  auto-detection of `<project>/.duplo/ledger/` (lines 1-17 and
  70-142).
- `mcloop/ledger_pause.py` evaluates thresholds after settlement and
  either returns no pause, invokes Duplo reauthor, or raises
  `HardStop`. It is explicitly fail-closed: failed reauthor leaves
  PLAN.md unchanged and records only the triggering crossing on the
  ledger (lines 1-22 and 297-420). Planfile does not need a separate
  "paused state" check; the settle/evaluate path already owns that
  decision.
- The ledger's status model is **richer than the parser's**:
  `pending | active | completed | abandoned | superseded | split |
  merged | blocked | provisional` (SCHEMA.md "Phase status enum").
- `TaskOutcome` (in `mcloop/ledger_emit.py:TaskOutcome`) carries
  `success`, `abandoned`, `summary`, `changed_files`,
  `failure_kind`, `transcript_ref`. The `failure_kind` field
  records `"commit_failed"`, `"max_retries_exceeded"`, etc.

### 2.4 Phase identity in the ledger today

`mcloop/ledger_emit.py` resolves phase IDs from PLAN.md via two
regexes:

```python
_PHASE_HEADER_RE = re.compile(
    r"^##\s+Phase\s+(?P<id>[A-Za-z0-9_]+):\s+(?P<title>.+?)\s*$"
)
_PHASE_ID_COMMENT_RE = re.compile(
    r"<!--\s*phase_id\s*:\s*(?P<id>[A-Za-z0-9_]+)\s*-->"
)
```

`find_explicit_phase_id_for_task(plan_text, task_label)` iterates
line by line. A line matching `_PHASE_HEADER_RE` sets
`current_phase_id`. A line containing the `task_label` as a
substring returns the current `current_phase_id`. A line matching
`_PHASE_ID_COMMENT_RE` updates `current_phase_id` for subsequent
tasks.

`resolve_phase_id(plan_path, task_label, ordinal_index)` implements
the explicit-required / ordinal-degraded contract. When the
explicit lookup yields nothing, `ordinal_index` maps to the n-th
phase header found. When that path runs, `record_phase_id_fallback`
prints a stderr warning and emits a `finding_observed` event tagged
`degraded`.

### 2.5 The format incompatibility

McLoop's `STAGE_RE` requires `\bphase\s+\d+\b` — bare digits after
"phase". The ledger's `_PHASE_HEADER_RE` accepts
`[A-Za-z0-9_]+` after "Phase". The following table shows what each
heading produces:

| Heading                                            | McLoop STAGE_RE     | Ledger header regex             |
|----------------------------------------------------|---------------------|---------------------------------|
| `## Phase 1: Core`                                 | matches (stage 1)   | matches, `phase_id="1"`         |
| `## Phase phase_001: Core`                         | **does not match**  | matches, `phase_id="phase_001"` |
| `## Stage 1: Core`                                 | matches (stage 1)   | does not match                  |
| `## Stage 1: Core` + `<!-- phase_id: phase_001 -->`| matches (stage 1)   | matches via comment             |

Only the dual-line form satisfies both regexes today **and**
delivers a stable string phase_id distinct from the ordinal. The
single-heading `## Phase phase_001:` form breaks McLoop's stage
detection.

A second-order consequence: a hypothetical `## Phase 1: ...` heading
would match both regexes, but the ledger would derive
`phase_id="1"` from it. The current Duplo PLAN.md uses H1 phase
headings (`# Phase 1: ...`), which McLoop's `STAGE_RE` accepts but
the ledger header regex does not. In both cases, the stable-ID
migration cost is one `<!-- phase_id: phase_NNN -->` line per phase,
with no rewrite of the visible heading.

### 2.6 PLAN.md / ledger directionality

`run_loop`'s success path calls `check_off(active_file, task)` to
write `[x]` to PLAN.md *first*, then calls
`_ledger_settle(label, TaskOutcome(success=True, ...))` which emits
a `commit_landed` event as audit evidence of what PLAN.md already
records. PLAN.md is the writable surface; the ledger is the
append-only witness. Hand-edits flow PLAN.md → ledger, never the
reverse.

## 3. Architecture

### 3.1 Library placement

```
bob/
  bob_tools/
    ledger/       # already exists; owns events + projected state
    planfile/     # NEW; owns PLAN.md syntax + canonicalization
      model.py
      parser.py
      renderer.py
      operations.py
      fileio.py
      cli.py
      tests/
```

The two libraries are peers. They cross at exactly two values:

- `phase_id`: written and read by `planfile` via headings and
  `<!-- phase_id: ... -->` comments; consumed by `ledger` in event
  payloads.
- `task_label`: computed by `planfile` from position and stable IDs;
  resolved to `phase_id` (and, when stable task IDs land, to
  `task_id`) by the McLoop settle hook.

`mcloop.checklist` becomes a thin compatibility wrapper or
disappears once callers migrate. `mcloop.plan_split` and Duplo's
planner write through `planfile`.

### 3.2 Function inventory (proposed)

Pure operations on typed objects:

```
parse_plan(text)               -> Plan | PlanSyntaxError
render_plan(plan)              -> str
validate_plan(plan)            -> None | PlanValidationError
canonicalize(text)             -> str
migrate(plan)                  -> Plan
next_tasks(plan, limit=1)      -> list[Task]
complete_task(plan, task_id, outcome=None) -> tuple[Plan, tuple[Settlement, ...]]
fail_task(plan, task_id, reason, outcome=None) -> tuple[Plan, tuple[Settlement, ...]]
reset_task(plan, task_id)      -> tuple[Plan, tuple[Settlement, ...]]
add_task(plan, phase_id, text, deps=(), parent_id=None) -> Plan
replace_phase(plan, phase_id, new_phase) -> Plan
resolve_task_context(plan, task_label_or_id) -> {phase_id, task_id, label}
check_consistency(plan, events) -> None | PlanInconsistencyError
```

File operations wrap those with locking and atomic writes:

```
load(path) -> Plan
save(path, plan) -> None
update(path, operation) -> Plan
```

Implementation rule: only `parse_plan` and `render_plan` touch text.
Everything else operates on typed objects.

The split between `canonicalize` and `migrate`:

- `canonicalize(text)` is lossless formatting: parse the input,
  re-render in canonical form. Does **not** assign IDs or add
  phase-id comments. Input that already has IDs round-trips through
  `canonicalize` unchanged; input without IDs is rendered without
  IDs (compatibility output).
- `migrate(plan) -> Plan` mutates identity: assigns `T-NNNNNN:` IDs
  to tasks that have none, adds `<!-- phase_id: phase_NNN -->`
  comments to phases that have none. Otherwise leaves the plan
  unchanged. Identity assignment is not formatting and must be
  explicit, not folded into a "format" operation.
- `bob-plan fmt PATH` is the composition: parse, migrate, save.
  ```python
  plan = parse_plan(text)
  plan = migrate(plan)
  save(path, plan)
  ```

Mutation operations return a tuple of Settlements rather than a
single one. A direct completion that also auto-checks a parent
returns `(direct_settlement, derived_no_event_settlement)` — explicit
and testable. Callers do not infer derived state by diffing Plans.
For tasks with no derived effects the tuple has one element.

## 4. Canonical syntax

The grammar is a strict subset of GFM Markdown. McLoop's existing
syntax is the floor; the only **additions** are stable IDs and the
optional `<!-- phase_id: -->` comment (which the ledger already
recognizes).

### 4.1 Example

```markdown
<!-- bob-plan-format: 1 -->

# Project Name

Project prose is allowed here, before the first phase heading.

## Stage 1: Core
<!-- phase_id: phase_001 -->

Phase prose is allowed here, before the first task.

- [ ] T-000001: [BATCH] Markdown checklist parser
  - [ ] T-000002: Parse tasks from markdown checkboxes
  - [ ] T-000003: Find the next unchecked item
    [RULEDOUT] Do not identify tasks by text alone; duplicate text is legal.
  - [x] T-000004: Check off completed items in the file
- [ ] T-000005: [USER] Launch the app and confirm the main window appears
- [ ] T-000006: [AUTO:run_cli] mcloop --dry-run
- [!] T-000007: Fix failing import path

### Manual verification

- [ ] T-000008: Run the smoke test by hand

## Bugs

- [ ] T-000009: Crash on empty PLAN.md
```

### 4.2 Productions

```
Plan        ← Magic Preamble? PhaseOrBugs+ EOF
Magic       ← "<!--" WS? "bob-plan-format:" WS? Int WS? "-->" NL
Preamble    ← H1 NL Prose?
PhaseOrBugs ← Phase / BugsSection
Phase       ← PhaseHead PhaseIdComment? Prose? Subsection* Item+
PhaseHead   ← "##" WS ("Stage" / "Phase") WS Int ":" WS Text NL
PhaseIdComment ← "<!--" WS? "phase_id:" WS? Word WS? "-->" NL
Subsection  ← "###" WS Text NL Prose?
BugsSection ← "##" WS "Bugs" NL Item*
Item        ← Indent* "-" WS "[" Status "]" WS TaskId Tag* Text Annot* NL DepsLine? Item* RuledOut*
Status      ← " " / "x" / "X" / "!"
TaskId      ← TaskRef ":" WS                      ; the colon-suffixed form used as a task's own ID
TaskRef     ← "T-" Digit+                         ; the bare form used to reference another task
Tag         ← FlagTag / ActionTag                 ; leading position only (§4.3)
FlagTag     ← "[" ("USER" / "BATCH") "]" WS
ActionTag   ← "[AUTO:" Word "]" WS Text?          ; args are remainder of text
Annot       ← WS "[" Key ":" WS Value "]"         ; e.g. [feat: "..."], [fix: "..."]
DepsLine    ← Indent* "@deps" (WS TaskRef)+ NL    ; bare IDs, no trailing colon
RuledOut    ← Indent* "[RULEDOUT]" WS Text NL
Indent      ← (WS WS)+                            ; numeric nesting, 2-space canonical
Prose       ← (! Item ! PhaseHead ! Subsection . )* NL
```

Notes on the grammar:

- Indentation is significant for nesting but **numeric**, not fixed.
  A child task requires strictly greater indent than its parent.
  The canonical renderer emits two-space units; the parser accepts
  any consistent unit ≥ 1 space. (Duplo's PLAN.md uses 2; McLoop's
  uses 3; PLAN.EXAMPLE.md uses 2.)
- Unknown bracket tags are rejected by validation, not silently
  ignored. New tags require a library change.
- Prose is allowed in three places: preamble after H1, phase prose
  after a phase heading, and subsection prose after a `###`
  heading. Loose checklist items inside Prose are syntax errors.
- The `<!-- bob-plan-format: 1 -->` magic line is recommended but
  optional during migration; its absence triggers compatibility
  mode (see §8).

### 4.3 Tag families

Three families. Position rules are stricter than McLoop's current
substring-matching behavior: operational tags are recognized **only
in the leading position** of a task line, immediately after the
checkbox and task ID. This is a deliberate departure from McLoop,
which classifies any task whose text contains `[USER]` anywhere as
a USER task. A task description that references the tag form in
prose (in quoted code, in example text, in references to the syntax
itself) is not classified as that operational kind.

1. **Flag tags** (`[USER]`, `[BATCH]`): must appear immediately
   after the checkbox and task ID, before the task description.
   `[USER]` or `[BATCH]` appearing later in the text is prose, not
   a tag.
2. **Action tags** (`[AUTO:<action>] <args>`): must appear
   immediately after the flag tags (if any) and before the task
   description. The argument string is the text from the closing
   bracket of the action tag to end of line. `[AUTO:...]` tokens
   appearing later in the text are prose.
3. **Key-value annotations** (`[feat: "..."]`, `[fix: "..."]`): at
   end of line. Multiple allowed.

Compatibility mode parses leading tags as tags and treats non-leading
bracketed tokens that resemble flag or action tags as prose. The
parser may emit a validation warning when a non-leading token like
`[USER]` appears inside task text — the warning is opt-in; the
default is silent acceptance as prose.

The `migrate` operation does **not** auto-promote non-leading
bracketed tokens into operational tags. Migrating a plan whose task
description mentions `[USER]` in prose leaves the bracket form as
text; it does not silently convert the task into a USER task.

`[RULEDOUT]` is **not** a task tag. It is a sibling line at the
child indent under the task it pertains to, matching McLoop's
existing convention. Reserved for the indent-attached rule.

## 5. Status model and settlement contract

PLAN.md keeps the three-valued checkbox. The ledger keeps the rich
status enum and task-outcome evidence. These surfaces are related,
but they are not currently a strict bidirectional invariant.

Current behavior:

- Standard code-edit success checks off the task in PLAN.md, then
  calls `_ledger_settle(..., TaskOutcome(success=True, ...))`
  (`main.py`, lines 1673-1712). `ledger_emit` emits
  `commit_landed` only if a git HEAD sha exists (lines 382-425 and
  subsequent success branch).
- Standard retry exhaustion marks the task `[!]`, then calls
  `_ledger_settle(..., TaskOutcome(success=False, abandoned=True,
  failure_kind="max_retries_exceeded"))` (`main.py`, lines
  1761-1778).
- Commit failure calls `_ledger_settle(..., failure_kind="commit_failed")`
  but does not currently mark the checkbox `[!]` before the terminal
  failure break (`main.py`, lines 1652-1664). This is a current
  inconsistency, not a planfile design rule.
- `[AUTO]` and successful `[USER]` tasks are checked off directly and
  do **not** currently call `_ledger_settle` (`main.py`, lines
  1181-1216).
- Parent auto-checking can mark parent tasks `[x]` as a derived
  operation without emitting a separate ledger event
  (`checklist.py`, lines 730-756).

Target contract after McLoop migrates to planfile:

```
Every planfile-mediated task settlement returns:
  - the new Plan text state
  - a Settlement describing what ledger event, if any, the caller
    should emit
  - whether the checkbox state is direct or derived
```

The intended mapping is:

- Direct success with a commit -> `[x]` plus `commit_landed`.
- Direct success without a commit, including `[AUTO]` / `[USER]`
  verification -> `[x]` plus `work_observed`. This uses an existing
  ledger event type and keeps human/automatic verification visible
  without inventing a task-specific schema before the ledger is ready
  for `attributed_task_id`.
- Direct terminal task failure -> `[!]` plus `test_failed`.
- Derived parent completion -> `[x]` with
  `ledger_event_required=False`.
- Resetting `[!]` to `[ ]` via retry -> no ledger event; it is an
  operator decision to retry existing work, not evidence about the
  implementation.
- No outcome yet -> `[ ]`.

The library exposes a checker for this target contract:

```
check_consistency(plan: Plan, events: list[Event]) -> None | PlanInconsistencyError
```

The checker must understand the exceptions above. It should flag
only contradictions, not intentional ledger gaps such as derived
parent completion or explicit no-ledger settlements.

The directionality stays as it is in the code today: PLAN.md is
written first, the ledger event is emitted as evidence. The library's
mutation operations (`complete_task`, `fail_task`, `reset_task`)
return both the new `Plan` and a tuple of settlement descriptors:

```python
new_plan, settlements = complete_task(plan, "T-000001",
    outcome=TaskOutcome(success=True, ...))
save(path, new_plan)
for settlement in settlements:
    if not settlement.ledger_event_required:
        continue
    storage.append(build_ledger_event(settlement))
```

The caller maps each `Settlement` into a concrete ledger event
(e.g. `commit_landed`, `work_observed`, `test_failed`) before
appending. The `Settlement` itself carries only the descriptor
fields listed above (kind, task_id, phase_id, summary,
failure_kind, ledger_event_required); construction of the
wire-format event is the caller's responsibility, because event
payloads need run-context (run_id, writer_id, transcript_ref) that
the planfile library does not own.

The tuple form makes derived parent completion explicit: completing
a leaf that also auto-checks its parent returns two settlements
(the leaf's `commit_landed` or `work_observed`, then the parent's
`kind="none"` derived settlement). Callers iterate the tuple and
emit only the settlements whose `ledger_event_required` is true.
For leaf tasks with no derived effects the tuple has one element.

The two writes stay coupled at the call site, not at the library
boundary. This matches the existing `_ledger_settle` flow rather
than fighting it.

## 6. Semantics: `next_tasks`

The algorithm preserves McLoop's `find_next` behavior. A task is
actionable iff:

1. Status is `[ ]`.
2. Every task ID listed in the task's `@deps` line (per §4.2) is
   complete (`[x]`). Dependencies that do not resolve to a known
   task are validation errors (raised by `validate_plan`), not
   actionability blockers; `next_tasks` does not raise on unknown
   refs. **Contract**: `next_tasks` assumes a validated Plan.
   Callers must run `validate_plan` before scheduling. CLI
   subcommands that schedule (`bob-plan next`, `bob-plan done`,
   `bob-plan fail`) call `validate_plan` internally before
   dispatching to `next_tasks` or mutation operations; library
   callers that bypass the CLI must do the same.
3. No failed (`[!]`) ancestor exists.
4. If the task has children, return the first actionable child
   before the parent.

Priority and scoping:

1. Tasks under `## Bugs` have absolute priority over phase tasks.
2. Within phase tasks, only tasks in the **first incomplete phase**
   (document order) are returned. Later phases are invisible until
   the current phase is fully `[x]`.
3. Failed subtasks block later siblings under the same parent
   (implicit sequential dependency).
4. A failed child prevents parent completion. Parent completion is
   **derived** from children (no independent parent state).

Output:

```
next_tasks(plan, limit=1) -> list[Task]
```

A `[USER]` task surfaces as "halt and surface to human"; a
`[BATCH]` parent surfaces as one unit (its actionable children
joined); an `[AUTO:<action>]` task surfaces with its action and
args.

## 7. Phase and task identity

### 7.1 Phase identity

Three mechanisms, in resolution order:

1. `<!-- phase_id: phase_NNN -->` comment immediately after the
   phase heading. **Canonical form** going forward. Already
   recognized by `ledger_emit.find_explicit_phase_id_for_task`.
2. Legacy `## Phase phase_NNN: Title` heading. Recognized by the
   ledger today, but **breaks McLoop's STAGE_RE**. Accepted by the
   parser for read-only migration purposes; the canonicalizer
   rewrites to form 1.
3. Ordinal fallback. The n-th phase heading in document order. Emits
   a `finding_observed` event tagged `degraded` (current behavior,
   preserved).

`planfile.resolve_task_context(plan, task_label)` becomes the single
resolver. `ledger_emit.resolve_phase_id` becomes a thin shim:

```python
def resolve_phase_id(*, plan_path, task_label, ordinal_index=None):
    plan = planfile.load(plan_path)
    ctx = planfile.resolve_task_context(plan, task_label)
    if ctx.phase_id_source == "explicit":
        return PhaseIdResolution(ctx.phase_id, "explicit", ctx.plan_phase_count)
    if ordinal_index is not None and ...:
        return PhaseIdResolution(..., "ordinal", ...)
    return PhaseIdResolution(None, "none", ctx.plan_phase_count)
```

### 7.2 Task identity

`T-NNNNNN:` IDs are mandatory in the canonical form, optional in
the compatibility-mode parser. Migration assigns IDs once on first
parse and writes them back. After migration, tasks are referenced
by ID, not by text or label.

**Caveat about ledger attribution today**: the
`commit_landed` event has `attributed_phase_id` but no `task_id`
field. Task identity flows through `test_failed.test_id` (which is
the task label, per `emit_task_lifecycle_events`). Adding stable
task IDs to PLAN.md does not by itself make commits attribute to
tasks. Two paths forward:

- **Short-term**: McLoop's settle hook maps `task_label → task_id`
  via `planfile.resolve_task_context`. The ID appears in
  `test_failed.test_id` instead of the label. `commit_landed`
  continues to attribute to phase only.
- **Longer-term**: ledger schema adds `attributed_task_id` to
  `commit_landed`. Requires a schema version bump; not part of
  this proposal.

**Caveat about substring matching**:
`find_explicit_phase_id_for_task` currently uses
`label_token in line` — pure substring match. This is brittle
today and breaks once `T-NNNNNN:` lands
(`T-000001` is a substring of `T-0000010`). The library must
tokenize and match against parsed task entries, not raw line
substrings. Pin in the contract.

## 8. Migration

Phased migration. No big-bang rewrite.

### Phase A: parser-only

1. Add `bob_tools.planfile` with the parser, renderer, validator,
   and migrator. Parser accepts both old (no IDs, headings without
   phase-id comments) and new (with IDs and comments) forms.
   Renderer emits canonical form. `migrate(plan)` is a separate
   operation that assigns missing IDs and phase-id comments.
2. `@deps` is supported from the start. The parser reads `@deps`
   lines, the renderer writes them, validation requires referenced
   IDs to exist in the plan, and `next_tasks` honors deps when
   deciding actionability. Minimal scope: dependency graph cycle
   detection is *not* required in Phase A (a cycle just blocks
   `next_tasks` from progressing past it; callers detect that as
   no actionable task remaining).
3. Add `bob-plan validate PLAN.md` and `bob-plan fmt PLAN.md`
   commands. `fmt` calls `parse_plan`, then `migrate`, then
   `save`. Idempotent.
4. McLoop and Duplo continue to use their existing parsers during
   Phase A.

### Phase B: McLoop migration

1. McLoop's settle hook calls `planfile.resolve_task_context`
   instead of `ledger_emit.find_explicit_phase_id_for_task`.
   `resolve_phase_id` becomes a shim.
2. `mcloop.checklist.parse` delegates to `planfile.parse_plan`.
   `check_off`, `mark_failed`, `find_next` delegate to
   `planfile.complete_task`, `fail_task`, `next_tasks`.
3. McLoop's parser disappears or remains as a 5-line shim.

Consumer contract: the split-plan runner is the proven, mostly-working
status quo, but `planfile` is its principled successor. The migration
does not use an interim direct-PLAN.md checklist hack. The ordered path
is: first finish the planfile operations, mutation/checkoff, and file-I/O
surface; then migrate McLoop to consume that deterministic API with
PLAN.md as the sole authoritative build document; then eliminate
CURRENT_PLAN.md, `mcloop/plan_split.py`, and the split-specific tests.

### Phase C: Duplo migration

1. Duplo's planner stops emitting raw Markdown. It produces typed
   `Plan` objects (via LLM-proposed task text, but with structure
   assigned by Duplo). Duplo calls `planfile.render_plan` to write.
2. LLM output is validated by `planfile.validate_plan` before
   write. Malformed output is rejected; Duplo retries or fails the
   phase. The LLM never owns plan structure.
3. Duplo annotations (`[feat: "..."]`, `[fix: "..."]`) are
   first-class in the typed `Plan` (fields on `Task`), not parsed
   from text.

### Phase D: cleanup

1. Delete `mcloop.checklist` if the shim has no remaining callers.
2. Decide whether `commit_landed` should gain `attributed_task_id`.
   If yes, that is a ledger schema bump, not a planfile change.

## 9. What changes for humans

Humans can still hand-edit PLAN.md. Constraints:

- Editing task text is safe.
- Editing checkboxes is safe; the consistency check will surface
  divergence from the ledger on the next run, but does not auto-
  rewrite.
- **Do not** edit `T-NNNNNN:` IDs or remove `<!-- phase_id: -->`
  comments. Either is detected and rejected with a line-located
  error.
- `bob-plan fmt PLAN.md` is safe to run anytime: it normalizes
  whitespace, sorts annotations into canonical position, and
  assigns IDs to any new tasks. Idempotent.

When McLoop refuses to run, the message is:

```
PLAN.md invalid at line 17, column 5:
expected task id like T-000123 after checkbox marker
```

No best-effort recovery. The user runs `bob-plan validate` to see
all errors, fixes them by hand, or runs `bob-plan fmt` for
structural normalization.

## 10. Out of scope

- `commit_landed.attributed_task_id`. Mentioned in §7.2 as a
  future-work item; not part of this proposal.
- Replacing the Plan Ledger. The ledger is the audit/event surface;
  planfile is the syntax surface. They coexist.
- A "running" status in PLAN.md. McLoop tracks live state in
  `.mcloop/runs/latest.json` and a lock file; a crashed run should
  not leave the plan in a "claimed" state.
- Multi-writer locking semantics for PLAN.md beyond file locking
  and atomic writes. Concurrent human + tool edits during a run
  are out of scope; McLoop already prints
  `"Do not edit CURRENT_PLAN.md or BUGS.md while mcloop is running"`
  on startup.

## 11. Open questions

1. **Should `T-NNNNNN:` be globally unique or per-phase?** Globally
   unique is simpler for ledger attribution; per-phase makes IDs
   smaller and locally meaningful. Global preferred unless there's a
   reason against.

2. **What is the migration story for the four existing PLAN.md
   files?** (Duplo, McLoop, McLoop's PLAN.EXAMPLE, and any in target
   projects.) `bob-plan fmt` should handle all four. Verify
   round-trip on each before claiming Phase A complete.

3. **Should `[RULEDOUT]` move from sibling-line to a field on `Task`?**
   The runtime behavior is identical (collected and fed to the
   prompt); the only difference is whether it appears as
   indent-attached prose or as a structured field. The current sibling-
   line form survives canonicalization unchanged; the structured form
   would require a schema decision. Recommendation: keep sibling-line
   form; treat `[RULEDOUT]` as a parser-collected per-task list in the
   `Task` object. No syntax change.

4. **How are `## Stage` and `## Phase` headings reconciled?** Both
   work today and mean the same thing. Recommendation: parser
   accepts both; canonicalizer leaves them as-is (does not rewrite
   "Stage" to "Phase" or vice versa). The keyword is cosmetic; the
   `phase_id` carries identity.

5. **What about subsection headings (`### Manual verification`)?**
   Currently used in Duplo's PLAN.md to group tasks within a phase.
   The parser must accept them. They have no semantic effect on
   `next_tasks` or `phase_id`. Recommendation: parse as structural,
   preserve through round-trip, attach contained tasks to the parent
   phase's `phase_id`.

6. **Is the settlement consistency check per-run or all-time?** The
   target contract in §5 should start per-run. All-time enforcement
   would require the ledger to be the source of truth across runs,
   which contradicts the directionality decision. Recommendation:
   per-run. Cross-run consistency is an audit command, not
   `bob-plan validate`'s default behavior.

7. **What happens when a `[USER]` task is checked off without a user
   prompt?** Today nothing checks; a hand-edit can flip
   `- [ ] [USER] Verify the menu appears` to `- [x] [USER] Verify the
   menu appears` and McLoop accepts it. Recommendation: keep the
   current permissive behavior during compatibility mode. After
   planfile-mediated settlement lands, McLoop-created `[USER]`
   completions should emit `work_observed`; hand-edited `[x]` user
   tasks remain accepted as explicit human assertions and are not
   rejected merely because no ledger event exists.
