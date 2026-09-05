# Persistence ownership and recovery

Updated 2026-09-05 for improvement-plan Slice B. Product identity, main Duplo
state, processing manifests, and run-summary persistence are migrated. Recovery
across verified commit transitions now stops at an explicit reconciliation gate.
Other task paths and multi-file Duplo operations remain queued.

## Inventory

| State and owner | Role | Existing publication / recovery contract |
| --- | --- | --- |
| `PLAN.md`, `BUGS.md` through `bob_tools.planfile` | Authoritative scheduling intent and task status | Canonical validation, sidecar lock, file fsync, replacement, directory fsync. `update` also detects an external change before lock acquisition. Use the API; reconcile Git and ledger evidence before changing task completion. |
| `PLAN.events.jsonl` and `.writers/*.seq` through `bob_tools.ledger.storage` (configured ledger directory, including `.duplo/ledger`) | Authoritative event evidence and sequence allocation | Validated append under advisory lock, file/directory synchronization; corrupt sequence state is refused. Preserve event and writer state together. Ledger projections are reconstructible; do not discard events or invent a replacement sequence. |
| `.duplo/product.json` through `saver.save_product` / `derive_app_name` | Authoritative confirmed identity and user-edited names | **Migrated:** versioned validation, locked read-modify-write, atomic replacement. Reads do not mutate. Corruption or unknown versions stop the operation. |
| `.duplo/duplo.json` through `saver` and other Duplo consumers | Authoritative selections, status, phase history, preferences, feedback, issues | **Migrated:** all runtime JSON readers use schema validation; short mutations hold a sidecar lock through atomic publication. Provider-backed feature merges and preference extraction compare a pre-call snapshot before saving. Unknown fields and unrelated history are preserved. |
| `.duplo/file_hashes.json`, `processed_videos.json` | Processing checkpoints | **Migrated:** flat legacy maps remain readable; updates write versioned envelopes with entries. Video records merge under a lock; the pipeline checks its original file checkpoint before replacement. Losing checkpoints may repeat processing and provider calls; preserve them with Duplo state. |
| `.duplo/references`, `examples`, `raw_pages`, `site_media`, frame descriptions | Retained inputs and derived evidence | Originals moved into references may be the only surviving copy. Derived artifacts depend on source availability and possibly paid calls. Preserve originals; rebuilding outputs is an explicit operation, not automatic crash reconciliation. |
| Orchestra run directory (`store.sqlite`, `log.jsonl`, `visibility.json`, prompt snapshots) | Artifact versions and execution evidence | SQLite WAL with FULL synchronization; log append fsync and tail-recovery rules; visibility gates successful invocations. Default CLI root is `~/.orchestra/runs`, configurable. Preserve the entire run directory using a consistent SQLite backup or after stopping writers; copying only the database while live may omit WAL data. Existing resume owns replay; no new replay guarantee here. |
| `.mcloop/completions/<uuid>.json` through `completion` | Verified commit-transition evidence | **Migrated:** validated atomic receipts before Git, then commit return, plan advancement, and ledger settlement. Unresolved receipts block bare-loop startup. Explicit acknowledgement retains evidence and resolution; no automatic replay. |
| `.mcloop/completions/owner.lock` | Bare-loop ownership | Nonblocking POSIX advisory lock held for the bare loop or acknowledgement. Released by process death; never unlink a live lock. Other commands and external writers are not coordinated. |
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

These guarantees cover individual state operations. They do not support two
complete project pipelines as one coordinated transaction: reference moves,
example-directory replacement, generated plans, and provider effects may already
have occurred when a later conflict is reported. Retain their logs and inspect
the project before retrying. Directory snapshots, pipeline ownership, and task
transition reconciliation are still separate recovery work.

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

Remaining boundaries: no-diff completion paths, explicit auto/user tasks,
editor/rate-limit checkpoint commits, audit/maintain/investigation commands,
and death before the verified receipt is published. The current worktree is not
cryptographically bound to the check output, and Git hooks or background work
can change it. Other commands, older versions, external editors, and independent
Duplo pipelines do not honor the bare-loop ownership lock. Runtime directories
remain separately backed-up local state; a clone cannot reconstruct these
receipts. Slice B remains partial, with full attempt ownership and Duplo directory
operations still pending.
