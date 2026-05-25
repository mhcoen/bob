# Codex Audit Pass 9 — Orchestra (Output-Persistence Scoped)

Date: 2026-05-03
Scope: output-persistence class only — where does content produced by
the model or by an adapter subprocess end up persisted on disk or
exposed to other processes?
Methodology: scoped audit, deliberately shorter prompt than passes 1-7.
Counterpart to passes 7 and 8 which addressed input-prompt persistence.

This pass used a class-scoped prompt rather than the general full-repo
prompt of pass-1 through pass-8. Output-persistence had been flagged as
uncovered surface area at the end of pass-8.

## Findings

### 1. [security] Subprocess transcript logs duplicate model stdout/stderr outside the private run directory

Location: orchestra/adapters/_subprocess.py:399-442; called by
orchestra/adapters/claude_code_text.py:143-176;
orchestra/adapters/claude_code_agent.py:147-184;
orchestra/adapters/codex_text.py:150-182;
orchestra/adapters/codex_agent.py:157-191

Issue: All real subprocess adapters capture combined stdout/stderr,
then persist it verbatim with write_log(). By default the transcript
path is project_dir/.mcloop/logs, not the pass-8-private run
directory, and it is used only as payload.fields.log_path /
transcript_ref; resume hydrates from payloads/*.json, not from these
transcript files. Verified under umask 022: .mcloop and .mcloop/logs
are 0755, transcript files are 0644, and a sentinel output string is
written verbatim.

A model or adapter subprocess that prints a secret, customer snippet,
internal doc excerpt, tool output, traceback, or stderr diagnostic
leaves a second copy in a world-readable project-local transcript
file on normal multi-user POSIX defaults. The run payload already
retains the final output for replay, so the transcript copy is
incidental debugging persistence, not required resume state.

Smallest fix: Tighten file modes on transcripts. Create .mcloop and
.mcloop/logs with 0700, write transcript files with 0600. Same
private-mode discipline as pass-8.

The structural question — should transcripts live under the run
directory, or be opt-in/debug-only — is a separate design decision
worth logging to IDEAS.md. Current convention is one .mcloop/logs
per project for cross-run debugging visibility; changing that
affects mcloop and any other tooling that reads transcripts by
convention. Hold per-project location until a real workflow needs
the change.

Confidence: verified by mode/content check.
Relation to prior audits: adjacent to pass-7 #1 and pass-8 #2.

## Summary

1 output-persistence leak found. The canonical replay surfaces are
mostly justified: payload JSON and committed artifacts contain output
because guards, resume, and downstream states need them, and current
run directories are forced private. The unjustified duplicate is the
project-local adapter transcript log, which persists full
stdout/stderr with weaker default permissions.

After this fix, output-persistence is meaningfully covered. The only
known remaining residual is the shell-history-equivalent persistence
of REPL queries in ~/.orchestra/history (acknowledged in pass-8 as
matching every shell history file's behavior, accepted residual).

## Convergence note

Yields: 8 -> 5 -> 3 -> 2 -> 3 -> 1 -> 1 -> 3 -> 1.

The architectural cycle on correctness/concurrency/design closed at
pass-7. Passes 8 and 9 surfaced the security-class residue: pass-8
the prompt-persistence residue across mock adapters, snapshots, and
REPL history; pass-9 the output-persistence residue in subprocess
transcripts. Both classes are now meaningfully covered.

The pass-9 prompt was deliberately shorter and class-scoped than
prior prompts. Result was one well-verified finding with a clean
fix. This is consistent with the pass-8 observation that shorter
prompts produce higher-quality findings than long methodology-heavy
prompts.

## Recommended fix

Single commit, two pieces:

1. .mcloop and .mcloop/logs created at 0700; transcript files
   written at 0600. _copy_private/chmod discipline as pass-8.

2. Regression tests: force umask 022, write a transcript with
   sentinel SECRET_TOKEN_123, assert file mode 0600 and directory
   modes 0700. Cover both the freshly-created-directory case and
   the pre-existing-loose-directory case (existing .mcloop/logs
   at 0755 should tighten back to 0700 on next write).

The structural question (transcript location) goes to IDEAS.md as
a separate design item, not into this commit.

## Constraints for the fix work

- Single commit.
- Empirical verification per CLAUDE.md inviolate rule #1.
- mypy --strict, ruff, pytest must all pass.
- Per standing rules: never mention Claude, Claude Code, or
  Anthropic in any commit message.
- OSError on chmod swallowed under POSIX threat model assumption,
  matching the pass-8 discipline for non-POSIX filesystems.
