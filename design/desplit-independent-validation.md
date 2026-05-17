# mcloop-desplit integration plan — independent adversarial validation

Reviewer: second, independent. Every claim below was re-derived from
current source. Citations are mine; original doc citations were not
trusted on input. Audience: CS PhD.

Object under validation: `bob/design/mcloop-desplit-integration-plan.md`
and the implemented stages it records (B0.1, B0.2/B0.3, B2, B3 harness,
B1 pre-flight).

Repos as inspected:
- bob-tools HEAD `68cbffb` (doc says `0767951`; one ahead, .mcloop gitignore commit, no behavior delta)
- mcloop HEAD `3cd8165` (matches doc)

---

## A. PASS/FAIL verdict table

Evidence column gives MY file:line (re-confirmed). Spot-citation discrepancies that
do not change the behavioral claim are noted as PASS (line-drift); only
behavioral mismatches are FAIL.

### A.1 §0 state verification

| Item | Verdict | Evidence |
|---|---|---|
| `clear_failed` exported from `bob_tools.planfile` | PASS | `bob_tools/planfile/__init__.py:39,74` |
| `validate_plan` exported from `bob_tools.planfile` | PASS | `__init__.py:47,87` |
| `clear_failed` implemented in operations.py | PASS | `bob_tools/planfile/operations.py:1058-1097` |
| `validate_plan` implemented at operations.py:202 | PASS | `operations.py:202` |
| Stage 6 fileio: fcntl.flock LOCK_EX on sidecar lock | PASS | `bob_tools/planfile/fileio.py:89-106` |
| Stage 6 fileio: atomic tempfile + fsync + os.replace | PASS | `fileio.py:109-135` |
| Stage 6 fileio: `update()` byte-compare raises `ConcurrentUpdateError` | PASS | `fileio.py:156-188` |
| bob-tools PLAN.md Stages 1–8 all `[x]`; Stage 9 DEFERRED no checkbox | PASS | grep on `bob-tools/PLAN.md` shows 0 `- [ ]`/`- [!]`, Stage 9 marker at `:293` |
| planfile editable-installed into mcloop venv | PASS | `__editable__.bob_tools-0.1.0.pth` exists; finder `MAPPING={'bob_tools':'/Users/mhcoen/proj/bob-tools/bob_tools'}` |
| `test_mcloop_parity.py` passes today | PASS | `3 passed in 0.81s` re-run by me |

### A.2 §2 parity audit (per-claim verdict matches doc)

| Claim | Doc verdict | My verdict | Evidence |
|---|---|---|---|
| (a) DFS / first-incomplete-phase / leaf-before-parent | MATCH | MATCH | `operations.py:591-666` mirrors `checklist.py:362-411`; `_phase_complete` (`operations.py:538-547`) mirrors `_stage_complete` (`checklist.py:302-318`) |
| (a) `@deps` vacuous on current plan | MATCH (vacuous) | MATCH (vacuous) | `rg "@deps" mcloop/PLAN.md` → 0; `_deps_satisfied` returns `all(())==True` (`operations.py:525`) |
| (a) subsection ordering — neutralized by canonical render | ACCEPTED-DOC | ACCEPTED-DOC | `_render_phase_into` orders phase-tasks-then-subsections; `next_tasks` walks `(phase.tasks, *sub.tasks)` (`operations.py:716`) |
| (b) failed-sibling root-skip / subtask-block | MATCH | MATCH | `operations.py:622-626` and `:657-665` byte-equivalent to `checklist.py:375-382` and `:402-407` |
| (c) BATCH child selection | MATCH | MATCH | `_get_batch_children` (`operations.py:550-575`) line-for-line equivalent to `get_batch_children` (`checklist.py:695-719`), modulo `TaskStatus` enum and `flag_tags`/`action_tag` for USER/AUTO break test |
| (c) BATCH return shape divergence | DIVERGENCE→shim | DIVERGENCE→shim | `next_tasks` surfaces parent via `dataclasses.replace(task, children=_get_batch_children(task))` (`operations.py:642`); shim `_planfile_compat.find_next` (`mcloop/_planfile_compat.py:233-251`) returns leaf and the BATCH parent shape is consumed by `run_loop`'s existing batch block |
| (d) USER classification on compat (no-ID) plans | MATCH | MATCH | both leading-anchored: `checklist.is_user_task` (`checklist.py:633-642`); `_planfile_compat.is_user_task` checks `"USER" in flag_tags` (`_planfile_compat.py:271-273`), and planfile parser strips leading ID before tag extraction (`parser.py:685-688`) |
| (d) AUTO/BATCH substring vs leading divergence | ACCEPTED-DOC | ACCEPTED-DOC | `checklist.is_batch_task` is `"[BATCH]" in task.text` (`checklist.py:692`); `is_auto_task` is `_AUTO_TAG_RE.search` (`checklist.py:729`); planfile flag/action extractors anchored (`parser.py:28,32,849-886`) |
| (d) prose-mention `[BATCH]` tasks all DONE in current PLAN.md | PASS | PASS | re-grep: `[BATCH]` mentions inside non-leading text appear only at `mcloop/PLAN.md:341,359,439`, all `- [x]` |
| (d) HIGH-SEVERITY: `migrate()` IDs break `checklist.is_user_task` | TRUE | TRUE | `checklist.is_user_task` checks `text == "[USER]"` / `startswith("[USER] ")` against raw post-checkbox text; after migrate prepends `T-NNNNNN:` the text starts with `T-…`, both predicates false. Parser path strips ID first (`parser.py:685`), so planfile is unaffected |
| (d) `_collect_body` USER capture also breaks under IDs | TRUE | TRUE | `checklist.parse` body-capture predicate identical to is_user_task (`checklist.py:227`) |
| (e) derived parent completion ≡ ledger_event_required=False | MATCH | MATCH | `complete_task` direct Settlement `ledger_event_required=True`; derived `kind="none"`, `ledger_event_required=False` (`operations.py:967-985`) — equivalent to mcloop's silent `_auto_check_parents` (`checklist.py:771-797`) |
| (e) `commit_landed` git-gating preserved by construction | MATCH | MATCH | `_git_head_sha` short-circuit at `ledger_emit.py:470-472` is in the caller (`emit_task_lifecycle_events`); planfile only produces a `Settlement` descriptor |
| (e) AUTO/USER currently no `_ledger_settle` call | TRUE | TRUE | `main.py:1202-1224` (AUTO branch) and `:1227-1271` (USER branch) call only `check_off`/`completed.append`/`ctx.add`/`notify`; no `_ledger_settle` |
| (e) commit-failure today: ledger settle but no `mark_failed` | TRUE | TRUE | `main.py:1660-1673`: `_ledger_settle(failure_kind="commit_failed")` then `break`; no `mark_failed` call |
| (e) retry-exhaustion today: `mark_failed` then `_ledger_settle` abandoned | TRUE | TRUE | `main.py:1769-1786` |
| (e) `complete_task`/`fail_task`/`reset_task` raise ValueError on missing ID | TRUE | TRUE | `operations.py:959-960`, `1009-1010`, `1042-1043` (doc cites :933-935/:983-985/:1016-1018 — line-drift only, ~26 lines off; raise-on-miss behavior verified) |
| (e) mutation requires migrated IDs (hard constraint) | TRUE | TRUE | `_find_task_by_id` matches by `task.task_id == task_id` only (`operations.py:107-122`); no ID → `None` → ValueError |
| (f) `mark_failed` semantics ([ ]/[x]→[!], no cascade) | MATCH | MATCH | mcloop: `checklist.py:583-588`; planfile: `fail_task` `cascade=False` (`operations.py:1012`) flips to FAILED unconditionally |
| (f) bulk clear: `planfile.clear_failed` exists, no-event, idempotent, recursive | PASS | PASS | `operations.py:1058-1097` + `_clear_failed_in_tasks` `:879-901` recurses, returns new Plan, no Settlement |
| (g) atomicity / locking — strictly safer + shim retry wrapper | PASS | PASS | `fileio.save`/`update` semantics as cited; `_planfile_compat._update_with_retry` is 3-attempt bounded (`_planfile_compat.py:32,366-381`) |
| (g) whole-file canonical-rewrite hazard real; rendered fixed point holds on migrated file | TRUE | TRUE | `render_plan(parse_plan(migrated)) == migrated` re-verified on scratch artifact; current `mcloop/PLAN.md` is 3-space-nested with no IDs/comments (`mcloop/PLAN.md:14-21` style); a first planfile save on it would reflow |
| (h) deletion-surface table accuracy | partly FAIL | see Findings | independent `rg "from mcloop\.(checklist\|plan_split)" mcloop tests` reproduces doc's 6 mcloop importers + 6 test importers, but **doc's test list omits `tests/test_args.py`** (real `from mcloop.checklist import Task` at `:13` plus 9 in-function `parse`/`Task` imports) |
| (h) `runner.py`/`run_summary.py`/`checks.py` not coupled | PASS | PASS | grep on those three files: zero `from mcloop.checklist` / `from mcloop.plan_split` |
| (h) `maintain.py` uses CHECKBOX_RE only for MAINTAIN.md | PASS | PASS | `maintain.py:13` import; usage at `:79` inside `parse_invariants` |
| Pre-cutover freeze invariant rg #1 (`@deps` in PLAN.md) | empty | empty | `rg "@deps" PLAN.md` returns no matches |
| Pre-cutover freeze invariant rg #2 (incomplete prose mention BATCH/AUTO/USER in PLAN.md) | empty | empty | re-run returns no matches |

### A.3 §3/§5 implemented-stage integrity

| Item | Verdict | Evidence |
|---|---|---|
| B0.1 `clear_failed` does what shim needs (bulk, idempotent, no ID) | PASS | re-read of `operations.py:1058-1097` |
| B0.2 `_planfile_compat.py` exposes claimed checklist-shaped surface | PASS | every name in doc's enumeration appears at the cited definitions in `mcloop/_planfile_compat.py` (109/122/146/233/254/271/276/281/286/299/317/323/394/400/411/433) |
| B0.2 shim is unimported by any mcloop runtime module | PASS | `rg "_planfile_compat" mcloop --glob '!_planfile_compat.py'` → 0; tests-only references in `tests/test_planfile_compat.py:11,261-265` |
| B0.2 BATCH return-shape normalization (§2c) | PASS | `find_next` returns leaf (`_planfile_compat.py:233-251`); never invokes planfile `next_tasks`'s parent-surfacing path |
| B0.2 ConcurrentUpdateError bounded retry (§2g) | PASS | `_update_with_retry` 3-attempt bound (`_planfile_compat.py:32,374-381`) |
| B0.2 retry-exhaustion-only `fail_task` boundary (§2e) | PASS | `mark_failed` is the only `fail_task` caller in the shim (`_planfile_compat.py:400-408`); docstring records the policy. Caller restriction is enforced at the `run_loop` site that will route to it — verifiable only post-B3 |
| B0.2 USER/AUTO/BATCH classified via flag_tags / action_tag (§2d) | PASS | `_planfile_compat.py:271-283` |
| B0.3 `tests/test_planfile_compat.py` covers operation-level parity + import proof | PASS | re-run `pytest -q tests/test_planfile_compat.py` → `11 passed in 0.90s` |
| B2 `resolve_phase_id` routes through `planfile.resolve_task_context` | PASS | `ledger_emit.py:122-171` rebuilt body imports `parse_plan`, `resolve_task_context` from `bob_tools.planfile` |
| B2 no-`ordinal_index` callers preserve `source="none"`/`phase_id=None` | PASS | `ledger_emit.py:153-171`: ordinal-path requires `ordinal_index is not None`; falls through to `("none", None)` otherwise |
| B2 `main._ledger_settle` still calls without `ordinal_index` | PASS | `main.py:903-906` — only `plan_path` + `task_label` passed |
| B2 exact-id substring hazard fixed via `_task_matches_label` | PASS | `operations.py:264-292` — first clause is exact-equality `task.task_id == ref`; substring forms only with required trailing separator (`:`/`)`/whitespace) |
| B2 test coverage as claimed | PASS | re-run `pytest -q tests/test_ledger_emit.py` → `27 passed in 1.07s`; explicit/comment/ordinal/no-id/T-000001-vs-T-0000010 cases all present |
| B3 harness hermetic: integration tests force direct backend + patch `_build_command` | PASS | `tests/integration/test_stub_run.py:90-95` patches `code_edit._select_backend → "direct"` and `runner._build_command`; other 4 test files patch `mcloop.main.run_task` outright |
| Autouse subprocess guard blocks unmocked `claude`/`codex` calls | PASS | `tests/conftest.py:30-53` |
| Five named B3 integration tests pass deterministically | PASS | re-run `MCLOOP_INTEGRATION=1 pytest -q tests/integration/{test_stub_run,test_minimal_run,test_subtask_ordering,test_failing_task,test_resume_after_kill}.py` → `21 passed in 2.24s` |
| B1 pre-flight: migrated file is a render fixed point | PASS | re-verified on `.scratch/mcloop-b1b3-preflight/PLAN.migrated.md` |
| B1 pre-flight: structural parity to legacy checklist on migrated copy | PASS | only deltas are 376 IDs, 10 phase_id comments, 286 indent changes, the 3 known DONE prose-mention BATCH lines |
| B1 pre-flight: transformation counts (376/10/0/286/-1) match | PASS | re-counted from current `PLAN.migration.diff`: `^+.*T-[0-9]{6}:` → 376; `^+.*phase_id:` → 10; `^+.*bob-plan-format` → 0; `+`/`-` totals 388/379 → net +9 = 10 + (-1) |
| B1 pre-flight: artifact SHA-256 / line count / path as doc records | **FAIL** | see Findings: doc cites `/tmp/...`, 878 lines, `d49204…`; actual is `/Users/mhcoen/proj/bob-tools/.scratch/mcloop-b1b3-preflight/`, 840 lines, `a3ceec…` |
| Decision D1: "drop work_observed" not yet code; preserved-by-current-behavior in fact | PASS (vacuous) | current `main.py` never invokes `complete_task`; AUTO/USER branches lack `_ledger_settle` — so today `work_observed` is not emitted regardless |
| Decision D2: ordinal collapse-to-none preserved | PASS | `ledger_emit.resolve_phase_id` returns `("none", None)` when `ordinal_index is None`, even if the planfile resolver synthesized an ordinal phase_id internally (`ledger_emit.py:153-171`) |
| §5 ordering: B2 genuinely independent of file identity (ID-bearing or not) | PASS | `planfile.parse_plan` handles compat-mode IDs-absent files (`parser.py:129-160` strict=False); `resolve_task_context` resolves by positional or text label too (`operations.py:457-489`) |

### A.4 Pre-cutover freeze invariant coverage

| Item | Verdict | Evidence |
|---|---|---|
| Invariant guards PLAN.md state | PASS | both rg commands cited in doc return 0 matches on `mcloop/PLAN.md` |
| Invariant covers all files parsed/scheduled by both libraries | **FAIL** | see Findings — BUGS.md is parsed by both, included in `next_tasks` via `plan.bugs.tasks`, but not in the rg path |

---

## B. CONSEQUENTIAL FINDINGS

### B-1. Freeze invariant under-specifies coverage (BUGS.md not scanned)

The §2 pre-cutover freeze invariant runs `rg` only against `PLAN.md`. But:

- `planfile.next_tasks` walks `plan.bugs.tasks` ahead of phase tasks
  (`operations.py:703-711`) with the same `_deps_satisfied` /
  `_walk_actionable` logic.
- The B5 wiring keeps `BUGS.md` as a second `parse_plan` input.
- mcloop's `checklist.parse` reads `BUGS.md` today the same way it
  reads PLAN.md (`main.py:981`, `1093`, `1807-1808`).

So an `@deps` line added to `BUGS.md` would trigger the §2(a)
"checklist ignores, planfile honors" divergence just as much as one in
PLAN.md. Similarly, an incomplete BUGS.md task containing a
non-leading `[BATCH]`/`[AUTO:…]` mention would re-introduce the §2(d)
divergence even though §2(d)'s "checked-only" guard holds on PLAN.md.

Current observable state is benign: current `BUGS.md` is 9 lines, 0
`@deps`, 0 `[BATCH]`/`[AUTO:]` mentions; the single `[USER]` substring
in line 4 is backticked prose that fails the post-Defect-C anchored
mcloop matcher *and* the planfile leading-tag matcher, so it is inert.
But the invariant as written does not prevent the next BUGS.md edit
from creating a divergence.

**Minimal correction:** the freeze-invariant rg commands must run
against `BUGS.md` as well as `PLAN.md`. Concretely:

```
rg -n "@deps" PLAN.md BUGS.md
rg -n "^[[:space:]]*- \[[ !]\].*\[(BATCH|AUTO[^\]]*|USER)\]" PLAN.md BUGS.md
```

(Doc text in §2 paragraph "Pre-cutover freeze invariants" should be
updated to call out BUGS.md by name, not just PLAN.md.)

### B-2. Deletion-surface inventory omits `tests/test_args.py`

Doc §2(h) lists 6 test files in the deletion/migration surface
(`test_plan_split.py`, `test_checklist.py`,
`test_checklist_integration.py`, `test_subtask_ordering.py`,
`test_output.py`, `test_lifecycle.py`). My independent
`rg "from mcloop\.(checklist|plan_split)" tests` returns those six **plus**
`tests/test_args.py`:

- `tests/test_args.py:13` — `from mcloop.checklist import Task`
- 9 additional in-function imports of `parse as parse_checklist` /
  `parse as cl_parse` / `Task` at `:4309, :4340, :5980, :6264, :6276,
  :6600, :9893, :9942, :9988`

`test_args.py` is therefore a real test-side coupling that must be
retargeted (to planfile types/operations or to the `_planfile_compat`
shim) before `mcloop.checklist` can be reduced or deleted at Stage D1.
Not a cutover blocker (B3 cutover is unblocked by this; the test only
needs to keep passing against the *unchanged* `mcloop.checklist`
during the cutover), but a real D1 deletion-prerequisite the doc
currently does not name.

**Minimal correction:** add `tests/test_args.py` to the §2(h) test
deletion/migration surface; flag its 10 import sites for retargeting
in Stage D1.

### B-3. B1 pre-flight artifact fingerprint is stale

§ "B1+B3 pre-flight result" records:

- path `/tmp/mcloop-b1b3-preflight/`
- diff line count 878
- SHA-256 `d4920465e2bba875bdc9b2fe874196875ad1b83c92f8f1f466caa9cafff8c5a7`

Current on-disk state:

- path `/Users/mhcoen/proj/bob-tools/.scratch/mcloop-b1b3-preflight/`
  (the policy-correct location; `/tmp` is forbidden by the workspace
  scratch policy and the directory at `/tmp/mcloop-b1b3-preflight/`
  does not exist)
- `PLAN.migration.diff` is 840 lines
- SHA-256 `a3ceecf4a05c58c62b0a7c5c434a4c05df06e1efa1e72d484d1bb253e3860fd6`

The transformation counts I independently re-derived
(376 task IDs, 10 phase_id comments, 0 magic, 286 indent changes,
blank-line net -1; total `+/-` 388/379, net +9) still match the
counts the doc cites. So the *canonicalization shape* the doc
describes is unchanged; only the recorded fingerprint and length are
stale. Likely cause: the artifact was regenerated against a slightly
later `mcloop/PLAN.md` after the doc text was last touched.

Practical impact on D4: the human reviewing the diff cannot use the
doc's SHA as a sanity check. The cutover commit must regenerate the
diff against current `mcloop/PLAN.md` immediately before review, and
the doc fingerprint must be refreshed (or replaced with an explicit
"regenerate-just-before-review" instruction).

**Minimal correction:** strike the stale path/length/SHA from the
"B1+B3 pre-flight result" subsection (or replace them with a one-line
"regenerate immediately before the cutover commit; counts must equal
376/10/0/286/-1, fixed-point and structural parity must hold"). Keep
the transformation-shape and parity claims, which are correct.

### B-4. None found in the linchpin §2(d)/(e)/(g) claims themselves

Every behaviorally load-bearing claim in the audit — the
`migrate()`-breaks-`is_user_task` linchpin, the `_collect_body` USER
body-capture breakage under IDs, the `complete_task` ValueError on
missing IDs, the `_get_batch_children` equivalence, the
failed-sibling root-skip/subtask-block asymmetry, the
`commit_landed` git-sha gate sitting in the caller, the
canonical-rewrite hazard on first save, the rendered fixed-point on
the migrated file — was re-derived from current source and holds. The
B0.1, B0.2/B0.3, B2, and B3-harness work passes my re-run gate
(parity 3/3, planfile-compat 11/11, ledger_emit 27/27, hermetic
integration 21/21). The substantive cutover analysis is sound.

---

## C. DESIGN-LEVEL DECISIONS FOR THE HUMAN

Three live forks. Each is the call the doc already names; my purpose
here is to state the decision crisply and the tradeoff, not to ask for
a re-audit.

### C-D1. `work_observed` emission for AUTO/USER on the new settle hook

Today mcloop emits no ledger event when AUTO or USER tasks succeed
(no `_ledger_settle` call in those branches). Planfile's
`complete_task` returns Settlements with `kind="work_observed"` for
both. The new settle hook can either drop those or pass them through
to `emit_task_lifecycle_events`.

- **Drop (recommended; doc default).** Behavior-preserving cutover.
  Audit stream unchanged. `evaluate_and_maybe_pause`,
  `ledger_pause`, reauthor/HardStop behavior bit-identical to today.
  Defer the AUTO/USER → `work_observed` adoption to opt-in Stage B6.
- **Emit.** Aligns with the doc's `kind` policy and the §5 design
  target. Starts writing `work_observed` into the audit stream
  immediately at cutover. Can change threshold evaluation outcomes;
  needs a fresh pause/threshold evaluation against the new event
  shape; mixes the parser swap with a meaningful audit-stream change,
  which is exactly the "looks complete, isn't" failure mode the whole
  effort exists to avoid.

**Decision:** **Drop.** Preserve the audit stream during the cutover;
adopt at B6 with its own test asserting the new event stream against
an explicitly-updated baseline.

### C-D2. Ordinal phase-id attribution via `resolve_task_context`

The new planfile resolver synthesizes an ordinal-derived phase_id
internally whenever a phase has no explicit `<!-- phase_id: … -->`
comment or `## Phase phase_NNN:` header. The B2 ledger shim collapses
that to `("none", None)` when the caller does not pass
`ordinal_index`. `main._ledger_settle` does not pass it
(`main.py:903-906`).

- **Collapse to none (recommended; doc default).** Behavior-preserving;
  preserves today's "pre-migration plans with no explicit phase ids
  emit no `finding_observed` fallback" emission profile. The
  `ordinal_index` parameter remains the opt-in switch for the degraded
  path.
- **Pass ordinal_index from `main`.** Starts emitting
  `finding_observed` fallback events for every settle on a no-explicit-id
  plan immediately at cutover. Same audit-stream-change concern as D1.

**Decision:** **Collapse.** Keep the ordinal opt-in deferred to B6.

### C-D4. Cutover go/no-go (the B1+B3 atomic commit)

The behavior-preservation question is answered: parity tests, shim
parity tests, ledger tests, and the hermetic-harness integration
suite all pass against current source; the migrated PLAN.md is a
render fixed point; structural parity vs `checklist.parse(original)`
holds with only the four documented additive deltas.

The remaining live judgment is whether the user accepts the *one*
genuinely consequential, irreversible artifact: the unified diff that
rewrites human-edited `mcloop/PLAN.md` to canonical form (3→2-space
indent, `T-NNNNNN:` IDs, phase_id comments, blank-line
normalization). Shape: 376 task IDs, 10 phase_id comments, 286
indent-only changes, blank-line net −1, no magic line. No
phase-tasks-before-subsections reordering on the current file (none
was needed).

**Caveat (B-3 above):** the doc's recorded artifact SHA / line count /
path are stale. Before review, regenerate the diff against current
`mcloop/PLAN.md` and present the fresh SHA. Counts must still equal
376/10/0/286/−1; the render fixed-point and structural parity must
still hold; if any of those numbers changes, the canonicalization
shape itself has drifted and the cutover should not proceed without
re-audit.

**Decision shape:** the cutover is GO from a behavior-preservation
standpoint. The only remaining decision is the human-review approval
of the freshly-regenerated B1 diff, per D4.

---

## D. Overall verdict

**GO**, conditional on three small corrections to the design document
*before* the cutover commit lands:

1. (B-1) Extend the §2 freeze-invariant rg commands to also scan
   `BUGS.md`. Inert today; will not be inert under arbitrary future
   BUGS.md edits.
2. (B-2) Add `tests/test_args.py` to the §2(h) test
   deletion/migration surface. Not a B1+B3 blocker; is a D1
   deletion-prerequisite.
3. (B-3) Refresh the B1 pre-flight fingerprint (or strike the stale
   SHA/length/path and instruct "regenerate immediately before
   cutover"). The shape claims are correct; the fingerprint is not.

The B1+B3 cutover itself is internally consistent, supported by
current source, and adequately covered by passing tests. The atomic
B1+B3 ordering is forced (not arbitrary) by the §2(d)/(e) hard
constraints, both of which I re-confirmed from current source. D1 and
D2 defaults preserve current behavior; D4 needs a freshly-regenerated
diff in the user's hands.
