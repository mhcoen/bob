# Persistence ownership and recovery

Initial inventory, 2026-09-05. This is the first boundary of improvement-plan
Slice B. Product identity and run-summary changes below are implemented; the
remaining migrations and cross-system failure injection are still queued.

## Inventory

| State and owner | Role | Existing publication / recovery contract |
| --- | --- | --- |
| `PLAN.md`, `BUGS.md` through `bob_tools.planfile` | Authoritative scheduling intent and task status | Canonical validation, sidecar lock, file fsync, replacement, directory fsync. `update` also detects an external change before lock acquisition. Use the API; reconcile Git and ledger evidence before changing task completion. |
| `PLAN.events.jsonl` and `.writers/*.seq` through `bob_tools.ledger.storage` (configured ledger directory, including `.duplo/ledger`) | Authoritative event evidence and sequence allocation | Validated append under advisory lock, file/directory synchronization; corrupt sequence state is refused. Preserve event and writer state together. Ledger projections are reconstructible; do not discard events or invent a replacement sequence. |
| `.duplo/product.json` through `saver.save_product` / `derive_app_name` | Authoritative confirmed identity and user-edited names | **Migrated:** versioned validation, locked read-modify-write, atomic replacement. Reads do not mutate. Corruption or unknown versions stop the operation. |
| `.duplo/duplo.json` through `saver` and other Duplo consumers | Authoritative selections, status, phase history, preferences, feedback, issues | **Pending:** many direct writes and tolerant reads remain. Back up this file before recovery; missing/corrupt state may currently trigger regeneration. Migrating writers alone is insufficient: inventory readers and lifecycle transitions too. |
| `.duplo/file_hashes.json`, `processed_videos.json` | Processing checkpoints | Values can be recalculated, but completion and prior paid work cannot be inferred from content hashes alone. Direct writes remain. Losing these may repeat processing and provider calls; preserve with Duplo state. |
| `.duplo/references`, `examples`, `raw_pages`, `site_media`, frame descriptions | Retained inputs and derived evidence | Originals moved into references may be the only surviving copy. Derived artifacts depend on source availability and possibly paid calls. Preserve originals; rebuilding outputs is an explicit operation, not automatic crash reconciliation. |
| Orchestra run directory (`store.sqlite`, `log.jsonl`, `visibility.json`, prompt snapshots) | Artifact versions and execution evidence | SQLite WAL with FULL synchronization; log append fsync and tail-recovery rules; visibility gates successful invocations. Default CLI root is `~/.orchestra/runs`, configurable. Preserve the entire run directory using a consistent SQLite backup or after stopping writers; copying only the database while live may omit WAL data. Existing resume owns replay; no new replay guarantee here. |
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
`duplo.json` in name derivation also refuses corrupt input, without claiming a
schema migration of that file.

## Tested failure boundaries

| Boundary | Expected durable evidence / next action |
| --- | --- |
| Invalid JSON, invalid schema, serialization, file fsync, or pre-replace failure | Original bytes remain. Diagnose the exception; repair input or environment. No mutating callback runs on invalid stored identity. |
| Writer killed immediately before replace | Previous state remains, OS releases the lock, and a subsequent update can proceed. A temporary file may remain. |
| Replace succeeds but directory fsync fails | Complete new state can be visible even though the call raises. Inspect the file before retrying a non-idempotent update; an exception is not proof of rollback. |
| Several cooperating JSON updates | All updates are retained; a four-process regression checks 120 appended entries. |
| Summary archive publication fails | Previous latest and prior archives remain intact. Inspect task and Git evidence; do not rerun a task based on missing summary. |
| Summary archive succeeds but latest replacement fails | New archive remains; latest may describe an earlier run. Locate archive by identity and verify its commits. |
| Two summaries share a start timestamp | UUID filenames preserve both archives. Latest identifies whichever publication finished last. |

No atomic transaction currently spans verification, Git commit, ledger append,
and plan advancement. If interrupted between those steps, retain all evidence
and reconcile the actual Git revision, checks, task ID, and recorded events
before replaying a mutating action. The next Slice B boundary must inject each
of those failures and implement explicit reconciliation; this document does not
claim that broader recovery is already automatic.
