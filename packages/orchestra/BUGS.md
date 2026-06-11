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

### Progress reporter labels states with the binding's model, ignoring the invocation-options model override

**Symptom**: an operator watching a run sees the wrong model in every progress
line whenever a per-call model override is in effect. Observed 2026-06-10 on an
mcloop-driven code_edit run: mcloop's tier-1 chain passed `model="fable"` as the
invocation override and the live subprocess genuinely ran
`claude -p ... --model fable` (verified against mcloop's `.mcloop/active-pid`
record), yet every progress line read `editor (claude_code_agent:opus)` for the
whole 17-minute session — the editor role's CONFIGURED binding at the time. The
operator reasonably concluded the wrong model was coding. The label is correct
only when the binding happens to coincide with the override.

**Root cause**: two independent paths. The override is applied where it
matters — the executor folds `invocation_options` into `backing_options` at
invoke time, so the adapter builds the command with the override model. But the
progress path never sees it: `_wrap_progress_callback`
(`orchestra/api/bindings.py`) enriches every `ProgressEvent` via
`_resolve(role)`, which returns `(binding.adapter, binding.model)` straight
from the role binding; `_format_backing` (`orchestra/progress.py`) then renders
that stale value. Nothing in the progress path consults the override.

**Evidence**: mcloop active-pid record showing `--model fable` on the live
subprocess concurrent with `(claude_code_agent:opus)` progress lines; source
trace `bindings.py` `_resolve` → `binding.model` vs `executor`
`backing_options.update(self._invocation_options)`.

**Fix**: resolve the EFFECTIVE model — the binding overridden by
`invocation_options` — before emitting progress events, so the label always
names the model the adapter actually invokes. Regression test: run a workflow
with a stub adapter, a role bound to model A, and `invocation_options
{"model": "B"}`; assert the captured `ProgressEvent.model` (and the
`format_event` rendering) says B, not A. A monitoring line that misreports
which model is executing defeats the purpose of having one.
