# McLoop cruft hunt — findings

Working notes from a module-by-module audit of `/Users/mhcoen/proj/mcloop/mcloop/`.
Verdicts: **CRUFT** (delete), **STALE** (rewrite or remove), **SUSPECT**
(needs your call), **CLEAN** (verified non-cruft).

Method: read every module's docstring and import block. Trace inbound edges by
inspecting imports of all sibling modules. Flag dead modules (no inbound edges
from the main run path), stale comments referring to past migration phases,
single-call-site indirection, and known-unused scaffolding.

---

## Tier 1 — Genuine cruft (safe to delete)

### 1. `mcloop/workspace_context.py` — CRUFT — DELETED
- 303 lines + `tests/test_workspace_context.py` (227 lines).
- Defines `WorkspaceContext` dataclass and `resolve_workspace_context()` resolver.
- **No production import.** Confirmed by reading every module's import block.
  No file in `mcloop/` imports `mcloop.workspace_context`. Only consumer is
  the test file.
- Built during Stage 12 of the workspace-context migration that was then
  abandoned (Stages 13–20 deleted from PLAN.md).
- **Disposition:** deleted in cleanup. If you ever resume the bigger
  architecture migration, the files are in git history.

---

## Tier 1.5 — Internal cruft inside live modules

These were not whole-module deletion candidates, but they were confirmed as
production-dead code inside otherwise live modules.

### A. `errors._error_signature_hash` — CRUFT — DELETED
- Defined in `mcloop/errors.py` and exercised only by tests.
- No production caller in `mcloop/` or sibling repos.
- The adjacent `_check_errors_json` docstring incorrectly claimed hash-based
  fix-attempt tracking; source actually reads and increments each entry's
  `fix_attempts` field directly.
- **Disposition:** helper and tests deleted; `_check_errors_json` docstring
  corrected to describe the current `fix_attempts` counter behavior.

### B. `prompts.build_investigation_plan_description` — CRUFT — DELETED
- Defined in `mcloop/prompts.py` and exercised only by tests.
- The active investigation path uses `investigator.generate_plan` from
  `investigate_cmd.py`, not this prompt builder.
- **Disposition:** function and tests deleted.

### C. Public-shaped helpers with no current production caller — PENDING USER DECISION
- `process_monitor.is_hung`, `process_monitor.send_input`, and
  `process_monitor.send_signal` are tested helpers with no current production
  caller in McLoop.
- `app_interact.type_text` is tested but has no current production caller.
- `orchestra_override.banner_text` is tested but production appears to use the
  line-oriented banner helper.
- These could be dead code or intentional library surface. Do not delete them
  without an explicit API decision.

---

## Tier 2 — Stale (correct content, misleading framing)

### 2. `mcloop/_planfile_compat.py` docstring — STALE
- File docstring says: *"This module is intentionally additive and unused by
  runtime code in Stage B0.2."*
- Source reality: `main.py:17–37`, `lifecycle.py:18–22`, `output.py:10–14`,
  `investigate_cmd.py:21` all import from `_planfile_compat`. It IS runtime
  code now. The de-split cutover happened.
- **Recommendation:** rewrite the docstring. The module is fine; the comment
  lies. One-line fix.

### 3. `mcloop/_planfile_compat.py` and `mcloop/_planfile_precondition.py`
**naming** — SUSPECT
- Leading underscore conventionally signals "private to package." Both modules
  are imported across mcloop.
- `_planfile_precondition.PlanNotCanonicalError` is not itself a public
  user-visible name: `main.py` catches it, prints the exception message, and
  exits with code 3. The user-visible surface is the exit-code-3
  canonical-plan precondition behavior, not the class identity.
- This is a naming smell, not cruft. The leading underscore made sense during
  the migration when only one specific module was meant to import them.
- **Recommendation:** rename to `planfile_compat.py` and
  `planfile_precondition.py` if you care, or leave it. Renaming touches every
  import site. Low priority.

### 4. Stage 11 `_refuse_nested_init` guard — SUSPECT but justified
- Lives in `git_ops.py:48–80`. Lands in Stage 11 as the immediate consolidation
  protection before the upward walk in Stage 13's T-000382.
- With T-000382's upward walk in `_ensure_git`, the guard is reachable only in
  the narrow case of being inside a uv workspace package with no `.git`
  anywhere up the tree.
- **Not cruft.** The case the guard covers (workspace pyproject exists but
  `.git` is somehow absent) shouldn't happen but is exactly the kind of
  edge case worth detecting loudly. Defense-in-depth is appropriate here.
- **Recommendation:** keep, but the docstring should note that this is
  defense-in-depth, not the primary mechanism. Currently `_ensure_git`'s
  docstring acknowledges this; the guard's own docstring does not.

---

## Tier 3 — Functional cruft signals worth investigating

The audit hasn't traced internal call sites within large modules. The following
are size or pattern signals that warrant a focused pass:

### 5. `main.py` is a long file — SUSPECT
- It's the entry point and the run loop, so length is expected.
- Actual line counts at audit time:
  - `main.py`: 2778
  - `audit.py`: 344
  - `lifecycle.py`: 698
  - `output.py`: 302
  - `errors.py`: 245
  - `review_integration.py`: 171
- Has been subject to periodic extractions, but only two of the named modules
  (`audit.py` and `output.py`) literally say "extracted from main.py" in their
  module docstrings. The earlier claim that all five docstrings said this was
  overstated.
- A long file isn't automatically cruft, but in a codebase with this much
  extraction history it's worth asking whether more extraction is pending.
- **Recommendation:** not actionable without a function-by-function read of
  `main.py`. Half-day's work to do properly. Defer.

### 6. `prompts.py` imports `investigator.py` constants — SUSPECT
- `prompts.py:5–11` imports five constants from `mcloop.investigator`
  (`DEBUGGING_INSTRUCTION`, `DEBUGGING_PLAYBOOK`, `PROBES_INSTRUCTION`,
  `TESTING_INSTRUCTION`, `WEB_SEARCH_INSTRUCTION`).
- The constants are prompt fragments. Their natural home is `prompts.py`,
  not `investigator.py`, but three of them (`DEBUGGING_PLAYBOOK`,
  `PROBES_INSTRUCTION`, `WEB_SEARCH_INSTRUCTION`) are also used by
  `investigator.generate_plan`.
- This looks like the constants were extracted from `main.py` into
  `investigator.py` at a point when only investigation needed them, and then
  other prompt builders started reusing them without anyone moving them.
- **Recommendation:** future action only: reverse the import direction by
  moving all five constants to `prompts.py` and having `investigator.py` import
  them from there. Do not bundle with unrelated cleanup.

---

## Tier 4 — Modules that look clean

Verified by reading the docstring and confirming a clear single responsibility:

- `app_interact.py` — AppleScript wrappers for GUI interaction. Used by `mcloop
  wrap` instrumentation. CLEAN.
- `audit.py` — Audit cycle, extracted from main.py. CLEAN.
- `checks.py` — Project test/lint suite detection and execution. CLEAN.
- `claude_md_check.py` — CLAUDE.md freshness check and NOTES.md update. CLEAN.
- `claude_md_sync.py` — Deferred CLAUDE.md sync queue. CLEAN.
- `code_edit.py` — Direct vs Orchestra backend dispatch. CLEAN.
- `config.py` — Reviewer config loading with role-based schema and
  back-compat. CLEAN.
- `conftest_guard.py` — Inject LLM-call-blocking conftest fixture. CLEAN.
- `dep_validator.py` — Pre-flight dependency installation check. CLEAN.
- `errors.py` — Crash handling and `_insert_bugs_section` mutator. CLEAN.
- `formatting.py` — Terminal output styling. CLEAN.
- `git_ops.py` — Git operations including the upward walk + `--relative`
  fixes from Stage 13. CLEAN.
- `idea_cmd.py` — `mcloop idea` subcommand. CLEAN.
- `install_cmd.py` — Install/uninstall subcommands. CLEAN.
- `investigate_cmd.py` — Investigation subcommand with worktree setup. CLEAN
  (though see #6 above for `investigator.py`).
- `ledger_config.py` — Slice D per-run config gating. CLEAN.
- `ledger_emit.py` — Slice D event emission. CLEAN.
- `ledger_pause.py` — Slice D threshold evaluation and auto-reauthor. CLEAN.
- `lifecycle.py` — Process lifecycle, orphan cleanup, signal handlers. CLEAN.
- `maintain.py` — Maintain mode subcommand. CLEAN.
- `notify.py` — Telegram + iMessage notification. CLEAN.
- `orchestra_override.py` — Project-local Orchestra config override
  acknowledgment. CLEAN.
- `output.py` — Display functions, extracted from main.py. CLEAN.
- `process_monitor.py` — GUI app process monitoring for `mcloop wrap`. CLEAN.
- `pytest_optimizations.py` — Inject pytest-xdist + pytest-timeout. CLEAN.
- `ratelimit.py` — Rate-limit detection patterns and CLI fallover. CLEAN.
- `review_integration.py` — Reviewer subprocess spawn/collect. CLEAN.
- `reviewer.py` — Reviewer logic with three dispatch backends. CLEAN.
- `run_summary.py` — `.mcloop/runs/` schema and writing. CLEAN.
- `runner.py` — AI CLI subprocess runner. CLEAN.
- `session_context.py` — Rolling session context between task sessions. CLEAN.
- `sync_cmd.py` — `mcloop sync` subcommand. CLEAN.
- `targeted.py` — Source→test file mapping. CLEAN.
- `test_runner.py` — Test command resolution with fallback chain. CLEAN.
- `web_interact.py` — Playwright wrappers, lazy-imported. CLEAN.
- `worktree.py` — Git worktree management for investigation. CLEAN.
- `wrap.py` — Source instrumentation for crash handlers. CLEAN.

---

## Summary

| Tier | Count | Disposition |
|------|-------|-------------|
| Genuine cruft (Tier 1) | 1 module + 1 test file (530 lines) | Deleted |
| Internal cruft (Tier 1.5) | 2 deleted items (206 lines), 3 pending helper groups | Deleted / pending user decision |
| Stale comments (Tier 2) | 2 items | Rewrite |
| Naming smell (Tier 2) | 2 modules | Rename if you care |
| Cruft signals (Tier 3) | 2 items | Investigate before acting |
| Verified clean (Tier 4) | 37 modules | Leave alone, except `errors.py` had internal cruft removed |

**Concrete action already taken:** Tier 1 deletion was unambiguous —
`workspace_context.py` and `tests/test_workspace_context.py` were dead and
removed. Two internal cruft pockets were also removed:
`errors._error_signature_hash` and `prompts.build_investigation_plan_description`.

Tier 2 fixes (stale docstring, defense-in-depth comment, leading-underscore
rename) are cosmetic. Worth doing in a single scoped commit but no urgency.

Tier 3 items need real investigation. The `prompts.py` ↔ `investigator.py`
constant-import inversion (#6) is the most obvious one, but the right future
fix is to move the constants to `prompts.py` and import them from
`investigator.py`, because `investigator.generate_plan` still uses three of
the five constants.

**The codebase is in better shape than the size suggests.** 42 modules
sounds inflated for a single loop, but 37 of them have clear single
responsibilities and obvious call sites. The one genuine dead module is
recent scaffolding from a migration that didn't happen. The pattern of
"large extraction history" suggests the codebase has been actively
refactored, not allowed to accrete.

**Bottom line: low cruft. The confirmed dead module/test and two confirmed
dead internal helpers are gone. Remaining work is documentation/naming cleanup
and explicit user decisions on public-shaped helpers that have no current
production callers.**
</content>
