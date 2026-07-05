## Bugs

- [x] T-000001: Ruff UP047 fails on two generic helper functions in `bob_tools/planfile/parser.py`; update the type syntax only, with no behavior change. Current `.venv/bin/ruff check .` output: <!-- completed_at: 2026-05-19T09:45:01Z -->

```text
UP047 Generic function `_attach_ruledout` should use type parameters
   --> bob_tools/planfile/parser.py:589:5
    |
589 |   def _attach_ruledout(
    |  _____^
590 | |     indent: int,
591 | |     stack: Sequence[_T],
592 | |     root_tasks: Sequence[_T],
593 | | ) -> _T | None:
    | |_^
594 |       """Resolve the task a ``[RULEDOUT]`` line should attach to.
    |
help: Use type parameters

UP047 Generic function `_attach_deps` should use type parameters
   --> bob_tools/planfile/parser.py:613:5
    |
613 |   def _attach_deps(
    |  _____^
614 | |     indent: int,
615 | |     stack: Sequence[_T],
616 | | ) -> tuple[_T | None, bool]:
    | |_^
617 |       """Resolve the task a ``@deps`` sibling line should attach to.
    |
help: Use type parameters

Found 2 errors.
No fixes available (2 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

Offending construct: both helpers are generic functions using the module-level `_T = TypeVar("_T")` style in signatures with `Sequence[_T]` and `_T` return values. Required end state: `.venv/bin/ruff check .` is clean; `.venv/bin/pytest -q` still reports 353 passing tests; `.venv/bin/mypy --strict bob_tools` still passes.

- [x] T-000002: `bob-plan fmt` should assign ids to bare checkboxes regardless of the `<!-- bob-plan-format: N -->` marker. Currently a file carrying the marker is validated (bare checkbox rejected with "expected task id after checkbox marker") instead of migrated. Confirm the migrate-vs-validate trigger in `bob_tools/planfile/cli.py:cmd_fmt` / `operations.migrate` and make `fmt` migrate un-id'd checkboxes even when the marker is present, since assigning ids is what `fmt` is for. Add a regression test: a marker-bearing file with a bare checkbox gets an id assigned, not an error. <!-- completed_at: 2026-07-04T21:03:57Z -->
- [x] T-000003: `bob-plan` error messages report the wrong filename: `bob-plan fmt BUGS.md` surfaced "PLAN.md invalid at line 127" when the offending line was in BUGS.md. Fix the error formatting to name the file actually being processed. Add a regression test asserting the reported path matches the input argument. <!-- completed_at: 2026-07-05T11:27:54Z -->
- [ ] T-000004: `save`/`update` with validation="canonical", magic=False always raises: magic=False sets magic_version=None, but `_render_for_validation` (planfile/fileio.py ~313) runs validate_plan(constructed=True, ...) first, which appends an error whenever magic_version != 1, so `assert_mcloop_canonical` (documented to accept a cleared magic line, validation.py ~285) is never reached. The documented loose-queue path is unusable; every in-repo magic=False caller works around it with validation="unchecked". Make the constructed-mode validator honor the cleared-magic case (or route canonical+magic=False around the magic check). Regression test: a canonical save of a magic-less plan succeeds. (Found in the 2026-07-02 cross-package audit.)
- [ ] T-000005: A Task with both an action-tag argument and body text does not round-trip: the renderer emits `[AUTO:run] --fast the description` (planfile/renderer.py ~175-181) but `_extract_action_tag` (parser.py ~969-987) consumes everything after the tag to end of line as args, so re-parse yields args="--fast the description", text="" -- breaking the parse(render(plan)) == plan contract that make_task guards against but other Task constructors do not. Either make the renderer refuse or encode the ambiguous combination, or make the parser delimit args. Add a property test over Tasks combining tag, args, and text.
- [ ] T-000006: planfile/preflight.py ~189 collapses per-validator messages: `exc.args[0]` is always a string (PlanValidationError.__init__ joins the messages before calling super().__init__), so the isinstance(..., list) branch is dead and the code always falls back to [str(exc)], discarding the individual messages it exists to preserve. Use exc.messages. Regression test: a plan with two validation errors surfaces two entries.
- [ ] T-000007: Write-path durability holes: planfile/backfill.py ~181 `backfill_file` writes with path.write_text -- no atomic tempfile+rename and no sidecar flock, unlike every other writer, so a crash mid-write truncates PLAN.md and a concurrent save/update can interleave; planfile/fileio.py load/update/_atomic_write_text (~128/~259/~365) use the platform locale encoding while backfill pins utf-8, so a non-UTF-8 locale misdecodes or re-encodes a non-ASCII plan inconsistently (pin utf-8 everywhere); and _atomic_write_text fsyncs the tempfile but not the directory after os.replace, so a crash right after the rename can lose the update despite the crash-safety claim. Route backfill through _atomic_write_text plus the lock, pin encodings, add the directory fsync. Regression tests where practical.
- [ ] T-000008: Ledger concurrency/idempotency gaps: ledger/storage.py append (~162-174) takes no lock and a commit_landed event with a large touched_paths list can exceed PIPE_BUF, so two concurrent writers (explicitly allowed by the design) can interleave a line and corrupt the JSONL, aborting iter_events/projection; _read_next_seq (~92-101) silently resets to 0 on an empty or unparseable seq file, colliding the (writer_id, seq) tiebreaker; ledger/projector.py dedups phases/assumptions by id but not invariant_declared, human_decision_recorded, finding_observed, or the evidence_refs/design_reasoning_refs appends (~403-603), so a duplicated event line double-counts and re-fires threshold crossings; ledger/thresholds.py record_crossings (~593-611) is a check-then-act race across processes (reads existing crossing keys, then appends, with no lock spanning the two). Add file locking or single-writer enforcement on append, refuse to reset seq on corruption, dedupe projection by event_id across all record types, and lock the read+append span in record_crossings. Regression tests per fix.
- [ ] T-000010: `bob-plan fmt` crashes with "bugs.tasks[0].trailing_lines must be empty on constructed tasks" on a file whose completed task carries a trailing code block -- observed 2026-07-03 running fmt on this very BUGS.md (T-000001 above has a fenced ruff-output block as trailing lines). fmt re-renders through the constructed-mode validator, which rejects the trailing_lines the parser legitimately captured from disk, so any plan that uses the documented lossless trailing-lines capture cannot be fmt'd. fmt must either preserve trailing lines through a parse-mode (not constructed-mode) validation path or exempt trailing_lines when the source was a file. Regression test: fmt on a plan containing a task with a trailing fenced code block succeeds and preserves the block byte-for-byte.
- [ ] T-000009: Duplicate phase ordinals evade `_check_structural_sanity` when heading forms are mixed: the duplicate-ordinal check (planfile/parser.py ~563-571) only matches bare-digit stage headings, but ledger-form phases (## Phase phase_001:) get a positional ordinal (parser.py ~298), so a stage-form and a ledger-form phase can both hold ordinal 1 uncaught, and `_resolve_positional_label` then silently returns the first -- a label like 1.1 mis-resolves. Extend the check to cover assigned positional ordinals. Regression test: mixed-form duplicate ordinals raise.
