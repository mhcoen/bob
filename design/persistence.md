# Persistence ownership and recovery

Updated 2026-09-05 for improvement-plan Slice B. Product identity, main Duplo
state, processing manifests, and run-summary persistence are migrated. Recovery
across verified commit transitions now stops at an explicit reconciliation gate.
Execution receipts now cover the bare loop and audit/maintain/investigate CLI
commands. Duplo directory operations stage output and retain originals with
explicit reconciliation. Broader cross-tool transactions remain unsupported.

## Inventory

| State and owner | Role | Existing publication / recovery contract |
| --- | --- | --- |
| `PLAN.md`, `BUGS.md` through `bob_tools.planfile` | Authoritative scheduling intent and task status | Canonical validation, sidecar lock, file fsync, replacement, directory fsync. `update` also detects an external change before lock acquisition. Use the API; reconcile Git and ledger evidence before changing task completion. |
| `PLAN.events.jsonl` and `.writers/*.seq` through `bob_tools.ledger.storage` (configured ledger directory, including `.duplo/ledger`) | Authoritative event evidence and sequence allocation | Validated append under advisory lock, file/directory synchronization; corrupt sequence state is refused. Preserve event and writer state together. Ledger projections are reconstructible; do not discard events or invent a replacement sequence. |
| `.duplo/product.json` through `saver.save_product` / `derive_app_name` | Authoritative confirmed identity and user-edited names | **Migrated:** versioned validation, locked read-modify-write, atomic replacement. Reads do not mutate. Corruption or unknown versions stop the operation. |
| `.duplo/duplo.json` through `saver` and other Duplo consumers | Authoritative selections, status, phase history, preferences, feedback, issues | **Migrated:** all runtime JSON readers use schema validation; short mutations hold a sidecar lock through atomic publication. Provider-backed feature merges and preference extraction compare a pre-call snapshot before saving. Unknown fields and unrelated history are preserved. |
| `.duplo/file_hashes.json`, `processed_videos.json` | Processing checkpoints | **Migrated:** flat legacy maps remain readable; updates write versioned envelopes with entries. Video records merge under a lock; the pipeline checks its original file checkpoint before replacement. Losing checkpoints may repeat processing and provider calls; preserve them with Duplo state. |
| `.duplo/references`, `examples`, `.duplo/operations/*` through `file_ops` | Retained inputs, generated examples, operation evidence | **Migrated:** stage new content, retain original sources and prior destinations/generations, publish a receipt before destructive steps. Pending operations stop supported pipelines and example readers; `duplo recover` reports evidence and records explicit reconciliation without replay. |
| `.duplo/raw_pages`, `site_media`, frame descriptions | Retained/derived evidence | Preserve alongside source and operation archives. Derived content can require paid calls to rebuild. Individual operation receipts do not cover every artifact of an entire pipeline. |
| Orchestra run directory (`store.sqlite`, `log.jsonl`, `visibility.json`, prompt snapshots) | Artifact versions and execution evidence | SQLite WAL with FULL synchronization; log append fsync and tail-recovery rules; visibility gates successful invocations. Default CLI root is `~/.orchestra/runs`, configurable. Preserve the entire run directory using a consistent SQLite backup or after stopping writers; copying only the database while live may omit WAL data. Existing resume owns replay; no new replay guarantee here. |
| `.mcloop/completions/<uuid>.json` through `completion` | Run execution and verified commit-transition evidence | **Migrated:** schema 2 run receipts before first mutation; schema 1 completion receipts before verified Git commits. Both kinds block supported CLI startup when unresolved. Explicit acknowledgement retains evidence and resolution; no automatic replay. |
| `.mcloop/completions/owner.lock` | Execution ownership | Nonblocking POSIX advisory lock held for the bare loop, audit/maintain/investigate CLI commands, or acknowledgement. Released by process death; never unlink a live lock. Other utilities, direct library calls, and external writers are not coordinated. |
| `.mcloop/task-baseline` through `git_ops` | Verification checkpoint | Best-effort SHA write. Missing/unreadable baseline is unavailable evidence, not an empty diff. A current Git HEAD cannot automatically reconstruct the pre-edit baseline. |
| `.mcloop/active-pid`, `interrupted.json` through `lifecycle` | Process hints and interruption evidence | PID cleanup checks command identity before killing a process. PID reuse or absence does not establish task outcome. Interrupted JSON still uses direct writes; inspect Git, logs, and plans. |
| `.mcloop/runs/*_run-summary.json`, `latest.json` through `run_summary` | Diagnostic summaries | **Migrated:** UUID identity and atomic publication per file. Dated record first, latest second. Latest is last-published, not a transaction or proof of commit. |
| Duplo call logs, McLoop logs, diagnostics | Diagnostic provenance | Preserve for investigation, including provider outcomes. They do not independently establish successful task advancement. No blanket atomicity claim. |

## Clone, process interruption, and host failure

A clone restores only committed files, usually plans, source, and documentation.
This repository ignores `.duplo/` and `.mcloop/`; default Orchestra runs live in
the operator's home. Neither Git nor the wheel artifacts back these up. Preserve
state separately with appropriate protection for prompts, URLs, and user content.

Atomic JSON publication serializes before creating the temporary file, writes a
sibling file, applies the old permission bits (0600 for new files), fsyncs it,
replaces the destination, then fsyncs its parent directory. Before replacement,
a failure leaves the old complete state. After replacement, readers see the new
complete state. Ordinary exceptions and interrupts clean up temporary files;
SIGKILL may leave an unreferenced `*.tmp` file that later updates ignore. Inspect
and remove such files only after stopping writers. Never remove a live `.lock`
sidecar: doing so could allow two locks on different inodes.

The guarantee covers process interruption on local POSIX filesystems. It is not
a multi-file transaction or a universal power-loss guarantee. Directory fsync
`EINVAL` is tolerated by the reused utility on filesystems that do not support it;
other errors propagate. Newly created ancestor directories are not recursively
synced. There is no macOS F_FULLFSYNC guarantee, replicated-storage guarantee, or
backup guarantee. Existing permissions are retained, but owner, ACLs, extended
attributes, and hardlink identity are not preserved by replacement.

Cooperating JSON writers acquire `<resolved-path>.lock` before reading and hold
it through publication. This serializes processes and threads opening the same
local path. External editors, older Duplo versions, and noncooperating writers
remain unsupported concurrent owners. Callbacks cannot nest same-file updates.
Atomic snapshot publication intentionally has last-writer-wins behavior and
must not be substituted for the locked update API.

## Product identity migration and recovery

Legacy objects with no `schema_version` (or explicit 0) remain readable without
changing bytes. The next successful identity update writes version 1 and retains
unknown fields. Known identity fields (`product_name`, `source_url`, `app_name`)
must be strings when present. JSON must be a UTF-8 object with unique keys;
non-standard NaN/Infinity constants are refused. Unsupported versions, malformed
fields, and malformed JSON raise `StateError` before the update callback runs.

On error, stop all project writers and preserve a copy of the original bytes.
Repair with the recorded schema or restore a known-good backup. For a newer
schema, use a compatible application version. Do not delete state to get past
the error. Check confirmed identity before resuming; a product file is not proof
that subsequent extraction or task creation completed. The fallback read of
`duplo.json` in name derivation uses the same validated main-state reader as
the pipeline, investigator, and verification loader.

## Main state and processing manifests

`duplo.json` accepts unversioned legacy objects and schema version 1. Reads are
read-only; each successful mutation writes version 1. Known scalar, list, and
object fields are checked for the shapes consumers require. Partial legacy
objects and unknown fields remain supported. Validation does not independently
establish that a feature is implemented or a requirement is correct.

Short saver operations use `edit_state()` and retain unrelated fields. Feature
merging works on a copy without a lock during model calls; publication compares
its original logical snapshot under the lock. Preference extraction similarly
checks a pre-call snapshot. A mismatch raises `StateConflictError`, preserves
newer state, and does not automatically repeat model calls. Whitespace-only edits
are equivalent snapshots; this comparison is not a history revision counter.
Reload and review the affected operation before explicitly retrying it.

Legacy processing manifests map arbitrary filenames to string hashes. Updates
migrate them to `{"schema_version": 1, "entries": {...}}`, preserving names such
as `schema_version` and `entries` inside the entries object. Loading helpers
still return the plain map. Invalid values, malformed envelopes, and unsupported
versions are refused without dropping individual records. Existing envelope
metadata is retained. Older Duplo versions cannot consume the new envelopes.

`record_processed_videos()` merges against the current checkpoint under lock.
`save_hashes()` replaces the whole file manifest and accepts optional expected
entries; the pipeline passes its observed checkpoint to reject intervening
changes. Direct callers omitting expected opt into serialized last-write
replacement. Scrape timestamp updates retain unrelated fields, with a snapshot
check on the unchanged-content branch and a source-URL check at final publication.

These JSON guarantees cover individual state operations. Duplo now serializes
normal/fix pipelines and file operations under its own ownership lock, and stages
reference/example publication as described below. A complete extraction run still
is not one transaction: generated plans, earlier state changes, and provider
effects may have occurred when a later conflict is reported. Retain logs and
inspect the project before retrying. McLoop, older versions, direct JSON writers,
and Duplo init/reauthor do not share this Duplo pipeline ownership lock.

## Tested failure boundaries

| Boundary | Expected durable evidence / next action |
| --- | --- |
| Invalid JSON, invalid schema, serialization, file fsync, or pre-replace failure | Original bytes remain. Diagnose the exception; repair input or environment. No mutating callback runs on invalid stored identity. |
| Writer killed immediately before replace | Previous state remains, OS releases the lock, and a subsequent update can proceed. A temporary file may remain. |
| Replace succeeds but directory fsync fails | Complete new state can be visible even though the call raises. Inspect the file before retrying a non-idempotent update; an exception is not proof of rollback. |
| Several cooperating JSON updates | All updates are retained; shared tests check 120 appends, and public Duplo APIs retain 40 feedback records plus 40 video records from four processes. |
| State changes during feature merging, preference extraction, or an unchanged scrape | Stale publication raises a conflict and preserves the intervening update; no automatic provider retry. |
| File checkpoint changes during processing | Expected entries no longer match; the newer checkpoint is preserved. |
| Summary archive publication fails | Previous latest and prior archives remain intact. Inspect task and Git evidence; do not rerun a task based on missing summary. |
| Summary archive succeeds but latest replacement fails | New archive remains; latest may describe an earlier run. Locate archive by identity and verify its commits. |
| Two summaries share a start timestamp | UUID filenames preserve both archives. Latest identifies whichever publication finished last. |

## Verified commit reconciliation

No transaction spans verification, Git, plan advancement, and ledger append.
McLoop's `completion` module now owns the conservative gate around verified
single-task commits (including declared acceptance with changes) and batch
commits. After verification, it publishes a UUID receipt before invoking Git.
Each successful boundary atomically updates that same record. Bare-loop startup
holds the project ownership lock and checks all receipts before provider
preflight, `--retry`, interrupt hints, checkpointing, or task execution.

The receipt retains task IDs/text, the prior plan, the task baseline when
available, check command/output, observed HEAD/status, returned commit hash,
post-completion plan, and ledger IDs when settlement returns. Missing Git evidence
is recorded as a failed command, never as proof of no change. Reports use Git's
no-optional-locks mode. The existing batch path emits no ledger events; its
settled receipt records that limitation explicitly.

| Interruption boundary | Durable evidence and reconciliation |
| --- | --- |
| Receipt publication fails before replacement | No commit invocation. Existing evidence remains. Diagnose storage; inspect worktree before retrying the editor. |
| Receipt replacement becomes visible but its directory sync fails | A pending receipt may exist although begin raised. Startup checks it; inspect the record and Git before retrying. |
| Verified receipt exists, before/during Git or after commit before return | Pending `verified` receipt. Compare actual Git history with the baseline; local commit and push outcomes can differ. Never infer rollback from an exception. |
| Commit returned, before plan mutation | `commit_returned` includes returned hash. Verify that revision and plan task before completing the task through planfile. |
| Plan mutation lands before its receipt update | Receipt may still say `commit_returned`; compare current plan with its saved prior version. Do not replay solely from the stage label. |
| Plan is updated, before/during/after ledger append or reauthoring | Pending `plan_updated` receipt. Inspect actual ledger events and any reauthored plan. A missing event ID in the receipt does not establish that append failed. |
| Final receipt update fails | Earlier complete record remains, or a complete settled record may already be visible. Check the actual record; never delete evidence to force retry. |
| Receipt reaches `settled` | Commit/plan/settle hook returned. Later plan pushes, post-run checks, and audits are outside this receipt. |

Single-task commit errors retain a pending receipt and return terminal failure;
they no longer emit a potentially false task-failure ledger event or trigger
reauthoring from an ambiguous commit. Batch commit errors raise a recovery stop
instead of returning a retryable editor failure. A process can die with no
`interrupted.json`; the receipt gate still applies.

`mcloop recover` returns read-only JSON with pending receipts, current Git evidence,
and current plan text. The operator stops writers, preserves state, checks the
actual revision and verification applicability, reconciles task status through
planfile, and inspects ledger events before any typed append. A completed task
can be recorded with `bob-plan done <plan> <task-id>` only after confirming its
outcome. When evidence is insufficient, preserve a pending/failed task and record
that decision. `mcloop recover --acknowledge <id> --reason <explanation>` acquires
the same ownership lock, stores the resolution and current evidence, and marks
only the receipt acknowledged. It never edits Git, plans, or the ledger and never
calls a model. Unsupported/corrupt receipts cannot be acknowledged; restore or
repair them after stopping writers. This gate trusts the operator's explicit
resolution and is not an independent acceptance oracle.

Tests exercise actual Git commits and ledger appends through both single-task
success branches; interrupt before Git, after commit, after plan completion, and
after append; verify refusal before provider preflight/retry; terminate child
processes without cleanup; and inject receipt replacement failures. Ownership,
corruption, report-only CLI behavior, acknowledgement preservation, ledger event
links, and batch push ambiguity are also checked.

## Execution before verification

Schema-version-2 execution receipts now extend McLoop's gate across the run.
After read-only preflight, `start_execution()` publishes before retry resets,
interrupt actions, checkpointing, editor execution, or task mutations. It saves
initial plan/Git evidence and records task selection, model attempts, and actual
checkpoint entry/return. The active receipt survives signals and process death,
including cases where the editor committed or a checkpoint landed before return.
It covers all paths inside the bare loop: no-diff completions, auto/user tasks,
rate/session-limit checkpoints, and post-run audit work.

A normal coordinator return atomically records `returned` plus its outcome and
current Git evidence. A returned failure remains a failure, not proof of task
completion. Unhandled exceptions and `SystemExit` leave the receipt active.
Existing deliberately handled in-process retries retain their prior semantics;
restart after interruption does not silently repeat them. Checkpoint status,
staging, diff, and commit errors propagate. Only a successful empty staged diff
establishes that a checkpoint has nothing to commit.

The audit/maintain/investigate CLI dispatches use the same guard. Maintenance
observations include MAINTAIN.md and the active invariant; investigation records
worktree creation and child-launch paths. A child can survive the parent: stop
those processes before reconciliation. An interrupted execution and its verified
completion can leave two pending IDs; inspect and acknowledge each independently.
The existing `mcloop recover` command understands both schemas; old versions
refuse the newer schema rather than replaying it.

Tests interrupt an editor before verification, with and without editor-created
commits; interrupt actual checkpoints immediately before/after Git commit; force
status/staging/commit errors; terminate a child without Python cleanup; and prove
restart refuses both models and checkpoints. CLI dispatch tests cover audit,
maintain, and investigation refusal. Successful/failed normal return remains
separate from interrupted execution.

## Duplo file publication and recovery

Duplo normal/fix pipelines and the public file operations share a nonblocking
POSIX owner at `.duplo/operations/owner.lock`. Example readers take the same lock.
Receipts and backups ignore themselves for ordinary Git staging and require
separate backup. Original permission bits are retained on copied files; inherited
filesystem/ACL behavior is not a universal metadata-preservation guarantee.

Example replacement builds a complete `new/` directory under a UUID archive,
including regular non-JSON operator files. It writes/fsyncs the staged data, then
publishes the receipt. The existing examples directory is renamed into `before/`
and the staged directory into its place. Every rename is followed by directory
synchronization subject to the POSIX limits above. The two renames are not one
atomic operation: a process can die with the public directory missing. A pending
receipt stops cooperating readers and pipelines until the operator reconciles it.
The prior generation stays intact. Directory/symlink entries are refused before
any destructive action; their recursive preservation is not implicitly promised.

Reference moves and accepted-frame copies stage all sources, preserve source
backups, and copy prior destinations before publishing a receipt. Each new file
is atomically replaced; moved sources are removed only afterward. Source and
destination hash mismatches observed before publication stop the operation;
a newer source observed before unlink is preserved. External writers can still
race between the check and action and are unsupported. Frame operations preserve
prior `duplo.json` bytes when present and record its absence otherwise, then save
descriptions after the frame files. Earlier published files can survive a later
failure; no transaction or automatic rollback is implied.

| Interruption boundary | Preserved evidence and operator action |
| --- | --- |
| Staging fails before receipt publication | Public content untouched; staging leftovers may remain. Diagnose storage/input before explicitly retrying. |
| Old example directory moved, new directory not installed | Receipt identifies old `before/` and staged `new/`. Stop writers, preserve both, and choose the verified generation to restore/publish. |
| New examples installed, final receipt write fails | Current new generation and old backup remain. Inspect them before acknowledging; do not rerun extraction from a missing success marker alone. |
| Reference destination published, source removal uncertain | Source backup and overwritten destination backup remain. Compare current hashes and source presence; preserve later edits before restoring anything. |
| Frame files published, description save interrupted | File backups and the prior state snapshot remain. Reconcile descriptions through state APIs; blindly restoring all JSON may lose unrelated later updates. |
| Receipt completed | This file operation returned. Other pipeline effects and provider calls remain independently owned. |

`duplo recover` reports pending receipts and current file hashes/directory entries
without starting a call log or making model calls. Stop writers, preserve the
archive, and choose either the complete published output or the prior content.
Restore/move only after comparing current files and protecting newer edits.
`duplo recover --acknowledge <id> --reason <explanation>` validates under the
owner lock and retains both the original evidence and the observed reconciliation
state. It does not restore/delete files, change plans, call models, or delete
backups. Corrupt/unsupported receipts require repair or known-good restore.
Archives accumulate until the operator deliberately removes unneeded backups
after stopping writers; never remove a live lock.

Tests interrupt before/after directory renames, after reference replacement,
and at final receipt publication; retain both old/new originals; refuse later
processing; exercise changed sources, missing state, corrupt receipts, owner
contention, read-only CLI reporting, and abrupt child-process death.

The initial persistence/recovery deliverables are implemented within these stated
boundaries. Remaining limitations are cross-tool coordination, automatic replay,
cryptographic binding of checks to candidates, direct library/utility paths,
and complete Duplo pipeline transactions (including init/reauthor). These are
not inferred from local atomic publication. The independently verified example
is the next end-to-end test of the supported recovery contract.
