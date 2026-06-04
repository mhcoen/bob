## Bugs

### Chain tier 2 hardcodes `gpt-5-codex`, which ChatGPT-account Codex rejects

**Symptom**: every mcloop run prints `Skipping chain tier 2
(codex/gpt-5-codex): preflight failed — Codex subscription preflight failed
before starting a task` and runs without the Codex tier.

**Root cause**: the chain-tier config names the model `gpt-5-codex`, which this
account's Codex CLI does not serve (it serves `gpt-5.5`; confirmed by running
`codex` from the shell). Same wrong constant as the orchestra `codex` identifier
bug — see `orchestra/BUGS.md`. This mcloop literal is independent of orchestra's
identifier table, so fixing orchestra alone does not clear this; the chain-tier
model string must be changed to `gpt-5.5` (or whatever the account serves) here
as well.

**Fix**: locate the chain-tier definition that specifies `codex/gpt-5-codex` and
change the model string to `gpt-5.5`. Cross-reference orchestra/BUGS.md so both
sites are fixed together.
