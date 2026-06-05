# Notes

## Observations

### [4] [T-000004] `--retry-task` resolves a colliding id to BUGS.md first (2026-06-04)
`--retry-task TASK_ID` (run_loop `retry_tasks=`) resets a single `[!]`-failed
task back to `[ ]` via `_reset_failed_tasks`, which searches BUGS.md before
PLAN.md and stops at the first failed match for that id. PLAN.md and BUGS.md
are migrated independently, so the same `T-NNNNNN` can name a task in each
file (both files start numbering at T-000001). When an operator passes such a
colliding id, only the BUGS.md task is reset; the PLAN.md namesake keeps its
`[!]` marker. This matches the loop's task-selection priority (bugs first) and
is covered by `test_reset_failed_tasks_resets_named_and_reports_missing`, but
it means an operator wanting the PLAN.md task reset for a colliding id has no
way to disambiguate today. Acceptable for now since within a single run the
ids the operator sees in one file are the ones they act on; revisit if
cross-file id collisions become user-visible.

### [3] [T-000003] `has_waiver` now keys on task identity, with baseline as a fallback (2026-06-04)
`waivers.has_waiver` no longer requires exact `baseline_sha` equality. It
matches a recorded waiver when `changed_input` matches AND either the
current `task_label` matches the record's (the durable key — survives a
mid-task commit/checkpoint that advances `.mcloop/task-baseline`) OR the
exact `baseline_sha` matches (fallback for environments that leave
`MCLOOP_TASK_LABEL` unset, e.g. native Anthropic models — see
`code_edit.py:419`). The call site `checks._resolve_flagged` reads
`MCLOOP_TASK_LABEL` and passes it through. Empty task_label + empty
baseline still never matches, so the gate cannot be blanket-bypassed.
Assumption worth revisiting: an empty `task_label` recorded by `mcloop
waive` (unset env) can only ever be matched by baseline equality, so such
waivers do NOT survive a baseline change — the survival guarantee holds
only when a real task label is present at both record and check time.

### [1] [T-000001] No-test-needed class draws the line at "carries executable logic", not "is non-.py" (2026-06-04)
`change_class.is_no_test_needed_input` exempts dependency manifests, tool
config, requirement/lock files, and plain data/docs (by suffix + a few
dotfile names), routed through `verify_change_covered` so the coverage gate
itself clears them with no run. It deliberately does NOT exempt every
non-Python input: templates (.j2/.jinja/.html/.mako), .sql, build scripts
(.sh), and Makefile still embed behavior and remain subject to the normal
mapped-test / waiver requirement. Only the inputs whose `_BEHAVIOR_SUFFIXES`
membership in `targeted.py` causes them to be accounted *and* that match the
exempt class actually exercise the new bypass; non-accounted files
(e.g. requirements.txt, *.lock) were never flagged to begin with and pass
trivially. `.py` is never exempt (the existing test/coverage requirement for
executable source is preserved).

### [14.9] [T-000392] Coverage instrumentation is structurally confined to the scoped per-task path (2026-06-01)
Confirmed the placement invariant rather than re-measuring it. The only code
that builds coverage instrumentation (`--cov=<module>` + `--cov-report=json:`)
is `coverage_verify._run_coverage`, reached solely through
`run_checks` → `_resolve_flagged` → `verify_change_covered`. That chain lives
entirely inside the `if changed_files is not None:` branch of `run_checks`.
The phase-boundary full-suite call is `run_checks(project_dir)` (main.py ~1435)
with `changed_files=None`, which short-circuits past that branch — so the full
suite can never run under coverage, never per-iteration beyond the per-task
gate, and is never agent-invoked (the inner `mcloop verify` adapter routes only
through the scoped `run_checks(changed_files=...)` form; `verify_cmd.py` ~16
documents it never calls the unscoped form).

Tests pinning the invariant from both sides:
- `tests/test_checks.py::test_phase_boundary_full_suite_has_no_coverage_instrumentation`
  — full-suite path never calls `verify_change_covered`/`_run_coverage` and emits
  no `--cov` flag.
- `tests/test_coverage_verify.py::test_run_coverage_emits_cov_instrumentation_scoped_to_change`
  — the scoped path is where `--cov=<dotted module>` + explicit candidate nodes
  are produced.

Overhead envelope (<=~0.5s/run scoped, ~+9.5% full-suite) is unchanged because
the scoped run is bounded to the dependent-test candidate set (never the full
suite) and the full-suite phase-boundary call carries no coverage at all, so its
cost is identical to the pre-coverage full-suite run. The ~+9.5% figure refers to
adding `pytest-cov` to a coverage-enabled full run, which mcloop never performs;
the per-task scoped coverage run is the only coverage cost and stays within the
~0.5s envelope.

### [14.8] [T-000391] Coverage-proven verification fallback for unmapped behavioral Python changes (2026-06-01)
`run_checks` no longer hard-fails the moment an unmapped behavioral change is
flagged. `_resolve_flagged` now gives each flagged source a second chance:
- an explicit waiver in `.mcloop/test-verification-waivers.jsonl` (keyed on
  changed input + the task's pre-edit baseline SHA) clears it, or
- a Python change is cleared when `coverage_verify.verify_change_covered`
  proves its changed diff-hunk lines were executed by a scoped candidate test.
Non-Python behavior inputs have no executable coverage lines and can only be
cleared by a named-test mapping (upstream) or a waiver.

Key design choices worth revisiting:
- **"Proven" = at least one changed line executed.** `verify_change_covered`
  passes when `changed_lines ∩ executed_lines` is non-empty, not when every
  changed line is covered. Requiring full coverage would reject changes whose
  defensive/error branches never run under the dependent test. If the gate
  needs to be stricter, tighten this intersection rule.
- **Coverage scope target is the dotted module** (`pkg/widget.py` →
  `--cov=pkg.widget`). This assumes the changed file is an importable project
  module; top-level non-package scripts (e.g. a hyphenated filename) would not
  resolve as a module and the JSON would carry no entry for them, yielding an
  empty executed set (treated as not-proven → fail/waiver). Acceptable
  fail-closed behavior, but noted.
- **Dependent-test discovery is a transitive first-party import walk**
  (`dependent_test_files`). It selects only tests whose import closure reaches
  the changed module — never the full suite. A test that exercises the module
  through a non-import path (dynamic import, subprocess, plugin entry point) is
  not discovered and the change would fail closed.
- **xdist + pytest-cov need no extra config.** pytest-cov combines per-worker
  data automatically under `-n auto`; `pytest_optimizations` injects only the
  `pytest-cov` dev dep (no `parallel=true`/`concurrency`/`sitecustomize`/
  `COVERAGE_PROCESS_START`). The startup `validate_project_dependencies` gate
  now fails fast if the target venv lacks pytest-cov.
- **Waivers are never written by the gate.** They are recorded only via the
  explicit `mcloop waive --input ... --reason ...` subcommand (task label from
  `MCLOOP_TASK_LABEL`, baseline from `.mcloop/task-baseline`), so every bypass
  is auditable.

### [14.7] [T-000390] targeted_pytest_command now emits an explicit, PATH/cwd-independent command (2026-06-01)
`targeted_pytest_command(test_files, project_dir)` no longer returns a bare
`pytest <relative paths>` string. It now emits a resolved executable prefix
(`<project>/.venv/bin/pytest` when present, else `<sys.executable> -m pytest`)
plus fully-qualified (absolute) node paths anchored to `project_dir.resolve()`.
This keeps targeted selection from collapsing to zero collection or invoking
the wrong pytest when ambient cwd/PATH differ from the project. `is_test_command`
was widened to recognize the resolved forms (basename `pytest`, or a
`python*`-basename interpreter with `-m pytest`) and now uses `shlex.split` so
the signal-verdict path in `run_checks` still fires on the explicit command.

Assumption: the fallback `<sys.executable> -m pytest` presumes pytest is
importable in the interpreter running mcloop when the target project has no
`.venv/bin/pytest`. This mirrors the prior reliance on a PATH `pytest`, but
pins it to mcloop's own interpreter rather than the shell PATH. If a target
project uses a non-`.venv` layout (e.g. `venv/`, poetry cache, conda), the
prefix falls back to mcloop's interpreter; revisit if such layouts appear.

### [14.6] [T-000389] run_checks now FAILS the gate on unaccounted behavioral changes (replaces full-suite fallback) (2026-06-01)
The 14.5 design fell back to the full configured test suite whenever any
changed input was unmapped. That fallback has a hole: the full suite can
pass *vacuously* -- it runs every existing test while never exercising the
unmapped change -- so a new module with no test, or a rename inside an
untested module, would still ship green. T-000389 replaces the fallback
with a fail-closed gate. `checks._unaccounted_behavioral_changes` walks the
unmapped accounts and, for each, decides whether the change can be *proven*
inert via `mcloop.change_class.classify_change` (compares the HEAD baseline
from `git_ops.read_file_at_head` against the on-disk new content). Anything
not provably non-behavioral flags the gate, and `run_checks` returns
`CheckResult(passed=False)` *before launching any check command*.

`classify_change` returns `NON_BEHAVIORAL` only for: comment-only edits,
docstring-only edits (conventional leading string stripped), AST-equivalent
formatting (compared via `ast.dump(..., include_attributes=False)`), and
import reordering with an unchanged import graph (leading import block and
per-statement alias lists sorted). Everything else -- renames, `__all__`,
decorators, dataclass fields, added imports, deleted/unreadable files, any
non-Python behavior input (pyproject.toml, data, templates), and any
unparseable source -- is `BEHAVIORAL`. A missing baseline is treated as an
empty file, so a brand-new code module flags while a new empty/comment-only
file does not.

Behavior changes worth noting:
- Editing a non-Python behavior input (e.g. pyproject.toml) with no mapped
  test now FAILS the gate where 14.5 ran the full suite. This is the
  conservative direction; the T-000391 waiver path is the intended escape
  hatch.
- Two 14.5 tests asserting full-suite fallback were rewritten to the
  fail-the-gate contract (`test_run_checks_unmapped_behavioral_change_fails_gate`
  and `test_run_checks_mixed_batch_unmapped_behavioral_fails_gate` in
  test_targeted.py).

## Hypotheses

### [14.6] [T-000389] docstring changes are classified non-behavioral even when `__doc__` is runtime-consumed (2026-06-01)
`classify_change` strips the conventional leading docstring before
comparing, so a docstring-only edit is always `NON_BEHAVIORAL`. The task's
allowlist names "docstring-only when not runtime-consumed", but proving
non-consumption (argparse `description=__doc__`, doctests, help text built
from `__doc__`) is not attempted -- it would require whole-program data-flow
analysis. The required allowlist test demands docstring-only -> non-behavioral,
so the strip is intentional. If a docstring that feeds a runtime path is ever
edited in an untested module, the gate would let it through; the linter and
the eventual T-000391 waiver review are the backstops. Narrow this by only
stripping docstrings of functions/classes that are never referenced by
`__doc__`-reading callers if it ever bites.

### [14.5] [T-000388] map_to_tests now accounts unmapped inputs; checks.py falls back on any unmapped (2026-06-01)
`mcloop/targeted.account_changed_inputs` replaces the silent-drop in
`map_to_tests`: every behavior-relevant changed input yields an
`InputAccount` that is either mapped (`test_files`) or explicitly
`unmapped` with a `reason`. `map_to_tests` is now a flat projection of
that accounting (mapped files only), so its public output is unchanged.
`checks.py::run_checks` was rewired: `fallback_to_full` now fires when
**any** account is unmapped (previously only when the targeted set was
globally empty), and the fallback branch is checked **before** the
targeted branch. This fixes the mixed-batch bug (PLAN ~540): a batch with
one mapped file and one unmapped file previously ran only the mapped
file's tests and shipped the unmapped file untested; it now runs the full
suite.

Behavior changes worth noting:
- A changed `__init__.py`/dunder is behavior-relevant but never maps by
  name, so it is now `unmapped` → full-suite fallback (same as the old
  `py_changed and not test_files` path). `map_to_tests` still returns `[]`
  for it.
- Non-Python behavior inputs (pyproject.toml, *.json/*.yaml data,
  templates, entry-point declarations) are now accounted and trigger
  fallback. Editing e.g. pyproject.toml now runs the full suite where it
  previously left tests scoped/skipped. This is the conservative
  direction (fail toward running more tests).
- Pure docs (`.md`, `.rst`) are intentionally excluded from accounting so
  a README/docs-only batch still skips tests entirely.

## Hypotheses

### [14.5] [T-000388] module-name `-k` matching is content-scan based and could over-broaden (2026-06-01)
When no `test_<stem>.py` exists, `_k_referencing_tests` reads every
`tests/**/test_*.py` and selects those whose text contains the module
stem as a whole word (`\b<stem>\b`). For a common stem (e.g. `config`)
this may select many test files in a real run, widening a "targeted" run
toward the full suite. It only runs when the conventional file lookup
finds nothing, so it errs toward more coverage, but if targeted-run speed
regresses this scan is the likely cause. The match is filename-agnostic
text containment, not a true pytest `-k` node — `k_module` is recorded on
the account for callers that may later want to emit an actual `-k` flag.


### [14.3] [T-000386] verify adapter treats an empty changed-set as fail-closed (2026-06-01)
`mcloop/verify_cmd.run_verify` distinguishes three outcomes from
`git_ops._changed_files_since`: `None` (cannot resolve — empty baseline,
no repo, or git error), `[]` (baseline resolves but nothing changed), and
a non-empty list. Both `None` and `[]` exit non-zero (`EXIT_FAIL_CLOSED`)
and never reach `run_checks`. The `[]` case is a deliberate design choice:
calling `run_checks(project_dir, changed_files=[])` would skip the test and
lint commands (no targeted tests, no changed .py) and return a vacuous
`passed=True`, which the task explicitly forbids ("fail closed rather than
... an empty pass"). So an in-session adapter run with no detectable edits
is reported as a failure, not a silent green. If a legitimate
zero-change verification is ever needed, that branch is the single place to
relax.

### [14.2] [T-000385] Signal predicate counts only passed+failed (2026-06-01)
`pytest_signal_verdict` in `mcloop/pytest_signal.py` defines valid signal as
`passed + failed >= 1`, matching the task's literal wording ("at least one
test executed to a pass or fail outcome"). This means a run that produced
*only* xfailed/xpassed outcomes (tests genuinely ran, just as
expected-fail / unexpected-pass) is currently judged as no-signal and would
fail `run_checks`. Such pure xfail/xpass runs are rare in practice and were
not among the four required invalid cases, so the simpler literal predicate
was chosen. If this ever bites, fold xfailed/xpassed into the "executed"
count — the structured counts are already parsed and available.

### [76.2] Codex CLI flag change (2026-03-14)
Codex CLI no longer accepts `--ask-for-approval never --sandbox workspace-write`.
The replacement is `--full-auto` (convenience alias for `-a on-request, --sandbox workspace-write`).
Updated `_build_command` in runner.py accordingly.

### [76.2] Codex panics inside Claude Code sandbox (2026-03-14)
Running `codex exec` inside Claude Code's sandbox causes a Rust panic in
`system-configuration-0.6.1/src/dynamic_store.rs:154` ("Attempted to create
a NULL object"). This happens even when run directly (not via mcloop).
The integration test `test_real_codex_creates_file_and_commits` is correctly
gated behind `MCLOOP_INTEGRATION=1` so it won't affect normal test runs,
but it cannot pass until Codex is run outside the sandbox or the Codex bug
is fixed.

### [7.3.1-7.3.3] Maintain prompt had conflicting check instructions (2026-04-09)
`_build_maintain_prompt` embedded its own CHECK COMMANDS section while
`run_task` → `_build_normal_prompt` added "ABSOLUTELY FORBIDDEN: do not
run any tests". These conflicting instructions could confuse the session.
Fixed by removing embedded check commands from the maintain prompt and
passing `check_commands` to `run_task` properly, so `_build_shared_parts`
handles it.

### [7.3.1-7.3.3] mcloop maintain cannot connect to API from spawned session (2026-04-09)
Both attempts to run `mcloop maintain` failed with `FailedToOpenSocket`
after exhausting all 10 retries. The spawned Claude Code subprocess
cannot reach the API. This is an infrastructure issue — the maintain
mechanism itself parsed invariants, built prompts, and handled failures
correctly. Live verification blocked until API connectivity is resolved.

### [2] shell=True orphan fix only covers run_cli (2026-04-17)
`launch()` now passes `start_new_session=True` so every shell-wrapped
child is its own process-group leader, and `run_cli`'s hang/timeout
paths use the new `kill_process_group` helper (SIGTERM→SIGKILL on the
group). Other callers of `kill(pid)` in the codebase (notably
`run_gui`'s kill_on_return path and `lifecycle.cleanup_orphan_processes`)
still target single PIDs. Those paths either already resolve the true
app PID via pgrep or do their own targeted verification, so they were
left alone to keep this fix minimal, but anything that launches via
`launch()` and kills via single-PID `kill()` could leak orphans the
same way. Audit those call sites if a similar bug recurs.

Reproduced the original bug with `sleep 120 & echo $! > pidfile; wait`
launched via `shell=True` with no `start_new_session`: killing only the
shell pid leaves the `sleep` child running. With the fix, killpg on the
group takes out both. Reproduction script kept at
`/tmp/claude/verify_orphan_fix.py` for reference (not committed).

### [3] Interrupted skip now targets the active split-plan file (2026-04-17)
`_check_interrupted` previously called `mark_failed(checklist_path, t)` with
checklist_path = master PLAN.md. Under the split-plan design the loop only
reads CURRENT_PLAN.md and BUGS.md, so the [!] landed in a file the loop no
longer consults and the "skipped" task was retried on the next run.

Fix: added an `active_paths` parameter (priority-ordered: BUGS.md,
CURRENT_PLAN.md, PLAN.md) and the skip/describe branches now mutate the
first path in that list that contains the task as unchecked. The fallback
to `[checklist_path]` preserves pre-split-plan test behavior. run_loop
filters active_paths to existing files before passing; on a fresh clone
with only PLAN.md this correctly degrades to master-only.

Edge case left intentional: if the task text matches in multiple split
files (e.g. duplicated between BUGS.md and CURRENT_PLAN.md), only the
first unchecked hit is marked. The bug-priority ordering matches how
find_next picks tasks in run_loop, so behavior stays consistent.

### [12.2] [T-000380] resolve_workspace_context edge case: consolidated root scope with cwd outside workspace_root (2026-05-22)
Rule (5) says ``execution_cwd = cwd`` in the consolidated case. When
``plan_path`` is explicit and points at the workspace root itself
(scope resolves to ``"root"``) but ``cwd`` is somewhere else (e.g.
``/tmp``), the resolver constructs a ``WorkspaceContext`` with
``workspace_root == scope_root != execution_cwd``, which trips the
compatibility-mode invariant in ``__post_init__`` and surfaces as an
``AssertionError`` rather than a ``WorkspaceResolutionError``. Rule (3)
does not catch this because the ambiguity check only fires when
``cwd`` is inside a *different* workspace, not when it is in no
workspace at all. T-000381's listed cases do not exercise this path
(its consolidated root case uses ``cwd == workspace_root``), so the
behavior is accepted as-is for now. If a future stage needs a
friendlier error here, the resolver should either coerce
``execution_cwd`` to ``workspace_root`` when ``scope == "root"`` in
the consolidated case or raise a structured error pre-construction.

## Hypotheses

## Eliminated

6c95f88: Parallelized check execution to improve performance by running independent commands concurrently. Updated tests to handle non-deterministic execution order and added a concurrency test to verify parallel behavior.

756c468: Re-enabled the CHECK COMMANDS block in the runner prompt generation, which provides mandatory check instructions to the inner Claude when check_commands are supplied. Updated corresponding tests to verify the block appears when check_commands are provided and is omitted when they are not. This restores the ability for the inner session to run checks and catch failures itself.

a05a67d: The CLAUDE.md sync process was moved to a background thread to prevent blocking the main loop during LLM calls. The sync function now returns a thread reference instead of waiting for completion, and error handling was updated to log failures without crashing. Tests were added to verify non-blocking behavior and proper exception handling in the background thread.

02dec40: Added mypy type checking support. The tool now automatically runs 'mypy .' if a project contains either a [tool.mypy] section in pyproject.toml or a mypy.ini file. This ensures type checking is included in the validation pipeline alongside existing ruff and pytest commands.

e60af39: Improved error handling for screencapture failures and bug verification. Fixed bug filtering to use exact title matches, preventing unrelated bugs from being dropped. Enhanced checklist marker clearing to avoid corrupting prose. Added better timeout handling and noqa comment detection for style checks. Made CLAUDE.md sync wait for completion to prevent race conditions. Improved worktree detection to avoid false positives. Enhanced output buffering to preserve both head and tail of long sessions. Fixed crash handler injection for Swift apps without an init(). Updated tests to cover new edge cases.

cba4873: Changed how user-reported failures are recorded in BUGS.md: instead of flattening and truncating the observation, it now preserves the full multi-line observation verbatim inside a fenced code block. Updated tests to verify the new behavior and removed unnecessary line breaks in test data strings.

5e6fe77: Added automatic archiving of completed bug reports. When bug entries are marked as done, they are now moved to a separate "BUGS-resolved.md" file instead of being deleted. This preserves historical resolution records while keeping the active bug queue concise. The resolved file is created only when there are done bugs to archive.

e8df686: Added safety check to refuse git init inside a uv workspace package subdirectory, preventing nested repository creation that would break cross-package operations. Updated README to clarify phase boundary behavior and exit notifications for stop flags.

9b9ae06: Added a new WorkspaceContext class to manage workspace and scope adaptation during migration. It enforces a compatibility-mode invariant for standalone repo runs, ensuring workspace_root, scope_root, and execution_cwd are identical when scope is "root". Includes comprehensive tests for the dataclass behavior and invariant validation.

b751f8f: Fixed an edge case in workspace resolution where specifying a plan at the workspace root while the current directory is outside the workspace would cause an assertion error. Added structured error handling with WorkspaceResolutionError to provide clearer diagnostics instead of crashing.

5e92f65: Updated git initialization to walk up the directory tree and use an existing parent repository if found, preventing nested git repos in consolidated workspaces. The existing guard against uv workspace packages remains as a defense-in-depth measure. Added corresponding tests for consolidated layouts and worktree scenarios.

733d88e: Updated git helpers to support consolidated workspace layouts by ensuring all file paths are returned relative to the current working directory. Added `--relative` flags to git diff commands and adjusted `_worktree_status` to strip workspace prefixes, maintaining consistent package-relative paths across functions. Added comprehensive tests for both standalone and consolidated workspace scenarios.
