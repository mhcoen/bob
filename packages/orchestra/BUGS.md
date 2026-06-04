## Bugs

### `codex` model identifier resolves to `gpt-5-codex`, which ChatGPT-account Codex rejects — wrong default ecosystem-wide

**Symptom**: every consumer that binds a role/tier to the `codex` short
identifier fails at invocation with
`400 invalid_request_error: The 'gpt-5-codex' model is not supported when using
Codex with a ChatGPT account.` Observed in three places so far:
- duplo `plan_author` reviewer leaf (`{"model": "codex"}`) → review state errored
  (orchestra run on `/Users/mhcoen/proj/writer`, transcript shows the 400).
- mcloop chain tier 2 (`codex/gpt-5-codex`) → `preflight failed` and the tier is
  skipped on every run.
- Any `run_role`/workflow leaf using the `codex` identifier.

**Root cause**: `orchestra/registry/registry.py`,
`BUILTIN_MODEL_IDENTIFIERS["codex"]` is
`ModelIdentifier(name="codex", adapter="codex_text", model="gpt-5-codex")`. The
account's Codex CLI serves `gpt-5.5`, not `gpt-5-codex` (confirmed: running
`codex` from the shell reports `model: gpt-5.5`). So the hardcoded model string
in the canonical identifier table is simply wrong for this account, and every
short-form `codex` binding inherits it. This is one wrong constant, not a
per-project misconfiguration — patching individual project configs to a
long-form `{"adapter": "codex_text", "model": "gpt-5.5"}` masks it one site at a
time instead of fixing the source.

**Evidence**: orchestra run `ed723f36e53b` review record (the 400 message verbatim
in the state output); mcloop startup line `Skipping chain tier 2
(codex/gpt-5-codex): preflight failed`; shell `codex` reporting `model: gpt-5.5`.

**Fix**: change the `codex` identifier's `model` from `gpt-5-codex` to the model
the account serves (`gpt-5.5`) at the source in `BUILTIN_MODEL_IDENTIFIERS`.
Then audit every package for independent `gpt-5-codex` string literals that do
NOT go through the identifier table (mcloop's chain-tier config is one — see
mcloop/BUGS.md) and fix those too, so the model string is correct everywhere a
consumer can reach it.

**Note**: decide whether `gpt-5-codex` should remain a selectable identifier at
all (e.g. for accounts that DO have that model) or be removed. If kept, it must
not be the value behind the bare `codex` identifier that everything defaults to.
