# Codex Audit — Orchestra

Date: 2026-05-03
Scope: full repository
Methodology: Codex review pass against /Users/mhcoen/proj/orchestra. Findings
filtered to SERIOUS only across four classes: correctness, security,
concurrency, design.

## Findings

### 1. [concurrency] Fan-out payload files can collide across child threads

Location: orchestra/executor/executor.py:2059-2061; orchestra/log/log.py:126-153; orchestra/payloads.py:27-44

Issue: Fan-out child workers call self._write_payload(self._log.next_seq, payload)
before reserving a log sequence. Two children that finish together can read
the same next_seq, write the same <run_id>-<seq>.json payload file, and then
log different state_exit records pointing at the same or overwritten payload.
On resume, guards using state.payload.* can hydrate the wrong child payload,
producing wrong routing or corrupted replay state.

Smallest fix: Make payload filenames independent of mutable log sequence.
Options: include invocation_id or a UUID in the filename, or add a LogWriter
API that atomically reserves a sequence/ref before payload write. Add a
fan-out stress test with a barrier adapter that makes multiple children write
payloads simultaneously and asserts distinct payload_refs and replay
hydration.

Confidence: needs verification. The race is visible in the code; a
barrier-based fan-out test would confirm it deterministically.

### 2. [correctness] Mid-log corruption is silently treated as truncation

Location: orchestra/log/log.py:168-182; orchestra/resume/resume.py:103-109

Issue: LogReader.read_all() breaks on the first JSONDecodeError and returns
the prefix as if the run merely crashed while writing the final line. If a
malformed line appears in the middle of a log, resume ignores all later
durable records and can re-enter completed states or miss transitions without
reporting corruption.

Smallest fix: Only tolerate an unterminated malformed final line. For
malformed non-final lines, schema/key errors, or sequence gaps, raise a clear
ResumeError with the line number and refuse to resume.

Confidence: verified.

### 3. [correctness] Missing or corrupt payload files replay as empty payloads

Location: orchestra/payloads.py:47-60; orchestra/resume/resume.py:194-209

Issue: A state_exit with a payload_ref is replayed with {} if the referenced
payload file is missing or contains non-object JSON. Guards that depend on
state.payload.* then evaluate against missing data and can choose a different
branch than the live execution, with no error.

Smallest fix: Treat payload_ref=None as the only valid "no payload" case. If
a non-empty payload_ref is missing, invalid, outside the run payload
directory, or not a JSON object, raise ResumeError instead of returning {}.

Confidence: verified.

### 4. [correctness] source file artifacts are validated as initialized but never loaded

Location: orchestra/loader/parser.py:269-314; orchestra/loader/validator.py:492-497; orchestra/store/store.py:286-325

Issue: The parser records source file / source path, and the dataflow
validator treats any source artifact as initialized. ArtifactStore.declare()
only materializes initial; it ignores source, so a state reading a
source-backed artifact gets None silently.

Smallest fix: Either implement source initialization during store setup,
including file existence and source/initial mutual exclusion, or reject
source qualifiers until implemented. For source file, read once at run start
and write version 0 as the design specifies.

Confidence: verified.

### 5. [design] orchestra run cannot execute packaged agent/transform workflows

Location: orchestra/cli.py:109-166; orchestra/registry/registry.py:187-212; orchestra/api.py:388-496

Issue: Direct workflow execution uses with_core(), which only registers mock
model, human, and shell backings. Packaged workflows using agent fail with
"unknown actor backing 'agent'", and ask_anonymous_reviewers.orc fails
because anonymize_outputs is not registered. This contradicts the README's
direct-run surface and breaks the recent anonymous reviewers workflow outside
the verb/library API path.

Smallest fix: Share the API registry construction path with cmd_run,
including the pre-load registry, builtin transforms, agent support, and
role-binding resolution. Or explicitly reject unsupported direct runs before
loading with a clear message.

Confidence: verified.

### 6. [security] codex_text is not forced read-only

Location: orchestra/adapters/codex_text.py:175-192

Issue: The README describes *_text adapters as read-only, and Claude text
enforces that with Read,Glob,Grep. CodexTextAdapter runs codex exec
--skip-git-repo-check --full-auto without --sandbox read-only or an approval
policy, so its ability to mutate depends on the user's Codex defaults; the
local Codex help exposes --sandbox and does not list --full-auto. A
critique/synthesis role can therefore mutate the workspace or fail on a stale
flag.

Smallest fix: Build the text command with explicit read-only sandboxing.
Current Codex syntax equivalent is codex --ask-for-approval never --sandbox
read-only exec --skip-git-repo-check ... Pin command-shape tests against
current CLI help.

Confidence: verified.

Note: confirm the exact current flag spelling against codex --help before
landing the fix; codex 0.128 deprecates --full-auto in favor of --sandbox
workspace-write and the read-only equivalent should be verified in the same
pass.

### 7. [correctness] Loader accepts states with no success transition

Location: orchestra/loader/validator.py:214-277; orchestra/executor/executor.py:470-475

Issue: Validation requires on error and on timeout for model/agent/shell
states, but does not require the normal success outcome (complete for
model/agent, pass/fail for shell). A malformed workflow can invoke the
actor, write payloads/artifacts, emit state_exit, and only then crash with
"no transition matched outcome".

Smallest fix: Validate required non-error outcomes per backing before
execution. For schema-backed model states, require transitions for the
declared verdict enum; for plain model/agent require complete; for shell
require at least pass and the reachable failure outcomes.

Confidence: verified.

### 8. [design] Dataflow validation allows reads before any possible write

Location: orchestra/loader/validator.py:477-501; orchestra/executor/executor.py:611-641

Issue: The validator only checks that an artifact is initialized or written
by some state somewhere. If the start state or an early branch reads an
artifact that is written only downstream or on another branch, the workflow
loads and the executor substitutes None, silently feeding wrong data into
prompts or transforms.

Smallest fix: Add reachability/dominance validation for artifact reads, or
require initial/source for any artifact read by a state that can be entered
before a writer on all paths. At minimum, warn/error for start-state reads
of artifacts without initial/source.

Confidence: verified.

## Summary

8 serious findings: 4 correctness, 1 security, 1 concurrency, 2 design.
Ship-blocker risk lives in #1 (fan-out payload race) and #5 (direct execution
of packaged agent/transform workflows including ask_anonymous_reviewers). No
ship-blocker found in the recent 78dc68a / ae798a7 progress reporter
changes, though #1 sits in _run_fan_out_group and the parallel-block format
will exercise that code path more visibly than before.

## Recommended fix order

Fix in this sequence, one commit per group:

1. Resume integrity (#2 + #3) in one commit. Both are "resume must
   distinguish absent from corrupt and refuse the latter." Same surface,
   same test setup.
2. Fan-out payload race (#1) in a separate commit. Needs the barrier
   test to confirm before fix; that same test is the regression guard.
3. codex_text read-only sandboxing (#6) in a separate commit. Verify
   current codex --help flag spelling first; pin the command shape in tests.
4. Direct-execution registry (#5) in a separate commit. Choose: fix the
   implementation to match the documented surface, or restrict the documented
   surface to match the implementation. Either is acceptable; the
   inconsistency itself is the bug.
5. Validator cleanup (#4 + #7 + #8) as a final batch. All three are
   "validator accepts incoherent workflow, executor crashes or silently
   substitutes wrong data after side effects." Same shape, same fix surface.

## Constraints for the fix work

- One commit per numbered group above. Do not bundle across groups.
- Empirical verification per inviolate rule #1 in CLAUDE.md: every
  fix must be run, not just reasoned about.
- mypy --strict, ruff, and pytest must all pass after each commit.
- Do not push #5 if you choose the doc-restriction route without confirming
  with the user which side of the inconsistency to keep.
- Per standing rules: never mention Claude, Claude Code, or Anthropic in
  any commit message.
