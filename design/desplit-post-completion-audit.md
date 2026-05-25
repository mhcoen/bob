# mcloop-desplit post-completion audit charter

Purpose: close the mcloop de-split with explicit verification that the
cumulative post-cutover state is equivalent to the pre-cutover behavioral
contract, with no drift accumulated across stage boundaries. This audit
runs after every stage gate has passed individually; the gates were
stage-local, this audit is end-to-end. It is the gating artifact between
the de-split and the subsequent repository consolidation.

This document is the executable specification for the Claude-Code
half of the audit (items 1, 3, 4, 5 below). An independent run by Kimi
K2.6 covers item 2 (parity re-derivation) under a separate prompt; the
two streams do not communicate during execution. Convergence between
the two reports is the closure criterion.

Authoritative references (re-derive every claim against these):

- `bob/design/mcloop-desplit-integration-plan.md` — the integration
  plan (§0 state, §1 governing risk, §2 parity audit, §3 staged plan,
  §4 decisions register, §5 forced ordering).
- `bob/design/desplit-independent-validation.md` and
  `bob/design/desplit-independent-validation-kimi.md` — the May 17
  parallel validation outputs; format precedent (PASS/FAIL verdict
  tables, evidence column with `file:line` citations).
- `bob/design/planfile.md` — the canonical architecture reference;
  §8 Phase B contract is the behavioral target.

---

## 0. Frozen state (preconditions)

The audit runs against named refs that do not drift if incidental
work lands during the audit window.

0.1 Create and push tags on both repos before any audit step begins:

```
cd /Users/mhcoen/proj/mcloop && git tag desplit-complete 901a0aff09329743c3f9a11c8667f59563cb8248 && git push origin desplit-complete
cd /Users/mhcoen/proj/bob-tools && git tag desplit-complete c30bdf71fe54404ad5fb7c2a8b8847b601c9adff && git push origin desplit-complete
```

Verify both tags resolve in their respective origins before proceeding.

0.2 Reference SHAs (pinned, do not re-derive):

- **Pre-B1 mcloop reference**: `d7ab3ae040a4a43633409d2f5f8271000878904a`
  (immediate parent of `eb80d13`, the B1+B3 cutover commit).
- **Post-D1 mcloop reference**: `901a0aff09329743c3f9a11c8667f59563cb8248`
  (tagged `desplit-complete`).
- **Post-D3 bob-tools reference**: `c30bdf71fe54404ad5fb7c2a8b8847b601c9adff`
  (tagged `desplit-complete`).

0.3 Scratch path for all artifacts (rehearsals, captures, intermediate
diffs): `/Users/mhcoen/proj/bob-tools/.scratch/desplit-post-audit/`.
`/tmp` is forbidden. Create the directory before step 1.

0.4 Output artifact: write the final report to
`/Users/mhcoen/proj/bob/design/desplit-post-completion-audit-claudecode.md`.
Format follows `desplit-independent-validation.md`: PASS/FAIL verdict
tables per item, evidence column with `file:line` citations, executable
commands in fenced blocks where they fit. Audience: CS PhD.

---

## 1. Item 1 — end-to-end behavioral equivalence (pre-B1 → post-D1)

The B5 baseline equivalence captured at and after B5 (and verified
through D3, D1a, and D1) covers only post-B5 → post-D1. It does not
cover the pre-B1 → post-D1 span — the most important behavioral
question, since B1+B3 was the irreversible cutover. Item 1 closes
that gap.

### 1.1 Methodology

PLAN.md byte contents are **not** a gate. The B1 canonicalization
deliberately changed PLAN.md's bytes (3-space → 2-space indent,
T-NNNNNN IDs added, phase_id comments added, blank-line
normalization). Demanding byte equivalence on PLAN.md across the
cutover would fail by design.

The gates are the *behavioral* outputs of a stub-backed `run_loop`
over a fixture pair (pre-B1 fixture in its original non-canonical
form; post-D1 fixture in its migrated canonical form, produced by
running `bob_tools.planfile.migrate` on the pre-B1 fixture exactly
once before the post-D1 capture).

The five gates for each capture mode:

(a) Ordered task execution sequence (task IDs or task labels in the
order `run_loop` selects them — under the canonical form these are
`T-NNNNNN`; under the pre-B1 form, the label string is the
canonical identifier since IDs did not exist).
(b) Ledger event stream — byte-identical across the pair. Per
Decision D1, AUTO/USER `work_observed` events are still not emitted;
per Decision D2, ordinal phase-id attribution is still collapsed to
`("none", None)`. Both decisions preserve pre-B1 emission, so the
ledger stream is the load-bearing equivalence signal.
(c) Exit code.
(d) `run-summary.json` fields: `terminal_status`, `stuck`,
`completed_stage`.
(e) BUGS.md final byte contents — byte-identical. BUGS.md is not
subject to canonical-id validation (per D3) and round-trips cleanly
through `parse_plan` / `render_plan` on the pre-B1 file as well, so
byte equivalence is the right gate here.

### 1.2 Fixture requirements

A single multi-stage PLAN.md fixture that exercises every behavior
covered by §2 of the integration plan:

- At least two stages with a phase transition.
- A `## Bugs` section with at least one open bug task and one DONE
  bug task (exercises purge re-home).
- At least one USER task.
- At least one AUTO task.
- One BATCH parent with at least two children.
- At least one `[RULEDOUT]` annotation on an existing task.
- At least one task that will fail under the stub backend (drives a
  `test_failed` event and the retry-exhaustion path).

The fixture pre-B1 form lives at
`.scratch/desplit-post-audit/fixture-pre-b1.md`; its post-D1
canonical form (produced by `bob_tools.planfile.migrate`) lives at
`.scratch/desplit-post-audit/fixture-post-d1.md`. Both checked into
the audit scratch tree as artifacts of the run.

### 1.3 Capture modes

The same five modes used in the B5 baseline equivalence:
`full_failure`, `retry`, `stop_after_one`, `stop_after_stage`,
`bug_only`. The pre-B1 / post-D1 capture pair runs all five.

### 1.4 Procedure

1. Set up two git worktrees of mcloop:
   - `.scratch/desplit-post-audit/worktree-pre-b1/` checked out at
     `d7ab3ae0`.
   - `.scratch/desplit-post-audit/worktree-post-d1/` checked out at
     `desplit-complete`.
2. Install each worktree's dependencies into its own `.venv`. The
   pre-B1 worktree depends on `bob_tools.planfile` only as a not-yet-
   adopted library; the post-D1 worktree depends on
   `bob_tools.planfile` as a runtime dependency. Confirm both `.venv`s
   resolve cleanly. The bob-tools editable install path for both
   worktrees points at the `desplit-complete` tag of bob-tools.
3. Drive `run_loop` against the pre-B1 fixture under the pre-B1
   worktree's `.venv` for each of the five capture modes. Capture
   gate (a)–(e) outputs to `.scratch/desplit-post-audit/capture-pre-b1/`.
4. Drive `run_loop` against the post-D1 fixture under the post-D1
   worktree's `.venv` for each of the five capture modes. Capture to
   `.scratch/desplit-post-audit/capture-post-d1/`.
5. Diff the captures gate-by-gate. The output report records, per
   mode, per gate, a verdict and the diff content (empty if PASS).

### 1.5 Pass / fail criteria

Per (mode, gate), PASS if the diff is empty under the equivalence
definitions in §1.1. FAIL if non-empty.

Gate (a) equivalence requires a label-mapping bridge: pre-B1
identifies tasks by label string ("Implement feature X"); post-D1
identifies them by `T-NNNNNN` ID followed by the same label. The
equivalence check strips the `T-NNNNNN:` prefix from post-D1 labels
before comparison. Document this mapping explicitly in the report.

Gate (b) equivalence is byte-identical. The ledger event stream
format is stable across the cutover by construction.

### 1.6 Stop conditions

Any (mode, gate) FAIL halts item 1, files the partial report under
the output path, and surfaces to Michael with the failing diff. Do
**not** continue to items 3/4/5 if item 1 has any FAIL — they are
sweep-style checks whose value is contingent on item 1 having
established behavioral equivalence.

---

## 2. Item 2 — Kimi independent re-derivation (NOT IN THIS PROMPT)

Item 2 is the parallel-lineage validation pattern from May 17. It
runs independently via Kimi K2.6 under a separate prompt. Claude
Code does not execute item 2 and does not coordinate with the Kimi
stream during execution. Convergence between the two reports is
verified by Michael after both complete.

---

## 3. Item 3 — audit-pattern completeness gate

D2's `__import__("mcloop.checklist", …)` escape and D1's
`from mcloop import checklist` escape both bypassed narrower
import-form-specific greps. Item 3 runs the broader pattern and
triages every remaining hit.

### 3.1 Procedure

In `/Users/mhcoen/proj/mcloop/` at the `desplit-complete` tag:

```
rg -n '\bchecklist\b' mcloop/ tests/
rg -n '\bplan_split\b' mcloop/ tests/
rg -n '\bCURRENT_PLAN\b' mcloop/ tests/
rg -n 'importlib\.import_module\(.{0,80}(checklist|plan_split)' mcloop/ tests/
rg -n '__import__\([\x27"](mcloop\.checklist|mcloop\.plan_split)' mcloop/ tests/
rg -n 'from mcloop import (checklist|plan_split)' mcloop/ tests/
rg -n 'getattr\(mcloop, [\x27"](checklist|plan_split)' mcloop/ tests/
```

The last four catch the dynamic-import / package-attr classes that
narrower grep patterns missed historically.

### 3.2 Per-hit triage

For every hit from the seven greps above, classify into exactly one
of three buckets in the report:

- **Bucket I — live code reference.** Any executable code path that
  resolves the name `checklist` / `plan_split` / `CURRENT_PLAN` to
  the deleted module / file. This is a FAIL.
- **Bucket II — string literal in a user-facing or prompt context.**
  Error messages ("Checklist not found:"), prompt templates, log
  format strings. PASS but recorded for the optional hygiene-pass
  backlog.
- **Bucket III — variable / parameter / docstring / comment.**
  `checklist_path` as a Path identifier (naming hangover from the
  pre-de-split era); inline comments; docstrings referencing the
  historical module. PASS.

### 3.3 Pass / fail criteria

PASS iff zero Bucket I hits across all seven greps.

### 3.4 Stop condition

Any Bucket I hit halts item 3 and is reported. The hit is a defect
that must be fixed before consolidation; consolidation will rewrite
SHAs and any unresolved import to a deleted module will then be
harder to diagnose.

---

## 4. Item 4 — decision-register reconciliation

§4 of the integration plan defines five decisions (D1–D5). Item 4
verifies each against current source.

### 4.1 D1 — AUTO/USER `work_observed` emission preserved as DROPPED

Per the integration plan §4 D1, the settle hook drops Settlement
objects of `kind == "work_observed"` (AUTO/USER successful tasks).
Mcloop's pre-B1 behavior was no emission for these; post-D1 must
preserve that.

Procedure: read `mcloop/_planfile_compat.py` and `mcloop/main.py`'s
`_ledger_settle` path. Confirm that the path from
`bob_tools.planfile.complete_task`'s returned Settlement to
`ledger_emit.emit_task_lifecycle_events` filters out
`kind=="work_observed"`. Cite the exact file:line.

PASS iff the filter is present and correct. FAIL if a code path
exists that would emit a `work_observed` event from an AUTO or USER
task completion.

### 4.2 D2 — ordinal phase-id attribution collapsed to ("none", None)

Per §4 D2 and §2(e) Decision D2, `main._ledger_settle` calls
`ledger_emit.resolve_phase_id` **without** the optional
`ordinal_index` argument. Per the shim at commit `0c4d6b7`, this
makes ordinal-source resolution collapse to `("none", None)` and
suppresses `record_phase_id_fallback`.

Procedure: read `mcloop/main.py`'s `_ledger_settle` (re-derive the
line range; the §2(h) reference at `main.py:903-906` may have drifted
across B3 / B4 / B5 / D1a). Verify the call site does not pass
`ordinal_index`. Read `mcloop/ledger_emit.py`'s `resolve_phase_id`
and confirm the no-ordinal path collapses to `("none", None)`.

PASS iff both checks hold. FAIL if `ordinal_index` is now passed
implicitly or if `resolve_phase_id` returns a non-`None` phase_id
without an explicit-source basis.

### 4.3 D3 — `--retry` routes through `planfile.clear_failed`

Per §4 D3 (the bob-tools commit `85b4524` addition) and §2(f),
`--retry` clears FAILED tasks across both PLAN.md and BUGS.md via
`bob_tools.planfile.clear_failed` (called through the
`_planfile_compat.clear_failed_markers` shim).

Procedure: trace the `--retry` argparse handler in `mcloop/main.py`
to the eventual `clear_failed` call. Confirm it operates on both
PLAN.md and BUGS.md (not just one). Cite both call sites.

PASS iff `--retry` calls `clear_failed` on both files. FAIL if only
one file is cleared, or if a checklist-era code path survives.

### 4.4 D4 — B1 canonicalization diff was routed to Michael

Historical / settled. The B1+B3 cutover commit `eb80d13` landed
after Michael reviewed the diff. No active code check; the report
records this as a one-line acknowledgment that the historical
decision-gate was honored.

PASS by construction (the commit exists). Recorded for completeness.

### 4.5 D5 — purge re-home preserves DELETE semantics (not RETAIN)

Per §4 D5 and the §2(h) D3 sub-stage description, the
`purge_done_bug_tasks` operation added to bob-tools at commit
`c30bdf7` filters DONE bug tasks out of `plan.bugs.tasks`. The
delete-vs-retain question (whether resolved bugs are erased,
checked-off-in-place, or moved to an append-only history) is
explicitly **out of scope**; the de-split's purge re-home preserves
the historical delete semantics.

Procedure: read
`bob-tools/bob_tools/planfile/operations.py`'s
`purge_done_bug_tasks` implementation. Confirm it filters DONE bug
tasks (`task.status == TaskStatus.DONE`) out of the returned
`Plan.bugs.tasks`. Confirm it does **not** change task status, does
not move tasks to a separate section, and does not write to a
different file. Read the mcloop shim
`mcloop/_planfile_compat.purge_completed_bugs` and confirm it calls
`fileio.update(BUGS.md, purge_done_bug_tasks, …)` without
post-processing that would change semantics.

PASS iff both checks hold. FAIL if semantics changed at the re-home.

### 4.6 Stop condition

Any FAIL in items 4.1, 4.2, 4.3, or 4.5 halts item 4 and is
reported. Item 4.4 is historical and cannot fail.

---

## 5. Item 5 — deletion-surface completeness

§2(h) of the integration plan lists every mcloop module's expected
disposition under the de-split. Item 5 walks the table and confirms
each disposition matches current source at `desplit-complete`.

### 5.1 Production source disposition

For each row, the verdict and the evidence (file existence,
`rg "from mcloop\.checklist|from mcloop\.plan_split"` per file):

| Module | Expected | Verify |
|---|---|---|
| `mcloop/plan_split.py` | DELETED IN FULL | `ls mcloop/plan_split.py` → no such file |
| `mcloop/checklist.py` | DELETED IN FULL (D1) | `ls mcloop/checklist.py` → no such file |
| `mcloop/main.py` | Re-pointed | zero `checklist` / `plan_split` imports; Bucket III-only hits on `\bchecklist\b` |
| `mcloop/lifecycle.py` | Re-pointed (B4) | zero `checklist` imports; `active_paths` is `[BUGS.md, PLAN.md]` |
| `mcloop/output.py` | Re-pointed (D1a-A) | zero `checklist` imports |
| `mcloop/investigate_cmd.py` | Re-pointed (D1a-B) | zero `checklist` imports; generated plans emit Stage-pattern headings |
| `mcloop/maintain.py` | `CHECKBOX_RE` local (D1a-E) | zero `checklist` imports; local `CHECKBOX_RE` constant present |
| `mcloop/ledger_emit.py` | `resolve_phase_id` shim (B2) | calls `planfile.resolve_task_context`; no-ordinal path collapses to `("none", None)` |
| `mcloop/_planfile_compat.py` | Pure shim, no `checklist` import | zero `checklist` imports (live or dynamic); D1a-D inlined `Task` dataclass |
| `mcloop/{run_summary,checks,runner}.py` | No change | no `checklist` / `plan_split` imports |
| `mcloop/{errors,sync_cmd,audit,claude_md_sync,dep_validator,investigator,review_integration,code_edit}.py` | String literals only | no `checklist` / `plan_split` imports |

### 5.2 Test surface disposition

| File | Expected | Verify |
|---|---|---|
| `tests/test_plan_split.py` | DELETED (B5) | `ls tests/test_plan_split.py` → no such file |
| `tests/test_checklist.py` | DELETED (D2) | `ls tests/test_checklist.py` → no such file |
| `tests/test_planfile_compat.py` | 4 shim-only tests retained (D1 Option 2) | 4 test functions present, zero `from mcloop import checklist`, zero `from mcloop.checklist` |
| `tests/test_args.py` | Imports retargeted to `_planfile_compat` (D2 + D2-completion `1ea7a3e`) | zero `checklist` imports including dynamic forms |
| `tests/test_lifecycle.py` | Imports retargeted | zero `checklist` imports |
| `tests/test_output.py` | Imports retargeted | zero `checklist` imports |
| `tests/test_r4_task_id_surfacing.py` | Imports retargeted (D2) | zero `checklist` imports |
| `tests/test_task_unification.py` | Imports retargeted (D2) | zero `checklist` imports |
| `tests/integration/test_planfile_scheduler_integration.py` | Renamed from `test_checklist_integration.py` (D2) | file present at the new name; `tests/integration/test_checklist_integration.py` absent |
| `tests/integration/test_subtask_ordering.py` | Imports retargeted (D2) | zero `checklist` imports |

### 5.3 Pass / fail criteria

PASS iff every row's "Verify" condition holds.

FAIL on any row mismatch. Surface the row, the expected condition,
and the observed state.

### 5.4 Stop condition

Any FAIL halts item 5. Most failures would indicate either an
undetected coupling that survived the staged audits, or a
disposition that drifted from the integration plan; either case is
a defect that must be fixed before consolidation.

---

## 6. Output report

### 6.1 Path

`/Users/mhcoen/proj/bob/design/desplit-post-completion-audit-claudecode.md`

### 6.2 Structure

Mirror `desplit-independent-validation.md`:

1. Header — what was audited, against what reference docs, by which
   reviewer (Claude Code), under which Anthropic-Claude model
   identifier and date.
2. Frozen state — the two `desplit-complete` tags, the pre-B1
   reference SHA, the bob-tools D3 SHA. Confirm each resolves.
3. Item-by-item PASS/FAIL verdict table, evidence column with
   `file:line` citations. One subsection per item (1, 3, 4, 5).
   Item 2 is recorded as "out of scope for this stream; covered by
   Kimi report under separate prompt."
4. Per-item findings (Bucket I/II/III for item 3; failing diffs for
   item 1; cited source verifications for items 4 and 5).
5. Closure summary: per-item PASS/FAIL totals, overall verdict, list
   of any deferred items (e.g., Bucket II hygiene-pass backlog).

### 6.3 Surface to Michael

In chat, surface only:

- Path to the written report.
- Per-item PASS/FAIL totals.
- Each FAIL with one-line evidence (full content in the report).
- The bucket counts from item 3 (with Bucket II hits listed as
  backlog candidates, not as defects).
- Any deviation from the charter that you encountered and how you
  resolved it.

Do **not** surface in chat: the table contents (they live in the
report), per-row verifications, normal procedural choices,
scratch-path mechanics, or operational details handled by the
charter.

---

## 7. Commit and push

A single scoped commit on `bob/main` containing:

- The output report at the path in §6.1.
- Any fixture artifacts under
  `bob-tools/.scratch/desplit-post-audit/` that should be durable
  (the pre-B1 / post-D1 fixture pair at minimum; capture diffs
  optionally).

If `bob` has a `.gitignore` excluding `.scratch/`, the fixtures live
in the bob-tools scratch tree, which is a separate repo — commit
those there if they belong, but the canonical report itself lives in
`bob/design/`.

Commit message neutral language (no `claude`, `anthropic`, `happy`,
`co-authored-by` anywhere in message or in chained `git add`
filenames; the commit hook will reject them otherwise). Push to
origin/main immediately after the commit.

---

## 8. Stop conditions (consolidated)

- Item 1 any FAIL → stop all items, file partial report, surface.
- Item 3 any Bucket I hit → stop, surface as a defect blocking
  consolidation.
- Item 4 any FAIL (excluding 4.4 which is historical) → stop,
  surface.
- Item 5 any row mismatch → stop, surface.
- Any push blocked by classifier or hook → stop, surface; do not
  continue audit work on top of an unpushed commit.

The audit either converges clean (every item PASS) or it surfaces a
defect. There is no acceptable intermediate state where the audit
runs to completion with unresolved findings.

---

## 9. Out of scope

These are explicitly **not** part of this audit:

- The string-literal hygiene pass (`checklist_path` → `plan_path`,
  "Checklist not found:" user message, prompt template references to
  the historical module name). These are Bucket II findings;
  recorded as backlog, not addressed here.
- Any change to runtime behavior. The audit is read-only; if a
  finding requires a code fix, the fix lives in a separate commit
  under a separate prompt.
- The bob-tools `purge_done_bug_tasks` op's design itself; only its
  semantic preservation (item 4.5).
- The lifecycle.py atexit-shutdown fix (commit `831bf7a`, B4); it
  landed and is covered by the item 5 row for lifecycle.py.
- Consolidation planning. The audit's outcome is the gate; planning
  starts only after both this charter's stream and the Kimi stream
  converge clean.

---

## 10. Convergence with the Kimi stream

After Claude Code completes this charter and Kimi completes the
item 2 prompt, Michael reads both reports side by side. The
convergence criterion (May 17 precedent): both lineages
independently report zero behavioral defects, with at most
mechanical doc corrections.

If convergence holds: a closure entry appended to
`bob/design/mcloop-desplit-integration-plan.md` records the audit
outcome, the two reports' paths, and the two `desplit-complete`
tags. That closure entry is the artifact making "the de-split is
done" auditable rather than oral. Consolidation planning then
starts from a verified base.

If convergence does not hold: triage the divergence in a follow-up
session — defect in code (fix and re-run the affected item) versus
defect in the integration plan or charter (correct the document,
re-run if the correction implies a code check). Iterate until
convergence. Consolidation does not start while either stream has
unresolved findings.
