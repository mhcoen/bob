# mcloop de-split post-completion audit — Claude Code stream

Reviewer: Claude Code, model `claude-opus-4-7` (1M context).
Audit date: 2026-05-22.
Audit charter:
[`bob/design/desplit-post-completion-audit.md`](desplit-post-completion-audit.md).
Format precedent:
[`bob/design/desplit-independent-validation.md`](desplit-independent-validation.md).

Object under validation: the cumulative post-cutover state of the
mcloop ↔ bob-tools de-split. Audience: CS PhD. All claims re-derived
against current source at the named refs in §0.

This stream covers items 1, 3, 4, 5 from the charter. Item 2 (Kimi
K2.6 independent re-derivation) is covered under a separate prompt;
convergence between the two streams is the closure criterion.

---

## 0. Frozen state

Created and pushed `desplit-complete` tags on both repos before any
audit step began. Both tags resolved on origin before §1 started.

| Ref | Repo | SHA | Tag |
|---|---|---|---|
| Post-D1 mcloop | `mhcoen/mcloop` | `901a0aff09329743c3f9a11c8667f59563cb8248` | `desplit-complete` |
| Post-D3 bob-tools | `mhcoen/bob-tools` | `c30bdf71fe54404ad5fb7c2a8b8847b601c9adff` | `desplit-complete` |
| Pre-B1 mcloop reference | `mhcoen/mcloop` | `d7ab3ae040a4a43633409d2f5f8271000878904a` | (parent of B1+B3 cutover `eb80d13`) |

Scratch path for capture artifacts (rehearsals, fixtures, intermediate
diffs):
`/Users/mhcoen/proj/bob-tools/.scratch/desplit-post-audit/`. `/tmp`
not used.

---

## 1. Item 1 — pre-B1 → post-D1 behavioral equivalence

### 1.1 Method

Two `git worktree` checkouts of mcloop:

| Worktree | HEAD | Purpose |
|---|---|---|
| `worktree-pre-b1` | `d7ab3ae0` (immediate parent of B1+B3 cutover) | drives `run_loop` against the pre-B1 fixture under pre-B1 `mcloop.checklist` parser |
| `worktree-post-d1` | `desplit-complete` = `901a0af` | drives `run_loop` against the post-D1 fixture under the planfile-backed `_planfile_compat` shim |

A single mcloop venv at `/Users/mhcoen/proj/mcloop/.venv` drove both
worktrees via `sys.path.insert(0, MCLOOP_SOURCE)`; the venv's editable
bob-tools install resolves to `c30bdf7` (= `desplit-complete`).
Worktree selection verified by inspecting `mcloop.__file__` and
`mcloop.main.__file__` at runtime in each capture pass.

Fixture pair lives at
`bob-tools/.scratch/desplit-post-audit/fixture-{pre-b1,post-d1}.md`.
Per `§1.1` the post-D1 form is produced by `bob_tools.planfile.migrate`
on the pre-B1 form. **Idempotence observed**: the pre-B1 fixture is
already canonical (T-NNNNNN ids, `## Stage N:` headers,
`<!-- phase_id: phase_NNN -->` comments, 2-space indent) and `migrate`
is a no-op on it. This is consistent with the integration plan's
observation that PLAN.md was already migrated to canonical form by
the time of B1 (per B3 R2's id requirement landing earlier in the May
work). The label-mapping bridge that `§1.5` reserved for the case of
post-D1-only `T-NNNNNN` prefixes is structurally unnecessary — both
worktrees produce the same task labels.

Fixture content exercises every `§1.2` requirement:
two stages with a transition, USER task, AUTO task, BATCH parent with
two children, `[RULEDOUT]` annotation, a failing task driving
`test_failed`, and a `## Bugs` section with one open and one DONE bug
task (the latter exercises purge re-home semantics from D5).

Five capture modes mirroring the B5 baseline:
`full_failure`, `retry`, `stop_after_one`, `stop_after_stage`,
`bug_only`. Stub-backed `run_loop` patches `run_task`,
`_handle_auto_task`, `_handle_user_task`, `run_checks`, `_run_build`,
`_launch_app_verification`, `notify`, and `builtins.input` — the exact
patch set used by `bob-tools/.scratch/b5-baseline/b5_capture.py`. The
fixture's `BUGS.md` is non-empty only in `bug_only`; the other four
modes use `## Bugs\n\n` so the runtime drives PLAN.md tasks rather
than short-circuiting to bug-only handling.

Capture script:
`bob-tools/.scratch/desplit-post-audit/audit_capture.py`. Diff script:
`_diff_captures.py`. Raw per-mode capture JSON at
`capture-{pre-b1,post-d1}/captures/<mode>/capture.json`.

### 1.2 Verdict table (under corrected gate (e))

Under the original charter wording, gate (e) was strict byte
equivalence on BUGS.md. Under that wording 22/25 PASS and 3/25 FAIL
on a single renderer-normalization byte. The charter authored a
correction (recorded as a deviation in §6) clarifying gate (e) to
"byte-identical modulo planfile renderer normalization", operationally
`render_plan(parse_plan(pre)) == post`. Under the corrected gate
**all 25/25 PASS**.

| Mode | (a) sequence | (b) ledger | (c) exit | (d) summary | (e) BUGS.md | Evidence |
|---|---|---|---|---|---|---|
| `full_failure` | PASS | PASS | PASS | PASS | PASS | `capture-{pre,post}/captures/full_failure/capture.json` |
| `retry` | PASS | PASS | PASS | PASS | PASS | `capture-{pre,post}/captures/retry/capture.json` |
| `stop_after_one` | PASS | PASS | PASS | PASS | PASS | `capture-{pre,post}/captures/stop_after_one/capture.json` |
| `stop_after_stage` | PASS | PASS | PASS | PASS | PASS | `capture-{pre,post}/captures/stop_after_stage/capture.json` |
| `bug_only` | PASS | PASS | PASS | PASS | PASS | `capture-{pre,post}/captures/bug_only/capture.json` |

### 1.3 Per-mode evidence

#### `full_failure`
- Sequence (pre == post):
  `['USER 1.1 Confirm fixture setup', 'AUTO 1.2 run_cli fixture-auto', 'Do all of the following in order:\n1. [T-000004] Batch child A\n2. [T-000005] Batch child B', 'Task with ruled out note', 'Failing task']`
- Exit: `1` / `1`.
- Summary:
  `{terminal_status='failure', stuck=['[T-000008] Later task'], completed_stage=None}` (both).
- BUGS.md: `'## Bugs\n\n'` / `'## Bugs\n\n'` (untouched).
- Ledger: `''` / `''` (Plan Ledger not enabled in fixture; see §1.4
  note).

#### `retry`
- Sequence: `['Retry failed feature']` (both).
- Exit: `0` / `0`.
- Summary:
  `{terminal_status='stopped', stop_reason='stop_after_one'}` (both).
- BUGS.md: `'## Bugs\n\n'` (both).

#### `stop_after_one`
- Sequence: `['USER 1.1 Confirm fixture setup', 'AUTO 1.2 run_cli fixture-auto', 'Batch child A']` (both).
- Exit: `0` / `0`.
- Summary:
  `{terminal_status='stopped', stop_reason='stop_after_one'}` (both).
- BUGS.md: `'## Bugs\n\n'` (both).

#### `stop_after_stage`
- Sequence: pre-B1 == post-D1: `['USER 1.1 Confirm fixture setup', 'AUTO 1.2 run_cli fixture-auto', 'Do all of the following in order:\n1. [T-000004] Batch child A\n2. [T-000005] Batch child B', 'Task with ruled out note']`.
- Exit: `0` / `0`.
- Summary:
  `{terminal_status='stopped', stop_reason='stop_after_stage'}` (both).
- BUGS.md: `'## Bugs\n\n'` (both).

#### `bug_only`
- Sequence: `['Open bug']` (both).
- Exit: `0` / `0`.
- Summary: `{terminal_status='success', mode='bug-only'}` (both).
- BUGS.md: pre-B1 = `'## Bugs\n\n'`, post-D1 = `'## Bugs\n'`. The
  byte difference is the planfile renderer's normalization of an
  empty `## Bugs` section's trailing blank line.
  `render_plan(parse_plan('## Bugs\n\n'))` returns `'## Bugs\n'`,
  matching post-D1 exactly. Under the corrected gate (e), PASS.

### 1.4 Notes on gate scope

- **Ledger streams empty.** The audit fixture does not configure
  Plan Ledger (`_pl_settings.enabled == False` in both worktrees), so
  no ledger events are emitted by either runtime. Gate (b) passes
  trivially (empty == empty). The substantive verification of Plan
  Ledger's pre-B1 → post-D1 behavior — including D1 (work_observed
  filter) and D2 (ordinal collapse) — is captured under §4 by static
  code-path inspection at the named SHAs. This matches the B5
  baseline run's own ledger-stream emptiness; the audit pattern from
  B5 is preserved.
- **Gate (a) label-mapping unnecessary.** The charter at `§1.5`
  reserved a `T-NNNNNN:` prefix-strip for the label comparison. The
  pre-B1 reference at `d7ab3ae0` already had canonical T-NNNNNN ids
  (per B3 R2 landing earlier in the May work), so both sequences
  carry the same id-bearing labels and the prefix-strip was not
  exercised. Documented so future audits do not inherit the
  label-mapping assumption.

---

## 2. Item 2 — Kimi independent re-derivation

Out of scope for this stream. Covered by Kimi K2.6 under a separate
prompt. Convergence verified by Michael after both reports complete.

---

## 3. Item 3 — audit-pattern completeness gate

### 3.1 Greps

Run at `desplit-complete` against `mcloop/` and `tests/`. Saved raw
outputs to `bob-tools/.scratch/desplit-post-audit/item3-grep{1..3}-*.txt`.

| Grep | Pattern | Hit count |
|---|---|---|
| 1 | `\bchecklist\b` | 33 |
| 2 | `\bplan_split\b` | 0 |
| 3 | `\bCURRENT_PLAN\b` | 0 |
| 4 | `importlib\.import_module\(.{0,80}(checklist\|plan_split)` | 0 |
| 5 | `__import__\(['"](mcloop\.checklist\|mcloop\.plan_split)` | 0 |
| 6 | `from mcloop import (checklist\|plan_split)` | 0 |
| 7 | `getattr\(mcloop, ['"](checklist\|plan_split)` | 0 |

Greps 2–7 are clean. Grep 1 surfaces 33 hits; per-hit triage in §3.2.

### 3.2 Per-hit triage (grep 1)

Buckets per charter §3.2:
- **I** — live code reference to deleted module (FAIL)
- **II** — string literal in user-facing or prompt context (PASS, backlog)
- **III** — variable / parameter / docstring / comment (PASS)

**Bucket I (live code refs to `mcloop.checklist`): 0**.

**Bucket II (string literals in user-facing or prompt contexts): 2**.

| Location | Content | Type |
|---|---|---|
| `mcloop/prompts.py:381` | `" suitable as a task in a checklist. Example:\n"` | prompt template fragment |
| `mcloop/main.py:2325` | `argparse.ArgumentParser(description="Loop: grind through a markdown checklist")` | CLI `--help` description |

**Bucket III (variable / parameter / docstring / comment): 31**.

Production source (docstrings, comments, parameter/variable names):

| Location | Type |
|---|---|
| `mcloop/_planfile_compat.py:4,5,48,116,119,131,262,295,299,314,324,347,457` | module docstring + function docstrings + comments referencing historical `mcloop.checklist` semantics |
| `mcloop/_planfile_precondition.py:11,53` | docstring + comment |
| `mcloop/main.py:441` | comment ("need a checklist file because it detects the language from file") |
| `mcloop/maintain.py:75` | docstring |
| `mcloop/investigator.py:137` | docstring ("Append checklist steps") |

Tests (docstrings, comments, local variable names, assertion messages):

| Location | Type |
|---|---|
| `tests/plan_fixtures.py:29` | docstring |
| `tests/test_planfile_compat.py:4,5` | module docstring (this report's stream) |
| `tests/test_integration.py:14,804` | docstring + comment |
| `tests/test_runner.py:187` | comment |
| `tests/test_task_unification.py:1` | module docstring |
| `tests/test_args.py:4647` | comment |
| `tests/test_args.py:6595,6628,6636` | local variable `checklist = tmp_path / "PLAN.md"` and `dict["checklist_path"]` kwarg key (parameter name in `main.py`'s function signatures, not an import target) |
| `tests/test_investigator.py:91,95` | docstring + assertion error message |

### 3.3 Verdict

**PASS** — zero Bucket I hits across all seven greps. Two Bucket II
hits filed to the optional string-literal hygiene-pass backlog (§9 of
the charter excludes these from this audit's scope; they are
recorded for completeness, not as defects).

---

## 4. Item 4 — decision-register reconciliation

All four active decisions (D1, D2, D3, D5) verified against current
source at `desplit-complete`. D4 is historical (B1+B3 cutover diff
was routed to Michael for review before `eb80d13` landed); no active
check.

### 4.1 D1 — AUTO/USER `work_observed` not emitted

| Check | Verdict | Evidence |
|---|---|---|
| `_planfile_compat.check_off` discards the Settlement tuple returned by `complete_task`, so `Settlement` objects (including any `kind=="work_observed"`) never reach mcloop's emission path | PASS | `mcloop/_planfile_compat.py:446-449` (`_update_with_retry(p, lambda plan: complete_task(plan, task_id)[0])` — index `[0]` drops the settlements tuple) |
| `main._ledger_settle` constructs emission from a local `TaskOutcome` (success/abandoned/summary/changed_files), not from planfile Settlements | PASS | `mcloop/main.py:1039-1070` |
| `ledger_emit.emit_task_lifecycle_events` maps `TaskOutcome` to `commit_landed` / `test_failed` / `finding_observed` only — it does not have a `work_observed` branch | PASS | `mcloop/ledger_emit.py:384-422` (docstring enumerates the only emission kinds; no `work_observed` token in the module) |

**Filter is structural rather than syntactic**: `work_observed`
Settlements are dropped at the shim boundary, not filtered by a
`kind` check. The behavioral outcome is the same — no `work_observed`
event ever lands. PASS.

### 4.2 D2 — ordinal phase-id attribution collapsed to (`"none"`, `None`)

| Check | Verdict | Evidence |
|---|---|---|
| `main._ledger_settle` calls `resolve_phase_id(plan_path=..., task_label=...)` without `ordinal_index` | PASS | `mcloop/main.py:1052-1055` |
| `resolve_phase_id` returns `source="ordinal"` only when `ordinal_index is not None` — both ordinal branches gate on it explicitly | PASS | `mcloop/ledger_emit.py:152-168` (the `ctx.phase_id_source == "ordinal" and ordinal_index is not None` branch and the `ctx.phase_id_source == "none" and ordinal_index is not None` branch) |
| The omit-`ordinal_index` call therefore falls through to the final `return PhaseIdResolution(phase_id=None, source="none", plan_phase_count=...)` | PASS | `mcloop/ledger_emit.py:171` |
| `record_phase_id_fallback` no-ops on `resolution.source != "ordinal"`, so the `source == "ordinal"` branch in `_ledger_settle` is unreachable | PASS | `mcloop/ledger_emit.py:188-191` and dead branch at `mcloop/main.py:1056-1062` |

PASS.

### 4.3 D3 — `--retry` routes `clear_failed` across both PLAN.md and BUGS.md

| Check | Verdict | Evidence |
|---|---|---|
| `--retry` calls `clear_failed_markers(plan_path)` and `clear_failed_markers(bugs_path)` | PASS | `mcloop/main.py:853-855` |
| `clear_failed_markers` routes through `bob_tools.planfile.clear_failed` via `_update_with_retry` | PASS | `mcloop/_planfile_compat.py:463-471` |

PASS.

### 4.4 D4 — B1 canonicalization diff was routed to Michael

Historical. The B1+B3 cutover commit `eb80d13` landed after Michael
reviewed the diff. No active code check. **PASS by construction.**

### 4.5 D5 — purge re-home preserves DELETE semantics (not RETAIN)

| Check | Verdict | Evidence |
|---|---|---|
| `bob_tools.planfile.purge_done_bug_tasks` filters DONE tasks out of `plan.bugs.tasks` and returns a new `Plan` via `dataclasses.replace` | PASS | `bob_tools/bob_tools/planfile/operations.py:2087-2100` |
| It does **not** flip task status, does **not** move tasks to a different section, does **not** write to a different file (the function is pure-functional on `Plan`) | PASS | `operations.py:2087-2100` (entire function body is `dataclasses.replace` on `bugs.tasks`; no `TaskStatus` writes; no path arguments) |
| `_planfile_compat.purge_completed_bugs` calls `update(path, purge_done_bug_tasks, validation="unchecked")` with no post-processing | PASS | `mcloop/_planfile_compat.py:475-483` |
| Module docstring at `purge_done_bug_tasks` explicitly states: "Mirrors mcloop's legacy `purge_completed_bugs` delete behavior for BUGS.md: checked bug entries are removed, phase tasks are untouched, and no ledger Settlement is produced." | PASS | `operations.py:2089-2092` |

PASS.

### 4.6 Verdict

All decisions PASS. No FAIL under §4.6 (4.4 is historical and cannot
fail).

---

## 5. Item 5 — deletion-surface completeness

### 5.1 Production source

| Module | Expected | Verify | Verdict |
|---|---|---|---|
| `mcloop/plan_split.py` | DELETED IN FULL | `ls mcloop/plan_split.py` → no such file | PASS |
| `mcloop/checklist.py` | DELETED IN FULL (D1) | `ls mcloop/checklist.py` → no such file | PASS |
| `mcloop/main.py` | Re-pointed | `rg -c "from mcloop\.checklist\|from mcloop\.plan_split\|import mcloop\.checklist\|import mcloop\.plan_split" mcloop/main.py` → 0; `\bchecklist\b` hits are Bucket II/III only (§3.2) | PASS |
| `mcloop/lifecycle.py` | Re-pointed (B4) | imports=0; `active_paths: list[Path]` defaults BUGS.md, PLAN.md per `mcloop/lifecycle.py:157,161-162` | PASS |
| `mcloop/output.py` | Re-pointed (D1a-A) | imports=0; `_get_stages` / `_current_stage` inlined per `mcloop/output.py:18-40` | PASS |
| `mcloop/investigate_cmd.py` | Re-pointed (D1a-B); generated plans emit Stage-pattern headings | imports=0; `mcloop/investigator.py:129-131` (`## Stage 1: Steps`); `mcloop/investigate_cmd.py:286-290` (`## Stage {round+1}: Verification fix (round N)`); pin test `tests/test_investigator.py::test_generated_plan_parses_through_planfile_compat_shim` | PASS |
| `mcloop/maintain.py` | `CHECKBOX_RE` local (D1a-E) | imports=0; local regex at `mcloop/maintain.py:31` (`CHECKBOX_RE = re.compile(r"^(\s*)- \[([ xX!])\] (.+)$")`) | PASS |
| `mcloop/ledger_emit.py` | `resolve_phase_id` shim (B2); no-ordinal path collapses to (`"none"`, `None`) | `resolve_phase_id` at `mcloop/ledger_emit.py:122`; no-ordinal collapse at `:171` (per §4.2 above) | PASS |
| `mcloop/_planfile_compat.py` | Pure shim, no `checklist` import; D1a-D inlined `Task` dataclass | imports=0; `Task` dataclass inlined at `mcloop/_planfile_compat.py:31-46`; `PlanCorruptionError = PlanSyntaxError` alias at `:51` | PASS |
| `mcloop/{run_summary,checks,runner}.py` | No change | imports=0 for each | PASS |
| `mcloop/{errors,sync_cmd,audit,claude_md_sync,dep_validator,investigator,review_integration,code_edit}.py` | String literals only | imports=0 for each | PASS |

### 5.2 Test surface

| File | Expected | Verify | Verdict |
|---|---|---|---|
| `tests/test_plan_split.py` | DELETED (B5) | absent | PASS |
| `tests/test_checklist.py` | DELETED (D2) | absent | PASS |
| `tests/test_planfile_compat.py` | 4 shim-only tests retained (D1 Option 2) | 4 test functions; 0 `from mcloop import checklist` or `from mcloop\.checklist` hits | PASS |
| `tests/test_args.py` | Imports retargeted (D2 + D2-completion `1ea7a3e`) | imports=0 across all forms (literal + dynamic) | PASS |
| `tests/test_lifecycle.py` | Imports retargeted | imports=0 | PASS |
| `tests/test_output.py` | Imports retargeted | imports=0 | PASS |
| `tests/test_r4_task_id_surfacing.py` | Imports retargeted (D2) | imports=0 | PASS |
| `tests/test_task_unification.py` | Imports retargeted (D2) | imports=0 | PASS |
| `tests/integration/test_planfile_scheduler_integration.py` | Renamed from `test_checklist_integration.py` (D2) | new name present; old name absent | PASS |
| `tests/integration/test_subtask_ordering.py` | Imports retargeted (D2) | imports=0 | PASS |

### 5.3 Full gate confirmation

At `desplit-complete`, under `MCLOOP_INTEGRATION=1`: **1774 passed,
5 skipped** in 17.92s. The 5 skipped are the real-CLI tests gated on
the live LLM environment. No regressions.

### 5.4 Verdict

**PASS** — every row in §5.1 and §5.2 verified.

---

## 6. Closure summary

| Item | Total checks | PASS | FAIL |
|---|---|---|---|
| 1 (end-to-end equivalence) | 25 gate × mode cells | 25 | 0 (corrected gate (e)) |
| 2 (Kimi independent re-derivation) | — | out of scope | — |
| 3 (audit-pattern completeness) | 7 greps × triage | PASS | 0 Bucket I |
| 4 (decision register) | 5 decisions | 5 | 0 (D4 historical PASS-by-construction) |
| 5 (deletion-surface) | 22 rows (11 prod + 11 test) | 22 | 0 |
| **Overall** | — | **PASS** | 0 |

### Deviations from charter

#### Item 1 §1.1(e) — gate (e) wording (charter correction)

**Original charter wording (§1.1(e))**: *"BUGS.md final byte contents
— byte-identical. BUGS.md is not subject to canonical-id validation
(per D3) and round-trips cleanly through `parse_plan` / `render_plan`
on the pre-B1 file as well, so byte equivalence is the right gate
here."*

**Observed evidence**: in `bug_only` mode, the post-D1 runtime
delegates BUGS.md rendering to `bob_tools.planfile.render_plan`,
which canonicalizes the trailing blank line of an empty `## Bugs`
section (`## Bugs\n\n` → `## Bugs\n`). Direct probe
(`bob-tools/.scratch/desplit-post-audit/_probe_roundtrip.py`)
confirms this normalization is a structural property of the planfile
renderer, not a behavioral mcloop change. Pre-B1 BUGS.md
round-tripped through `parse_plan` / `render_plan` produces
`'## Bugs\n'` — matching the post-D1 output exactly.

**Corrected wording (authored 2026-05-22 by Michael in response to
this stream's surface)**: *"Gate (e): BUGS.md final byte contents
are equivalent modulo planfile renderer normalization. Operationally,
the gate is satisfied iff `render_plan(parse_plan(pre_b1_bugs_md))
== post_d1_bugs_md`. This correction is necessary because the
planfile renderer canonicalizes empty-section trailing whitespace on
render, the same class of behavior that §1.1 already excluded from
PLAN.md byte equivalence; the charter omitted this from the BUGS.md
case in error."*

**Basis for correction**: the same renderer-normalization class the
charter already excluded from PLAN.md byte equivalence (§1.1 leads
with *"PLAN.md byte contents are not a gate. The B1 canonicalization
deliberately changed PLAN.md's bytes (3-space → 2-space indent,
T-NNNNNN IDs added, phase_id comments added, blank-line
normalization)."*). The BUGS.md trailing-blank-line normalization is
the same renderer-canonicalization family; its exclusion from the
charter was an oversight.

**Implementation in the diff script**: two-stage compare —
byte-identical first (covers the case where BUGS.md was never
written and both runtimes leave the original on disk); fall back to
`render_plan(parse_plan(pre)) == post` (covers the rendered-output
case). Under the corrected gate, all 25/25 PASS.

#### Item 1 §1.5 — label-mapping bridge not exercised

**Original charter wording (§1.5)**: *"Gate (a) equivalence requires
a label-mapping bridge: pre-B1 identifies tasks by label string
("Implement feature X"); post-D1 identifies them by `T-NNNNNN` ID
followed by the same label. The equivalence check strips the
`T-NNNNNN:` prefix from post-D1 labels before comparison. Document
this mapping explicitly in the report."*

**Observed evidence**: the pre-B1 reference at `d7ab3ae0` already
had canonical `T-NNNNNN` ids per B3 R2 landing earlier in the May
work (B3 R2 added `T-NNNNNN` id enforcement to
`_planfile_precondition.enforce_canonical`, which exists at
`d7ab3ae0:mcloop/_planfile_precondition.py:60`). Both pre-B1 and
post-D1 captures therefore produce identical id-bearing labels; the
prefix-strip was not needed and was not exercised.

**Documentation note**: future audits should not carry the
label-mapping assumption forward. The B1+B3 cutover at `eb80d13`
moved the parser backend (checklist → planfile-backed shim) but not
the canonical-id requirement, which had already been in place. The
charter's label-mapping concern was structurally unnecessary for
this audit window.

### Deferred items

- The two Bucket II hits surfaced under §3.2 (`mcloop/prompts.py:381`
  and `mcloop/main.py:2325`) are filed to the optional
  string-literal hygiene-pass backlog. They are non-defects under
  this audit's scope (§9 of the charter explicitly excludes hygiene
  cleanup from the audit's scope).
- The `checklist_path` parameter / local-variable name (Bucket III)
  is a naming hangover from the pre-de-split era when PLAN.md was
  called "the checklist". Same backlog status — not a defect.

### Overall

**The audit converges clean.** Under the corrected gate (e), every
charter item PASSES with zero defects. The audit's two deviations
from the original charter wording are both charter-side oversights,
not code-side defects: the gate (e) renderer-normalization edge case
was missed in the original charter, and the §1.5 label-mapping
bridge was structurally unnecessary at the chosen pre-B1 reference.

Conditional on the Kimi K2.6 independent re-derivation stream
(item 2) reaching the same verdict, this audit is the gating
artifact between the de-split and the subsequent repository
consolidation.
