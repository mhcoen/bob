# Codex Audit Pass 7 — Orchestra

Date: 2026-05-03
Scope: full repository, seventh pass
Methodology: Codex re-audit against /Users/mhcoen/proj/orchestra at HEAD
4429c4a. Pytest run: 432 passed, 2 skipped. Findings filtered to SERIOUS
only across four classes. Prior reports treated as out-of-scope unless a
fix proved incomplete or introduced a regression.

## Findings

### 1. [security] Subprocess prompts are persisted in logs and pid metadata

Location: orchestra/adapters/claude_code_text.py:181-199;
orchestra/adapters/claude_code_agent.py:190-209;
orchestra/adapters/codex_text.py:196-209;
orchestra/adapters/codex_agent.py:207-220;
orchestra/adapters/_subprocess.py:257-269,435-438;
orchestra/executor/executor.py:321-327,2101-2107

Issue: All four real subprocess adapters append the fully rendered
prompt as a command-line argument. That command is then written into
actor_prepare summaries, .mcloop/active-pid metadata, and adapter
transcript logs; while running, it is also exposed through process
listings. A prompt containing credentials, private source, incident
data, or customer text is therefore copied into multiple secondary
surfaces outside the intended payload/snapshot mechanism.

Verified: a synthetic command containing SECRET_TOKEN_123 written
through write_log() appears verbatim in the generated adapter log.
Full pytest still passes (432 passed, 2 skipped).

Smallest fix: Stop putting prompt text in argv/logged command
structures.
- Adapters pass prompt bytes through stdin or a mode-0600 temp file
  (verify per-CLI which is supported and pin command-shape tests
  against the new structure).
- Redact prompt-bearing args from prepared.summary, .mcloop/active-pid,
  and write_log; keep only adapter, model, prompt length, and a
  non-secret correlation id.
- Replace prompt_preview with prompt_sha256. Hashing keeps the
  legitimate use case (verifying snapshot integrity, confirming two
  runs got the same input) without retaining content. The eyeball-
  the-preview debug workflow is dropped; if someone needs to see the
  actual prompt, the snapshot is in the run directory.

Confidence: verified.
Relation to prior audits: new.

## Summary

1 serious finding, security. Ship-blocker for any real workflow whose
prompts may contain secrets or proprietary input.

The pass-6 fan-out snapshot extension held up clean: attempts/retries
are immutable integer dict values, the targeted regression test passes,
the full suite passes. No prior fix introduced a new regression.

origin/main is otherwise close, but should not be left as-is for real
subprocess use until the prompt leakage is fixed.

## Convergence note

Yields: 8 -> 5 -> 3 -> 2 -> 3 -> 1 -> 1.

The one-regression-per-pass streak BROKE. This finding is new, not a
regression on any prior fix. The architectural cycle on correctness,
concurrency, and design has reached its floor for this codebase.

What pass-7 surfaced is the residue of audit-class shifts: a security-
class finding that no prior pass specifically went looking for. Six
previous audits had security in scope but none specifically followed
prompt content from rendering through every place it could be
persisted. The audit's security class is doing real work but it has
to be exercised for it to find things.

After this fix lands, the audit cycle is closing for this codebase.
A dedicated security pass scoped to "where does sensitive content end
up" would likely surface 2-3 more findings of similar shape (run
directory file modes, JSONL log content, adapter transcript stdout
capture, runtime metadata, error tracebacks, the relay surface
itself). User declined to scope a full security pass at this time;
the bounded fix above is the agreed scope. mcloop README will gain
a short security-notes section as a separate small task.

## Recommended fix

Single commit, three pieces:

1. Adapters pass prompt via stdin or mode-0600 temp file. Verify
   per-CLI support; pin command-shape tests.
2. Redact prompt content from prepared.summary, .mcloop/active-pid,
   and adapter transcript logs. Keep correlation fields only.
3. Replace prompt_preview with prompt_sha256.

## Constraints for the fix work

- Single commit.
- Empirical verification per CLAUDE.md inviolate rule #1. The
  regression test must reproduce the leak Codex demonstrated
  (SECRET_TOKEN_123 in write_log) and assert it is absent post-fix.
- mypy --strict, ruff, pytest must all pass.
- Per standing rules: never mention Claude, Claude Code, or
  Anthropic in any commit message.
- Do not expand scope into a broader security pass. Bounded fix only.
- The mcloop README addition is a separate small commit on the
  mcloop side, not part of this orchestra fix.
