# Kimi K2.6 independent re-derivation — mcloop-desplit post-completion

Companion charter to
`bob/design/desplit-post-completion-audit.md`. That charter specified
items 1, 3, 4, 5 (executed by Claude Code, report at
`bob/design/desplit-post-completion-audit-claudecode.md`, committed at
`a96e626` on `bob/main`, all PASS). This charter specifies item 2 —
the parallel-lineage parity re-derivation, executed by Kimi K2.6,
modeled on the May 17 precedent at
`bob/design/desplit-independent-validation-kimi.md`.

**Dispatch:** Claude Code dispatches Kimi via the `kimi` shell
function in this session. The Claude Code stream is complete; the
two reports' convergence is the closure criterion.

Audience: CS PhD. Every claim re-derived from current source; no
trust in upstream summaries. Citations are `file:line` style. Format
matches the May 17 Kimi output verbatim (PASS/FAIL table, then
consequential findings, then any design-level decisions).

---

## 0. Frozen state

Tags are pushed and stable:

- `mcloop` `desplit-complete` →
  `a0c6acc7c8e3b55ad666753c35e5f61c2e214ad3` (post-D1 HEAD).
- `bob-tools` `desplit-complete` →
  `3cc32e11125491f51281cd2eb5ad6b9c1115134b` (post-D3 HEAD).

All claims re-derived against these tags. Resolve before reading
anything.

Authoritative reference documents:

- `bob/design/mcloop-desplit-integration-plan.md` — the integration
  plan as frozen.
- `bob/design/desplit-independent-validation.md` and
  `bob/design/desplit-independent-validation-kimi.md` — the May 17
  parallel validation outputs; precedent for format and rigor.
- `bob/design/planfile.md` — the canonical architecture reference;
  §8 Phase B contract is the behavioral target.
- `bob/design/desplit-post-completion-audit-claudecode.md` — Claude
  Code's stream A report. Read but do not trust; re-derive every
  shared claim independently.

Scratch path: `/Users/mhcoen/proj/bob-tools/.scratch/desplit-post-audit-kimi/`.
`/tmp` is forbidden.

Output report:
`/Users/mhcoen/proj/bob/design/desplit-post-completion-audit-kimi.md`.

---

## 1. Scope: §2 parity re-derivation against current source

The May 17 Kimi output covered the integration plan's §2 parity
audit, plus the §0 state items, the §3 stage-precondition logic, and
the §4 decision register. The same scope applies here, **re-derived
against the post-D1 / post-D3 state**, not the May 17 state.

The structural change since May 17: `mcloop/checklist.py` is now
deleted, so every §2 parity claim that previously read
`checklist.py:XXX` against `operations.py:XXX` must be re-derived
against `_planfile_compat.py:XXX` (the post-D1a shim) against
`operations.py:XXX` (current bob-tools). The parity is no longer
mcloop-checklist vs bob-tools-planfile; it is mcloop-shim
vs bob-tools-planfile, with the shim's correctness now load-bearing
because checklist is gone.

For each §2 item (2(a) through 2(g) and §0/§3/§4 items), the
verdicts and evidence to produce:

### 1.1 §2(a) DFS/scope/leaf-before-parent equivalence

Re-derive `_planfile_compat.py`'s `find_next` (or whichever symbol
post-D1a uses for next-task selection) against
`bob_tools/planfile/operations.py`'s `_walk_actionable`. Verify
control flow is still isomorphic; verify leaf-before-parent ordering
and `is_subtask` asymmetry.

### 1.2 §2(a) `@deps` vacuous-match

`rg -n "@deps" /Users/mhcoen/proj/mcloop/PLAN.md` → expected empty.
Verify `bob_tools/planfile/operations.py`'s `_deps_satisfied` still
returns `True` on empty deps.

### 1.3 §2(a) subsection-order divergence

Original divergence (May 17): `checklist.parse` flattened linearly;
`next_tasks` walked `phase.tasks` before `sub.tasks`. Divergence
neutralized by B1 canonicalization. Re-verify under current source
that the canonicalization holds — the post-B1 PLAN.md is the only
authoritative ordering source.

### 1.4 §2(b) failed-sibling root-skip / subtask-block asymmetry

Re-derive against `_planfile_compat.py` and `operations.py:622-626,
657-665` (re-derive line numbers; the bob-tools file has moved since
May 17). Verify root FAILED → `continue`, subtask FAILED → `return`.

### 1.5 §2(c) batch child selection and BATCH return-shape

Re-derive against `_planfile_compat`'s batch handling and
`operations.py`'s `_get_batch_children`. Verify the shim normalizes
the return shape (leaf vs surfaced BATCH parent) per the original
B3 design.

### 1.6 §2(d) tag detection (USER / AUTO / BATCH)

Original divergence (May 17): `checklist.is_user_task` was
leading-anchored, `checklist.is_auto_task` was substring-anywhere
(`re.search`), `checklist.is_batch_task` was substring-anywhere
(`"[BATCH]" in task.text`). Planfile is leading-only for all three.
The divergence was neutralized by the freeze invariant (no
prose-mention tags on incomplete tasks).

Re-derive against `_planfile_compat.py`. The shim's `is_user_task`,
`is_auto_task`, `is_batch_task` (if they exist as named symbols)
must match planfile's leading-only semantics. The freeze invariant
must still hold on current PLAN.md:

```
rg -n "^[[:space:]]*- \[[ !]\].*\[(BATCH|AUTO[^\]]*|USER)\]" /Users/mhcoen/proj/mcloop/PLAN.md
```

Expected: empty.

### 1.7 §2(d) ID-prefix strip

After `migrate()` prepends `T-NNNNNN:`, `task.text` is e.g.
`T-000001: [USER] ...`. Planfile strips the ID prefix before tag
detection (`parser.py:668-671, 685-686`; re-derive). Verify the
shim's tag detection sees the ID-stripped text.

### 1.8 §2(e) derived parent completion, `commit_landed` gating,
       AUTO/USER `work_observed` drop, commit-failure non-routing

Four sub-items, all decision-register-adjacent:

- Derived parent completion: silently checks parents, no event.
  Verify `operations.py`'s parent-checking returns derived
  Settlements with `ledger_event_required=False`.
- `commit_landed` git-gated: `ledger_emit.py`'s
  `emit_task_lifecycle_events` still gates on `_git_head_sha`.
- AUTO/USER `work_observed` dropped: per Decision D1; verify the
  shim's settle path filters `kind == "work_observed"`.
- Commit-failure not routed through `fail_task`: verify
  `main.py`'s `commit_failed` handling does not flip task status
  to FAILED.

### 1.9 §2(e) mutation needs IDs

`complete_task` / `fail_task` / `reset_task` resolve by `task_id`;
missing ID raises `ValueError`. Re-derive line numbers and confirm.

### 1.10 §2(f) `mark_failed` semantics and `--retry` clear

`fail_task` flips to FAILED unconditionally with `cascade=False`.
`clear_failed` clears all FAILED markers across PLAN.md and BUGS.md.
Verify both. Cross-reference Decision D3 from the Claude Code report
(item 4.3).

### 1.11 §2(g) atomic locking and renderer normalization

`fileio.save` uses `fcntl.flock LOCK_EX` + atomic tempfile + fsync +
os.replace; `update()` byte-compares. Whole-file canonical rewrite
on every save, neutralized at B1.

Note for this re-derivation: the Claude Code stream surfaced a
charter-correction case for §1(e) (BUGS.md byte equivalence) where
the planfile renderer normalizes an empty `## Bugs` section's
trailing blank line. That is a renderer-normalization property at
the same class as the PLAN.md canonicalization — not a defect, but a
behavioral fact that should appear in §2(g) of this re-derivation.
Verify it independently:

```
python3 -c "
from bob_tools.planfile import parse_plan, render_plan
src = '## Bugs\n\n'
print(repr(render_plan(parse_plan(src))))
"
```

Expected output: `'## Bugs\n'` (the trailing blank line dropped).
Record this as a normalization property, not a defect.

### 1.12 §0 state items

- `clear_failed` exported from `bob_tools.planfile`.
- `validate_plan` exported from `bob_tools.planfile`.
- `_planfile_compat` is the runtime path; `checklist.py` is absent.
  Verify via `ls /Users/mhcoen/proj/mcloop/mcloop/checklist.py` →
  expected: no such file.
- `purge_done_bug_tasks` exported from `bob_tools.planfile` (new
  since May 17, added at D3).

### 1.13 §3 stage preconditions / ordering

The May 17 Kimi report verified the staged ordering (B0.1 before
B0.2 before B2 before B3 before B5 before B6). The de-split has
since landed B1+B3, B4, B5, and Phase D (D3 first, then D2, then
D1a, then D1). Verify the actual landed order against the
integration plan §5 forced ordering and against current source:

- B0 → B1+B3 → B2 → B4 → B5 → D3 → D2 → D1a → D1.

Each transition's precondition is testable in source.

### 1.14 §4 decision register

D1, D2, D3, D5 verified by the Claude Code stream (item 4). Re-derive
independently here. D4 is historical (B1 diff review).

For each, cite the live code path in current source and verify the
decision still holds:

- D1: `_ledger_settle` drops `kind == "work_observed"`.
- D2: `resolve_phase_id` called without `ordinal_index` →
  collapses to `("none", None)`.
- D3: `--retry` routes through `planfile.clear_failed` on both
  PLAN.md and BUGS.md.
- D5: `purge_done_bug_tasks` filters DONE bug tasks, no semantic
  change beyond filtering.

---

## 2. Format requirements

Mirror `bob/design/desplit-independent-validation-kimi.md`:

1. **Section (A) — PASS/FAIL table.** One line per item, file:line
   evidence, three-column structure (Item / Verdict / Evidence).
   Use the verdict vocabulary from the May 17 report:
   `PASS`, `PASS (mcloop-adapt)`, `PASS (accepted-doc)`,
   `PASS (hard constraint)`, `PASS (resolved)`, `FAIL`. The
   parenthesized qualifiers are diagnostic — a PASS that depends on
   a downstream adaptation, an accepted documentation divergence, a
   structurally enforced constraint, or a previously-FAIL item now
   resolved.

2. **Section (B) — Consequential findings.** Itemized; each finding
   has a one-paragraph statement of what was observed against
   current source, and a one-paragraph correction or
   acknowledgment. If nothing consequential: "None found." (The
   May 17 report had one consequential finding — the
   `tests/test_args.py` deletion-surface omission. That was fixed
   during D1a. If anything analogous turns up here, surface it.)

3. **Section (C) — Design-level decisions for the human.** Only if
   the re-derivation surfaces a design-level question that requires
   Michael's judgment. The May 17 report used this for the
   conservative defaults on D1 and D2. If nothing requires a
   design-level decision: omit section (C) entirely.

4. **Closing — convergence note.** A one-paragraph note: "This
   stream's findings should be compared against Claude Code's
   stream A report at
   `bob/design/desplit-post-completion-audit-claudecode.md`.
   Convergence is the closure criterion."

---

## 3. Re-derivation discipline (the central rule)

The Claude Code report (stream A) is in your context. **Do not
trust it.** Every shared claim is re-derived from source
independently. The point of parallel validation is to catch defects
where both streams must independently arrive at the same evidence;
if Kimi reads Claude Code's evidence column and parrots it, the
parallel-validation pattern collapses.

Operational rule: for each item, open the cited file at the cited
line range and read the code yourself. Reproduce the citation in
your own evidence column. If your re-derived line number differs
from Claude Code's, use yours and flag it. If your verdict differs
from Claude Code's, that is a finding for section (B), not a thing
to reconcile silently.

The expected outcome (May 17 precedent): both streams independently
arrive at the same verdict on every item, with at most mechanical
citation-line drift between them. Anything else is a finding worth
surfacing.

---

## 4. Output, commit, push

1. Write the report to
   `/Users/mhcoen/proj/bob/design/desplit-post-completion-audit-kimi.md`.
2. Read both reports (this one and the stream A report) and
   produce a convergence note. The convergence note is a separate
   short document at
   `/Users/mhcoen/proj/bob/design/desplit-post-completion-audit-convergence.md`:
   one paragraph summarizing whether the two streams converge per
   the May 17 criterion (both independently report zero behavioral
   defects, with at most mechanical doc corrections), and listing
   any divergence by item.
3. Commit both new files (the Kimi report and the convergence
   note) in a single scoped commit on `bob/main`. Push to
   origin/main immediately.
4. Commit message neutral language per the commit-hook rule.

---

## 5. Surface to Michael

Only the convergence verdict and any divergence by item. The
reports live at their committed paths; Michael reads them at the
filesystem. Do not paste the table contents into chat. Do not
narrate the dispatch mechanics or the re-derivation procedure.

Specifically surface:

- Path to the Kimi report.
- Path to the convergence note.
- Convergence verdict: `CONVERGE CLEAN` (both streams zero
  behavioral defects), `CONVERGE WITH MECHANICAL CORRECTIONS` (both
  zero behavioral defects, one or both flagged citation-line drift
  or analogous doc corrections), or `DIVERGE` (one stream surfaces
  a finding the other did not, or the two streams disagree on a
  verdict).
- If `DIVERGE`: the specific items where divergence appears, with
  both streams' verdicts side by side.
- Commit SHA and push confirmation.

---

## 6. Stop conditions

- The `kimi` shell function is unavailable or fails to dispatch →
  stop, surface, do not fall back to running the re-derivation
  yourself (the parallel-lineage property requires Kimi
  specifically).
- Either tag (`mcloop:desplit-complete` or
  `bob-tools:desplit-complete`) does not resolve → stop, surface;
  consolidation cannot proceed on an unstable reference.
- The Kimi run produces a report that materially deviates from the
  May 17 format (no PASS/FAIL table, no evidence column, no
  consequential findings section) → re-run with an explicit pointer
  to the May 17 file as the format precedent; if the second run
  still deviates, surface and stop.
- Push blocked by classifier or hook → stop, surface; do not let
  the convergence-note commit accumulate locally.

---

## 7. Out of scope

- Any code change. This stream is read-only.
- The string-literal hygiene pass (Bucket II findings from stream A).
- Consolidation planning. The audit's outcome is the gate;
  consolidation starts only after convergence holds.
- Any re-validation of items 1, 3, 4, 5 from the Claude Code
  charter beyond what falls naturally within §2 / §0 / §3 / §4
  scope. Stream A covered those; this stream is the parity-and-
  decisions re-derivation.

---

## 8. Closure

After this charter executes and the convergence note lands, append
a one-paragraph closure entry to
`bob/design/mcloop-desplit-integration-plan.md` recording:

- The audit outcome (both streams' verdicts).
- The three artifact paths (stream A report, Kimi report,
  convergence note).
- The two `desplit-complete` tags and their SHAs.
- The date.

That closure entry is the artifact making "the de-split is done"
auditable. Consolidation planning starts from there.
