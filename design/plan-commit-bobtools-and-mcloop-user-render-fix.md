> **STATUS: SUPERSEDED — historical record, do not execute.**
>
> This is the initial planning document from the 2026-05-16 session. Its §0 verification ledger (V1–V6) was sound and was independently confirmed by a later Codex audit. Its §1 procedure is **counterfactual**: it plans to commit a "pending green bob-tools bug-fix" fileset that did not exist — that work was already committed in bob-tools commit `06d819d`, and the working tree was clean. §2–§4 and Decisions A/B were overtaken by events. The session's actual path: the D1/D2 mcloop `[USER]` defects were filed into `mcloop/BUGS.md` (commit `5c7c714`), `BUGS.md` was made git-tracked in mcloop (`579f347`) and un-ignored in duplo/orchestra, the authoritative planfile design spec was committed (`bob/design/planfile.md`), and a deferred-design backlog was created (`bob/design/BACKLOG.md`). bob-tools Stages 3–8 were not resumed in this session.
>
> Retained as audit provenance and as a specimen for structural-lessons analysis (see `bob/design/BACKLOG.md`, entry 2): it documents the session's root-cause failure mode — an authoritative-looking plan whose central premise derived from an unverified handoff prompt rather than confirmed source.

---

# Plan: commit pending bob-tools work; diagnose + in-plan-fix mcloop `[USER]` render defect; resume bob-tools Stages 3–8

Status: draft for Codex review, then execution. Author: Claude (Opus 4.7), 2026-05-16.
Scope: ordered execution plan with a source-verification ledger and two unresolved decisions for the user.

All claims below were verified by reading source on the user filesystem on 2026-05-16. Items that could **not** be verified from this environment are marked `UNVERIFIED` with the reason. Nothing was inferred from patterns.

---

## 0. Verification ledger (read this before executing)

The prompt's framing diverges from source in six places. None is resolved here; each is surfaced so execution does not paper over it.

V1. **`journal.txt` absent.** `/Users/mhcoen/proj/bob-tools/journal.txt` does not exist. Not present in `proj/`, `bob-tools/`, `bob-tools/.mcloop/`, `proj/.mcloop/`, `~/.mcloop/`, or `mcloop/`. Recursive glob (`Filesystem:search_files`) timed out (local MCP server unresponsive, 4 min). Session history was therefore reconstructed from in-repo sources instead: `bob-tools/NOTES.md`, `bob-tools/CURRENT_PLAN.md`, `bob-tools/PLAN.md`, `bob-tools/BUGS.md`, and the `## Eliminated` commit digest in `NOTES.md`. The "most recent transcript it points to" was not read because the pointer file is missing.

V2. **bob-tools Stage 2 checkbox state is inconsistent across files.** `bob-tools/PLAN.md` (master): Stage 1 tasks `[x]`; **every Stage 2 task `[ ]`** including "Verify Stage 2 leaves the repo green". `bob-tools/CURRENT_PLAN.md` (mcloop split-plan active file): Stage 2 subtasks `[x]`, but its `[USER]` task and "Verify Stage 2 green" are `[ ]`. Master and active disagree on Stage 2. Prompt's "Stages 1–2 built and verified" matches CURRENT_PLAN.md, not PLAN.md.

V3. **`353 tests pass; ruff/format/mypy strict clean` is `UNVERIFIED`.** No test run and no git access from this environment (see V4). Corroborated only indirectly by `NOTES.md` `## Eliminated` commit digest, which is narrative, not a green-state proof.

V4. **Pending uncommitted bob-tools changes are `UNVERIFIED` from here.** `bash_tool` executes on the Claude container; `ls /Users/mhcoen/proj` returns `No such file or directory`. There is no git tool over the user filesystem. The specific pending fileset (`BUGS.md`, `NOTES.md`, `bob_tools/planfile/__init__.py`, `bob_tools/planfile/operations.py`, `bob_tools/planfile/tests/test_operations.py`, untracked `bob_tools/planfile/tests/manual/`) cannot be confirmed. `NOTES.md` entries dated 2026-05-16 corroborate the *content* of the work (RUF022 on `__init__.py` `__all__` re-sorted; dead `# noqa: BLE001` removed for RUF100; `bug_count(plan) -> int` added; manual helper `check_compat_read.py`; `TestBugCount` cases) but not working-tree state. `bob-tools/BUGS.md` currently contains only `## Bugs` (empty) — consistent with the task-2.8 BUGS.md noise having been cleared.

V5. **`[USER]` task count is four, not three.** `bob-tools/PLAN.md` contains `[USER]` tasks in Stage 2, **Stage 3**, Stage 7, Stage 8. All four are in the readable "What to do / What to expect / What to report back" form with commands on isolated lines — so the substantive claim (PLAN.md needs no edit) holds. The Stage 3 `[USER]` task is omitted from the prompt's enumeration. Separately: `CURRENT_PLAN.md`'s Stage-2 `[USER]` task is still the *old inline one-liner* form (stale snapshot); it is only relevant if CURRENT_PLAN.md ever becomes the operator-facing surface.

V6. **The defect is two distinct defects; "Phase B" does not exist.** Source trace of `mcloop/mcloop/checklist.py`:
  - `parse()` converts only `CHECKBOX_RE` lines to `Task`s. `text = m.group(3).strip()` is the **single checkbox line only**. Every non-checkbox body line hits `if not m: continue` and is **discarded at parse time**.
  - `user_task_instructions(task)` returns `task.text.replace("[USER]","").strip()` — only the first line survives.
  - `formatting.user_banner()` prints that one line. The multi-line "What to do/expect/report" body and isolated command lines **never reach the console**. This is **not** "rendering collapses the body into a blob" — the body is **absent before rendering**. Root cause is the parser, not the printer.
  - Distinct second defect: `mcloop/mcloop/main.py`, the `[USER]`-fail branch, `flat_obs = response.replace("\n"," | "); short_obs = flat_obs[:200]` flattens+truncates the **user's typed observation when filing it to BUGS.md**. This is the defect `bob-tools/NOTES.md` (2026-05-16) actually logged for task 2.8. `NOTES.md` cites `mcloop/main.py:1218-1226`; line numbers drift (per `bob-tools/PLAN.md` preamble policy), so re-confirm with `grep -n`.
  - `mcloop/BUGS.md` already holds one open `- [ ]` entry for this, described there as "dumps the entire task text as one prose blob" — also imprecise vs. source; its proposed fix ("render `[USER]` with structure") **cannot work without first changing the parser** to retain the body.
  - `mcloop/PLAN.md` uses `## Stage N`, has Stages 1–10, **all `[x]`**, and **no `Phase` sections and no "Phase B"**. The prompt's "Phase B" referent is unverified. The real in-repo bug-fix surfaces are enumerated under Decision B.

---

## 1. Commit the pending green bob-tools bug-fix work

Precondition: bob-tools working tree is exactly the expected pending fileset and is green. **This is V4-`UNVERIFIED`; step 1.0 verifies it. Do not commit blind.**

1.0 Verify state (no mutation):
```
cd /Users/mhcoen/proj/bob-tools && git status --porcelain
cd /Users/mhcoen/proj/bob-tools && git diff --stat
```
Expected modified: `BUGS.md`, `NOTES.md`, `bob_tools/planfile/__init__.py`, `bob_tools/planfile/operations.py`, `bob_tools/planfile/tests/test_operations.py`. Expected untracked: `bob_tools/planfile/tests/manual/`. If the set differs, **stop** and report; do not reconcile silently.

1.1 Confirm green before commit:
```
cd /Users/mhcoen/proj/bob-tools && ruff check . && ruff format --check . && pytest -q && mypy --strict bob_tools
```
All must pass. If any fails, **stop** — the work is not green; do not commit, do not revert (hard rule: never revert working code).

1.2 Commit (mechanism is **Decision A** — do not pick here). Message (factual, scoped to what NOTES.md records):
```
planfile: fix RUF022 (__all__ sort) + RUF100 (dead BLE001 noqa); add bug_count() disambiguation API and check_compat_read manual helper for task-2.8 bug
```
Stage exactly the verified set incl. the untracked `manual/` dir. No `[USER]`/PLAN.md edits in this commit.

1.3 Post-commit: re-run 1.1; confirm clean tree (`git status --porcelain` empty). Do **not** push (push is an explicit-permission action; not in scope unless the user authorizes).

---

## 2. Read-only diagnosis of the mcloop `[USER]`-task console-rendering defect

No edits. Output is a written diagnosis appended to the Codex review thread (not to any repo file out of band).

2.1 Confirm the parse-time loss (primary root cause):
```
grep -n "if not m" /Users/mhcoen/proj/mcloop/mcloop/checklist.py
grep -n "def user_task_instructions" /Users/mhcoen/proj/mcloop/mcloop/checklist.py
grep -n "def user_banner" /Users/mhcoen/proj/mcloop/mcloop/formatting.py
```
Assert: `CHECKBOX_RE` is the only task-producing path; `user_task_instructions` returns first-line-only; `user_banner` interpolates `instructions` as one run. Conclusion: body is dropped in `parse()`, before any display path.

2.2 Confirm the distinct bug-filing flatten/truncate:
```
grep -n "flat_obs" /Users/mhcoen/proj/mcloop/mcloop/main.py
```
Assert: `replace("\n"," | ")` + `[:200]` in the `[USER]`-fail branch only; this is the BUGS.md-filing path, not the body-display path. Record actual current line numbers (NOTES.md's `1218-1226` is a drift-prone reference).

2.3 Deliverable: a 2-defect diagnosis — (D1) parser discards `[USER]` body (`checklist.parse` + `user_task_instructions`); (D2) observation flatten/truncate when filing to BUGS.md (`main.py` flat_obs). State explicitly that the `mcloop/BUGS.md` entry and the prompt both under-describe D1 (it is loss, not flattening) and that any structured-render fix is contingent on the parser first retaining the body.

---

## 3. Write the fix as a task in mcloop's own repo (in-plan, not out-of-band)

The surface is **Decision B** — do not pick here. Whatever surface is chosen, the task text must encode the corrected D1/D2 understanding from §2.3, because the existing `mcloop/BUGS.md` wording would misdirect the fix. Task body (to be placed per Decision B), in mcloop's readable convention, commands on isolated lines:

- Retain `[USER]`-task body in `checklist.parse`: associate contiguous non-checkbox lines following a `[USER]` checkbox (until the next checkbox/heading) with that `Task` (new field, e.g. `body: str`); do not fold into `text`. Keep `CHECKBOX_RE` task identity unchanged.
- `user_task_instructions` returns the retained body verbatim (newlines preserved) when present, else current behavior.
- `formatting.user_banner` renders the body line-structured: command-looking lines isolated with blank lines around them; no reflow.
- D2: when filing a failed `[USER]` observation to BUGS.md, stop collapsing newlines to ` | ` and stop the 200-char hard cut; preserve the observation (wrap as a fenced block).
- Tests: a multi-line `[USER]` task round-trips body through parse → `user_task_instructions` → banner with newlines intact; a long multi-line observation files to BUGS.md without truncation.
- Leaves mcloop green: `ruff check`, `pytest`, `mypy` (mcloop's configured checks) all pass.

No edit to stopped mcloop source happens in this step; this only authors the task in-repo. (Tension is **Decision B**.)

---

## 4. Resume bob-tools Stages 3–8

Precondition: §1 committed and clean; Decision B path for §3 chosen and, if it routes through mcloop bug-only mode against `mcloop/BUGS.md`, that run completed and mcloop is green.

4.1 No PLAN.md edit (V5: PLAN.md `[USER]` tasks already in readable form — verified). If V2 indicates CURRENT_PLAN.md/PLAN.md Stage-2 divergence blocks resumption, surface it; do not hand-reconcile.

4.2 Resume:
```
cd /Users/mhcoen/proj/bob-tools && mcloop
```
4.3 Stages 3–8 contain `[USER]` checkpoints (3, 7, 8). Until §3's fix lands in mcloop, those checkpoints will display first-line-only (D1 active) — operator must read the body from `bob-tools/PLAN.md` directly. Note this expectation explicitly to the user before the run.

---

## Open decisions (do not resolve — user decides)

**Decision A — how to land the §1 commit.**
- A1 Direct `git commit` in bob-tools (steps as in §1). Fastest; bypasses mcloop entirely; no mcloop run touches the tree.
- A2 One-task mcloop run that commits. Keeps mcloop's commit path authoritative, but mcloop on startup runs checkpoint/`_stage_safe`, ensures CURRENT_PLAN.md/BUGS.md split files, and enters **bug-only mode if `bob-tools/BUGS.md` has any unchecked entry** (it is currently empty — verified — so bug-only would not trigger now). Risk: an mcloop run mutates more than the intended commit (checkpoint commits, split-plan files) and interacts with V2's Stage-2 divergence. Trade-off: tool-path purity (A2) vs. blast-radius minimization and the never-edit-around-a-run rule (A1).

**Decision B — §3 surface, and the edit-now vs. in-plan tension.**
- B1 In-plan via the **existing `mcloop/BUGS.md` entry**: this *is* mcloop's own bug-fix path. `cd /Users/mhcoen/proj/mcloop && mcloop` enters bug-only mode and fixes it. Fully in-repo, honors "let mcloop's bug-fix path own bug fixes," no out-of-band edit. Cost: the BUGS.md entry text is imprecise (V6) — must be corrected in-repo first (itself an in-plan edit to mcloop, made while mcloop is *not* running), and a full mcloop self-run is slower.
- B2 In-plan via a **new appended stage in `mcloop/PLAN.md`** (there is no "Phase B"; Stages 1–10 all `[x]` — V6). Explicit, reviewable, ordered; but adds a stage to a fully-complete plan and still requires an in-repo PLAN.md edit while mcloop is stopped.
- B3 Direct out-of-band edit of stopped mcloop source now (fastest path to a correct operator display for the §4 resume). **Violates the never-edit-out-of-band hard rule.** Listed only because the prompt names the tension explicitly; the rule disfavors it.
- The tension: B3 gives a correct `[USER]` display *before* the bob-tools Stages 3–8 resume (so §4.3's degraded-display caveat disappears); B1/B2 are rule-compliant but leave D1 active during the §4 resume. The user must weigh "correct checkpoints during Stages 3–8" against the never-edit-out-of-band rule. Not resolved here.

---

## Execution order (once A and B are chosen)
§1.0 → §1.1 → (Decision A) §1.2 → §1.3 → §2 (read-only) → §3 (Decision B, in-repo, mcloop stopped) → if B routes through mcloop, run it and confirm green → §4. Abort-and-report on any failed gate; never revert working code; never edit files out of band around an mcloop run.
