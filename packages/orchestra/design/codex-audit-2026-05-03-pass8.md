# Codex Audit Pass 8 — Orchestra

Date: 2026-05-03
Scope: full repository, eighth pass
Methodology: Codex audit against /Users/mhcoen/proj/orchestra at HEAD
000796f. Pytest run: 435 passed, 2 skipped. Findings filtered to SERIOUS
only. Prior reports treated as out-of-scope unless a fix proved
incomplete or introduced a regression.

This pass used a deliberately shorter prompt to test whether the prior
prompts were priming Codex to find issues. The shorter prompt asked for
"anything genuinely wrong — bugs a real user would hit, not edge cases
that need a constructed adversarial workflow to trigger." All three
findings below are real; none required adversarial workflows.

## Findings

### 1. [security] Mock adapters still persist prompt previews

Location: orchestra/adapters/mock_model.py:27-37;
orchestra/adapters/mock_human.py:46-60;
orchestra/executor/executor.py:321-327, 2101-2107

Issue: MockModelAdapter and MockHumanAdapter still place prompt[:160]
in prepared.summary, and the executor persists that summary in
actor_prepare records. Verified: a prompt containing SECRET_TOKEN_123
appears directly in both summaries. This is the same durable input-
prompt leak class pass-7 removed from subprocess adapters, but it
remains in non-subprocess adapters.

Smallest fix: Apply the same summary contract to all prompt-consuming
adapters: keep prompt bytes only in non-persisted invocation
internals, replace prompt_preview with prompt_sha256 computed over
the exact prompt text/bytes. Add regression coverage that
actor_prepare records for mock model and mock human do not contain a
sentinel secret.

Confidence: verified.
Relation to prior audits: adjacent to pass-7 #1.

### 2. [security] Prompt snapshots are world-readable under the default umask

Location: orchestra/prompt_snapshot.py:109-130, 156-160; run dirs
created at orchestra/api.py:885-912 and orchestra/cli.py:216-223

Issue: Prompt source snapshots are copied with shutil.copyfile()
into directories created with default permissions. Under the normal
022 umask, prompt_sources is 0755 and copied prompt files are 0644,
making persisted prompt inputs readable by other local users. Since
prompt snapshots are canonical run state and may contain credentials
or proprietary context, this is a durable credential/content leak.

Smallest fix: Create run directories and prompt_sources with private
directory permissions (0700), and write or chmod snapshot files to
0600. Add a test that forces umask 022, snapshots a prompt source
containing a sentinel secret, and asserts private directory/file
modes.

Confidence: verified.
Relation to prior audits: adjacent to pass-7 #1.

### 3. [security] REPL history persists raw user queries in a world-readable file

Location: orchestra/repl.py:48, 311-315, 373-377

Issue: The REPL uses prompt_toolkit.history.FileHistory at
~/.orchestra/history, so raw query lines are persisted outside the
run directory. Verified: FileHistory writes a query containing
SECRET_TOKEN_123 verbatim and creates the history file as 0644 under
the default umask. This reintroduces sensitive prompt persistence
through the REPL surface even though subprocess adapter summaries no
longer store prompts.

Smallest fix: Keep history (up-arrow recall is the main reason anyone
uses a REPL) but enforce 0700 on ~/.orchestra and 0600 on the history
file. The implementation is shared with #2 (same private-mode
discipline). Do not disable history by default; do not redact query
payloads. The remaining residual (root or backup-process can still
read history) matches the residual every shell history file has and
users already accept that risk.

Confidence: verified.
Relation to prior audits: adjacent to pass-7 #1.

## Summary

3 serious findings, all security-class. No correctness, concurrency,
or design findings met the serious bar. The shorter, less priming-
heavy prompt produced a smaller, more focused finding set; all three
are real and verified with on-disk sentinel-secret reproduction.

The pass-7 stdin redesign for the real subprocess adapters held up
clean. No prior fix introduced a regression. origin/main is not
stable enough to leave as-is for workflows where prompts or REPL
queries may contain secrets, because adjacent prompt-persistence
surfaces remain unchanged.

## Convergence note

Yields: 8 -> 5 -> 3 -> 2 -> 3 -> 1 -> 1 -> 3.

The architectural cycle on correctness/concurrency/design is closed.
What remains is the security class, and pass-8 confirms what pass-7
suggested: there is meaningful surface area there that has not been
audited by any prior pass. These three findings are the residue of
pass-7's bounded scope (subprocess adapters only). After this fix
lands, the security class on prompt-persistence is meaningfully
covered. Output-persistence (model outputs that contain sensitive
content) is a different surface and is not addressed.

The shorter prompt produced findings of higher quality (verified
with concrete reproductions, no adversarial workflow construction)
than the longer prompts of passes 5 and 6. This is evidence that
the long prompts were priming Codex to find issues by enumerating
candidate failure modes.

## Recommended fix

Single commit, three pieces:

1. Mock adapters: prompt_sha256 instead of prompt[:160] in
   prepared.summary. Same redaction discipline as pass-7's subprocess
   adapter fix.

2. File-mode discipline: snapshot directory created with 0700,
   snapshot files written with 0600. ~/.orchestra directory same.
   Implementation shared with #3.

3. REPL history: keep with up-arrow recall intact. Enforce 0700 on
   ~/.orchestra and 0600 on the history file. Do not disable, do not
   redact.

## Constraints for the fix work

- Single commit.
- Empirical verification per CLAUDE.md inviolate rule #1. Regression
  tests must reproduce the leaks Codex demonstrated (SECRET_TOKEN_123
  in mock adapter summary, world-readable snapshot file under umask
  022, world-readable history file) and assert each is closed
  post-fix.
- mypy --strict, ruff, pytest must all pass.
- Per standing rules: never mention Claude, Claude Code, or
  Anthropic in any commit message.
- Do not expand scope into output-persistence (model outputs, JSONL
  log content, transcript stdout capture). That is a separate audit
  if the user wants it.
