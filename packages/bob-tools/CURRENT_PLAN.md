## Stage 6: File I/O

- [ ] [BATCH] Implement load, save, and update
   - [ ] `load(path: Path) -> Plan`: read file, call `parse_plan(text, source_path=path)`. Errors propagate.
   - [ ] `save(path: Path, plan: Plan) -> None`: render to text, write atomically (write to a tempfile in the same directory, fsync, rename). Acquire an advisory file lock (`fcntl.flock` with LOCK_EX) for the duration of the write. Release after rename.
   - [ ] `update(path: Path, operation: Callable[[Plan], Plan]) -> Plan`: load, lock, re-parse to detect concurrent edits, apply operation, save, release lock. Returns the new Plan. This is the safe-mutation entry point for tools that race with humans.
   - [ ] Tests: atomic write does not leave half-written files on simulated crash (use a tempdir and a side-channel that simulates failure between write and rename); locking serializes two concurrent `update` calls; `update` detects mid-flight external edits and raises.

- [ ] Verify Stage 6 leaves the repo green.
