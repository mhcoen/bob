## Bugs

### Chain tier 2 hardcodes `gpt-5-codex`, which ChatGPT-account Codex rejects

**Symptom**: every mcloop run prints `Skipping chain tier 2
(codex/gpt-5-codex): preflight failed — Codex subscription preflight failed
before starting a task` and runs without the Codex tier.

**Root cause**: the chain-tier config names the model `gpt-5-codex`, which this
account's Codex CLI does not serve (it serves `gpt-5.5`; confirmed by running
`codex` from the shell). Same wrong constant as the orchestra `codex` identifier
bug — see `orchestra/BUGS.md`. This mcloop literal is independent of orchestra's
identifier table, so fixing orchestra alone does not clear this; the chain-tier
model string must be changed to `gpt-5.5` (or whatever the account serves) here
as well.

**Fix**: locate the chain-tier definition that specifies `codex/gpt-5-codex` and
change the model string to `gpt-5.5`. Cross-reference orchestra/BUGS.md so both
sites are fixed together.

### `mark_failed` crashes on id-less BUGS.md bug tasks, halting the bug-fix loop

**Symptom**: in bug-only mode, mcloop synthesizes fix tasks from
`.mcloop/errors.json` into BUGS.md, runs the first one, and when that task
"produced no changes" it calls `mark_failed`, which raises
`ValueError: mark_failed requires migrated PLAN.md task ids` and crashes the
whole loop (traceback through `main.py:run_loop` ->
`_planfile_compat.mark_failed` -> `_require_task_id`). No further bugs are
processed; the queued duplo/orchestra fixes never run.

**Root cause**: a self-inconsistency in `_planfile_compat.py`. BUGS.md bug
entries are id-less by design — `purge_completed_bugs` documents it: "Standalone
BUGS.md remains a loose bug queue, not a canonical PLAN.md, so id-less bug
entries must not be rejected by PLAN.md canonical validation." But `mark_failed`
-> `_require_task_id` raises whenever `task.task_id is None`, and the bug-fix
failure path routes an id-less BUGS.md task straight into that ID-targeted
mutation. read/select/classify tolerate id-less compat tasks; the mutation path
does not; the bug-fix flow puts an id-less task on the mutation path. So the
first bug task that fails to produce changes crashes the loop. This is NOT the
user's PLAN.md being un-migrated — it is BUGS.md tasks being id-less by design
and the fail path not handling that case.

**Compounding trigger**: the first synthesized fix task targeted
`/tmp/claude-501/exp_e2e.py` — a transient experiment file that is part of no
package and does not persist across runs. A fix against it can never produce
changes, which guaranteed the `mark_failed` path was exercised. The errors.json
-> fix-task scraper should not generate fix tasks from `/tmp`/ephemeral paths in
the first place (no durable source file to edit). Two defects intersect: (a) the
scraper sourcing bugs from `/tmp`, and (b) `mark_failed` crashing on the id-less
task that results.

**Fix** (design decision required, do not pick silently):
1. Make the bug-task failure path id-agnostic: mark a BUGS.md bug task failed by
   its source span / checkbox rewrite rather than via `fail_task(plan, task_id)`,
   since bug tasks legitimately have no id. The ID-targeted mutation is correct
   for canonical PLAN.md tasks but wrong for the loose BUGS.md queue.
2. AND filter the errors.json -> fix-task scraper so transient/ephemeral paths
   (`/tmp`, run dirs, anything outside a package source tree) never become fix
   tasks — there is no durable file to edit, so the task can only fail.

**Evidence**: run on `/Users/mhcoen/proj/bob/packages/duplo` in bug-only mode;
four bugs detected from `.mcloop/errors.json` (incl. the duplo `plan_author`
decision-consistency error already captured in duplo/BUGS.md); task 1
(`/tmp/claude-501/exp_e2e.py` TypeError) produced no changes; crash at
`_planfile_compat.py:_require_task_id` via `mark_failed`.
