# Bob reliability and utility improvement plan

Status: in progress, 2026-09-05. Slice A passed all five hosted CI jobs on
`d4df6eee`. Slice B's first boundary covers persistence ownership, shared JSON
updates, product identity recovery, and atomic run summaries. Main Duplo state
and processing manifests are now migrated as the second boundary. Cross-system
reconciliation remains pending.

This records the repository review and turns its recommendations into bounded
deliverables. It is a design document, not an executable McLoop queue. Promote
ready slices into the appropriate canonical `PLAN.md` through the planfile API,
preserving existing task IDs and history. Cross-package slices belong in the
[workspace plan](../PLAN.md); package-local slices belong in their package plans.

## Intended outcome

A new user can install Bob, adopt a small project, complete one independently
verified change, understand the evidence, and recover from an interruption without
hand-editing state. Subsequent improvements should reduce operator intervention
and escaped defects, with recorded evidence of the tradeoffs in time and usage.

Preserve deterministic scheduling, replaceable model actors, structured plans,
and local inspectable artifacts. Build on existing verification, ledger, replay,
and configuration facilities. Broader unattended self-improvement depends on these
foundations becoming demonstrably reliable.

## Evidence and existing work

The review ran 107 focused tests successfully: ledger storage, canonical plan
saves, Orchestra replay and deterministic execution, Duplo acceptance annotations,
and McLoop coverage verification and pytest signals. This is a targeted baseline,
not a full-suite result or proof of end-to-end reliability.

Two wheel builds established concrete packaging omissions:

- Duplo's wheel omits all eight Python modules under `duplo/platforms/`.
  Its [package configuration](../packages/duplo/pyproject.toml) explicitly lists
  only the top-level package.
- Orchestra's wheel omits the JSON schemas referenced by its bundled workflows.
  Its [package-data configuration](../packages/orchestra/pyproject.toml) includes
  workflows and prompt templates but not those schemas.

Other observed starting points:

- [Duplo persistence](../packages/duplo/duplo/saver.py) treats invalid JSON as an
  empty object and writes updates directly to the destination file.
- [Run summaries](../packages/mcloop/mcloop/run_summary.py) also use direct writes;
  authoritative state and diagnostic artifacts need different recovery contracts.
- [The Bob CLI](../packages/bob-tools/bob_tools/bob_cli.py) currently exposes hook
  installation only. McLoop and Duplo already have useful separate CLI surfaces.
- Requirements are largely name-based, while plan tasks have stable identifiers.
- McLoop's main module is about 4,000 lines, and McLoop and Duplo have reciprocal
  package dependencies. Refactoring should follow concrete lifecycle contracts.
- No tracked GitHub Actions workflows were found during the review.

The [existing roadmap](bob-roadmap.md), [recursive improvement design](recursive-improvement.md),
and [backlog](BACKLOG.md) already discuss diagnosis, recovery, intervention metrics,
and an external executable specification. Reconcile those items before adding
tasks. Iterative design and its Duplo wiring are already recorded as completed in
the workspace plan; this proposal does not schedule their reimplementation.

## Delivery order

| Slice | Deliverable | Dependency | Primary location |
| --- | --- | --- | --- |
| A | Reproducible installation and release checks | None | Workspace and package metadata |
| B | Explicit persistence and recovery contracts | None; use A's checks when available | bob-tools, Duplo, McLoop |
| C | One diagnosable, independently verified example | A and B's relevant state paths | Workspace examples and docs |
| D | Operator CLI and bounded execution | C; reuse existing summaries | Bob CLI, McLoop, Orchestra |
| E | Requirement identity and behavioral evidence | C's acceptance contract | bob-tools, Duplo, McLoop |
| F | Workflow evaluation and justified presets | C; use D's usage records | Orchestra and evaluation fixtures |
| G | Existing-repository adoption and wider stack support | A, C, D; integrate E as available | Duplo and McLoop |

Documentation changes accompany each slice. Execution-core refactoring accompanies
the relevant lifecycle work; it is not a prerequisite rewrite. Dependency order
does not require parallel agent execution.

## Slice A: installable artifacts — begin here

Smallest useful result: the packaged software includes what its runtime needs,
and that property is checked outside an editable checkout.

1. Add a wheel smoke harness using a temporary environment and a working directory
   outside the source tree. Ensure no editable imports or source-path overrides
   can make the test pass. Separate dependency provisioning from smoke execution;
   the smoke itself uses mock actors and temporary configuration, with no model
   calls or writes to the operator's home directory.
2. Reproduce the two observed omissions as failures: importing Duplo's platform
   modules and resolving schemas from packaged Orchestra workflows.
3. Correct package discovery and resource inclusion. Build all four workspace
   wheels and inventory runtime resources, including hook-installation assets and
   templates. The latter inventory is an investigation, not a claim that every
   asset is currently broken.
4. Exercise every installed CLI's help, resolve platform profiles, validate bundled
   workflow assets, and execute a representative mock-backed workflow from the
   installed artifacts. Verify imports originate in the temporary installation.
5. Verify and document installation plus CLI invocation from a separate target
   project. Distinguish environment creation from putting commands on shell PATH.
6. Add repository CI for the wheel smoke, existing package quality gates, and a
   representative cross-package smoke. Use declared supported Python versions;
   keep live-provider tests separately invoked. Respect each package's lint rules.

Acceptance: the smoke fails against the original omissions and passes with the
fixes; all four artifacts install together; bundled resources resolve without the
checkout; the documented setup reaches the CLI from a new project directory.
Record commands and results. Do not claim standalone McLoop/Duplo installation
until their dependency and distribution contracts actually support it.

First implementation boundary: deliver steps 1–4 as one focused change. Follow
with installation documentation and CI once the local harness is reproducible.

## Slice B: durable state and honest recovery

1. Inventory persistent files and their owners: plans, ledger events, Duplo state,
   Orchestra artifacts, task baselines, process markers, and run summaries.
   Classify each as authoritative, reconstructible, or diagnostic. Document what
   survives a clone, process interruption, and host failure separately.
2. Define a shared atomic JSON update primitive where appropriate: validation,
   temporary file in the destination directory, replacement, permissions, and a
   stated durability guarantee. Reuse existing sound persistence utilities rather
   than replacing ledger storage or Orchestra replay wholesale.
3. Define concurrency behavior for read-modify-write operations. Atomic replacement
   alone does not prevent lost updates; use locking or revision checks where
   multiple writers are supported, and reject unsupported concurrent ownership.
4. Migrate Duplo's authoritative JSON paths first. Distinguish missing, malformed,
   and unsupported-version state. Preserve corrupt bytes and report recovery
   options; never silently initialize over corrupted existing state. Add schema
   versioning and a migration path for current files.
5. Make run-summary publication atomic, with collision-resistant run identity.
   A missing or damaged summary must not imply that a task did or did not commit.
6. Inject failures around persistence boundaries and between edit verification,
   git commit, ledger append, and task advancement. Record which component owns
   reconciliation when only part of a transition is durable.

Acceptance: interrupted updates leave either the previous valid state or the new
valid state; corrupt input is reported without overwrite; supported concurrent
updates do not silently disappear. Recovery never silently reruns an ambiguous
mutating action. A defined recovery procedure handles each tested boundary or
stops with preserved evidence and an explicit operator action.

## Slice C: one complete, independently checked example

1. Add a small Python CLI fixture with a fixed behavioral requirement, existing
   regression tests, and an intentional defect. Keep its acceptance oracle under
   harness control, outside the candidate editor's writable test set.
2. Exercise plan creation/loading, one implementation attempt, verification,
   commit, evidence recording, interruption, and resume. Use deterministic mock
   actors for the repeatable test and an explicitly invoked real-model variant.
3. Include adversarial candidates: a plausible wrong implementation, a weakened
   in-project assertion, a no-op, and an unrelated change that passes existing
   tests. Require the relevant acceptance contract to reject each invalid result.
4. Emit an acceptance record identifying requirement, task, baseline and candidate
   revisions, oracle version/digest, executed checks, results, and evidence paths.
   Define failures and unavailable evidence explicitly.
5. Write a short walkthrough showing setup, success, failure explanation, and
   recovery. Report exactly what was verified. Mock-backed success establishes
   orchestration correctness; it does not measure model competence.

Acceptance: a correct candidate passes; deliberately incorrect candidates fail;
an interrupted run reaches a defined outcome without duplicate mutation. A reader
can trace a completed requirement to the exact candidate and checking evidence.

## Slice D: an operator interface with bounded work

Start with proposed `bob doctor`, `bob status`, and `bob explain <task-id>` commands.
Add coordinating `init`, `plan`, and `run` commands only after their package-level
semantics are clear. Reuse the current CLIs and configuration precedence. Resolve
the umbrella CLI's dependency boundary explicitly so shared infrastructure does
not acquire eager imports of the entire toolchain.

The default doctor is read-only and makes no paid model calls. It reports project
identity, effective configuration and its source, tool availability, state health,
and check configuration. Explicit probes can test authentication or run checks.
Status distinguishes running, waiting, blocked, failed, and complete using durable
records plus liveness checks; stale PID files alone are insufficient evidence.
Explain links a stop reason to the relevant check, transcript, and next action.

Introduce a versioned failure record, reusing the existing roadmap's diagnosis
design after checking its implementation status. Add run-wide time and invocation
budgets spanning nested workflows, retries, and audits. Preserve consumed budgets
across resume. Record usage where available; unknown token/cost values remain
unknown. Enforce a monetary ceiling only where accounting supports that promise.

Acceptance: the example's failure can be understood without searching raw logs;
text and JSON outputs agree; budgets stop new work predictably and leave resumable
state. Existing rate-limit, retry, and watchdog behavior remains covered.

## Slice E: stable requirements and behavioral evidence

Design a versioned requirement record before changing the plan grammar. Include
stable identity, revision, source evidence, whether behavior is observed/requested/
inferred, explicit assumptions, and acceptance references. Decide how these records
are stored and projected using existing plan/ledger facilities.

Migrate name-based feature links while retaining readable labels and legacy input
support. Renaming must not lose completion history. A semantic deduplication model
may propose a merge, but a merge must preserve lineage and cannot independently
establish behavioral completion. A changed requirement invalidates or marks stale
the evidence whose applicability it changes.

Expose separate implemented, exercised, and behavior-verified states. Require
behavioral completion to identify both a requirement revision and acceptance
evidence. Record oracle changes separately from ordinary implementation edits.
Apply mutation checks selectively to high-value contracts where they establish
that assertions reject incorrect behavior.

Acceptance: rename, merge, split, and revision fixtures preserve traceability;
queries identify uncovered requirements and stale evidence; historical tasks
remain readable. No model's declaration alone upgrades a requirement to verified.

## Slice F: evidence for workflow selection

Build an initial corpus from resolved defects and representative design tasks,
with pinned starting revisions and independent expected outcomes. Separate cases
used to tune prompts from held-out cases used to evaluate them. Compare a single
actor, independent critique, iterative review, and a council on matched inputs.

Record model and CLI versions, prompt/workflow digests, termination, attempts,
independently accepted outcomes, operator interventions, elapsed time, and available
usage. Use repeated trials and report variation and unknown accounting. Control
execution conditions and disclose differences in time or invocation allowances.
Set the live-evaluation budget before running it.

Acceptance: one reproducible report supports or rejects a proposed workflow preset
on the measured task class. An inconclusive result is valid. No universal claim
about councils or self-improvement follows from a single successful run.

## Slice G: adopt existing projects and support additional stacks

Add an adoption flow that inventories an existing repository, identifies its build
and test entry points, records baseline failures, and drafts one bounded change.
Show the proposed plan before execution and preserve unrelated operator edits.
Reuse McLoop's existing investigation/worktree support where its contract fits;
do not imply that isolated execution is already universal.

Start with a platform-neutral Python service profile, then one web stack selected
from an actual target project. Each profile declares reproducible build, test,
packaging, and behavioral verification commands. Unsupported stacks receive a
clear capability report and explicit configuration path.

Acceptance: a documented existing-project example produces one verified change
with evidence, distinguishes pre-existing failures from regressions, and resumes
after interruption. Additional profiles must meet the same lifecycle contract.

## Execution-core and documentation discipline

Extract lifecycle logic as a slice requires it: typed attempt outcomes, shared
single/batch verification rules, and explicit retry/rollback/commit transitions.
Characterize current behavior before moving it. Break the Duplo/McLoop dependency
cycle through a narrow reauthoring interface when that boundary is understood.
Do not make a large rewrite a condition of fixing the observed defects.

Update the relevant package documentation and manifests with each implementation.
Make the root README lead with the verified example, supported setup, exact
guarantees, and limitations; retain the longer rationale as linked material.
Mark historical designs and reconcile completed work in the older roadmap.

## Completion evidence and next action

For each delivered slice, record the implementation revision, exact validation
commands, resulting artifacts, known limitations, and the canonical task IDs that
implemented it. Expand later slices into bounded implementation designs when
their dependencies are met and the earlier results can inform their scope.

### Delivered: Slice A steps 1–4 (2026-09-05)

Workspace tasks `T-000027`, `T-000028`, and `T-000029` implement the first boundary.
Duplo now discovers its platform subpackages, and Orchestra includes workflow
schemas in its package data. The [wheel smoke](../scripts/README.md) builds fresh
source copies, inventories resources, installs all four wheels, verifies installed
import origins and five CLI launchers, resolves platform profiles and 16 bundled
workflows, and executes a mock workflow with durable output. Runtime probes forbid
external processes and network connections.

Local validation before commit `7b3592da`:

- Original metadata: smoke failed for eight omitted Duplo platform modules and
  seven omitted Orchestra schemas; the installed Duplo launcher also failed with
  `ModuleNotFoundError: No module named 'duplo.platforms'`.
- Updated metadata: `python3 scripts/smoke_wheels.py --offline --work-dir
  /private/tmp/bob-wheel-final` passed. The local artifact directory contains
  wheel files, step logs, `installed-requirements.txt`, and `result.json`.
- `.venv/bin/python -m pytest -n 4 -q`: 6,903 passed, 125 skipped, nine warnings
  from the existing schema-smoke cycle fixture.
- `.venv/bin/ruff check .`: passed.

The asset inventory also identified checkout-dependent hook/permission installers
and optional command helpers; see the smoke documentation for exact paths and
limitations. These were inventoried, not repaired in this boundary. The smoke
does not establish wheel-only support for those optional features, run live model
tests, or exercise native screenshots. CI is not wired yet.

### Delivered: installer resources and CI configuration (2026-09-05)

Workspace tasks `T-000030`, `T-000031`, and `T-000032` cover this continuation.
The shared hooks and settings example now live in `mcloop/resources/`; original
checkout paths are compatibility symlinks to the unchanged source bytes. Both
installers find the packaged resources. Bob's lookup does not import McLoop's
eager module graph, and an explicit hook remains usable without McLoop installed.
Wheels also install the token-audit script and portable Swift helper source beside
appshot, without including a host-built native binary.

The smoke now checks temporary-home installation, idempotence, stale-hook refresh,
preservation of unrelated configuration, recommended-permissions output, and the
installed audit script. The current settings example has no permissions block;
the installer preserves its existing empty allow-list behavior.

Local validation before commit `7b3592da`:

- Before the fix, the installed `bob install` failed looking under a nonexistent
  `venv/lib/packages/mcloop/` checkout path.
- `python3 scripts/smoke_wheels.py --offline --work-dir
  /private/tmp/bob-install-final-2`: passed with installer and helper checks.
- `.venv/bin/python -m pytest -n 4 -q`: 6,904 passed, 125 skipped, nine existing
  schema-smoke cycle warnings. The focused installer/hook tests also passed (527).
- `.venv/bin/ruff check .`, targeted type checking of `bob_cli.py`, shell syntax
  checking of appshot, and actionlint on the new workflow: passed.
- `uv sync --locked --all-packages --all-extras` succeeded in a fresh temporary
  workspace copy. Activating its environment made `bob` and `duplo` available from
  a separate project directory, where their help commands succeeded.

[The CI workflow](../.github/workflows/packaging.yml) tests wheels on Linux/macOS
and Python 3.12/3.13, and runs the workspace lint/test suite on macOS/Python 3.13.
It uses pinned action revisions and saves packaging evidence on failure or success.
Hosted validation subsequently passed on `d4df6eee`:
[GitHub Actions run 33952437743](https://github.com/mhcoen/bob/actions/runs/33952437743).
All four wheel jobs and the workspace job passed (6,894 tests passed, 136 skipped,
nine existing schema-smoke warnings). Follow-up commits tracked the uv lockfile,
installed ffmpeg, activated the test environment, isolated mocked video tests,
and replaced the removed ffmpeg `-vsync` option in both extraction paths.
Native window capture and live provider setup remain outside the smoke's scope.

### Slice B first boundary (2026-09-05)

Workspace tasks `T-000033`–`T-000035` cover this boundary. The
[persistence inventory and recovery contract](persistence.md) classifies state
ownership and distinguishes process interruption, host failure, and clone recovery.
Shared `bob_tools.json_state` reuses planfile's existing synchronized replacement
and locking utilities, adding JSON validation and permission preservation.

Duplo product identity is the first authoritative consumer: missing state remains
distinct from malformed state; legacy objects migrate on update to version 1;
unknown fields are retained; schema and corruption errors preserve original bytes.
The read-modify-write operation is serialized across cooperating writers.
McLoop summaries now use UUID identities and atomic per-file publication; failure
to replace latest does not discard the archived summary. Summary IDs remain
separate from the ledger's existing run IDs.

Regression reproduction against `d4df6eee`: `save_product` silently overwrote a
truncated JSON file. The new implementation refused the same operation and kept
the original bytes. Tests exercise serialization, validation, synchronization,
replacement, process death, four concurrent writers, same-second summary
collisions, and failure between archive and latest publication.

Local validation:

- Activated workspace: `python -m pytest -n 4 -q` passed with 6,947 passed,
  125 skipped, and nine existing schema-smoke warnings.
- 42 persistence regressions passed; the first broader focused run also passed
  295 existing and new state, planfile, saver, and summary tests.
- `.venv/bin/ruff check .` passed. Targeted strict mypy passed for shared JSON
  state and run summaries; the full suite also ran Duplo's package type check.
- `python scripts/smoke_wheels.py --offline --work-dir
  /private/tmp/bob-persistence-wheels-2` passed, including runtime resource
  inventory, fresh installation, all five CLI launchers, and runtime probes.
  The first sandboxed invocation could not read uv's cache; the successful
  offline run used approved cache access. Artifacts remain at the printed path.

Hosted evidence for the first boundary: commit `4545b588`,
[run 33953132692](https://github.com/mhcoen/bob/actions/runs/33953132692), all five
jobs passed; 6,936 workspace tests passed, 136 skipped.

### Slice B second boundary: Duplo state and checkpoints (2026-09-05)

Workspace tasks `T-000036`–`T-000038` cover this boundary. Main state readers
now share `duplo.state.read_state()`, including the pipeline, investigator,
verification loader, and saver. Missing state remains distinct from invalid
JSON, unsupported versions, and malformed known field shapes. Legacy objects
migrate on update while unknown fields and unrelated history remain intact.

Short writes use locked atomic transactions. Feature merging and preference
extraction compute outside the lock, then reject mismatched input snapshots
without repeating provider calls. Scrape timestamps no longer rewrite an old
copy of the full state. File and video manifests migrate to versioned envelopes;
video merges serialize and file checkpoint publication checks the observed map.

Validation:

- `source .venv/bin/activate` followed by `python -m pytest -n 4 -q`: 6,996
  passed, 125 skipped, nine existing schema-smoke warnings.
- The final `test_state.py` module passed 50 cases, including a scrape-concurrency
  regression added after the full run. It exercises 13 public writers against
  corrupt input, legacy migration, stale model results, interrupted publication,
  parallel feedback/video updates, and checkpoint conflicts.
- Ruff and strict mypy on the shared JSON utility passed; the full suite also
  ran Duplo's package type check.
- `python scripts/smoke_wheels.py --offline --work-dir
  /private/tmp/bob-duplo-state-wheels` passed from installed artifacts outside
  the checkout. The retained directory contains wheels and probe logs.

Next boundary: inject and reconcile failures between verification, Git commit,
ledger append, and task advancement. Define pipeline ownership and recovery for
multi-file operations such as example-directory replacement and reference moves.
A conflict currently stops safely at the individual JSON publication boundary;
it does not roll back earlier external effects. Slice B remains in progress.
