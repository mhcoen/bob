# Proposal: Shared role-to-model bindings in `.orchestra/config.json`

## Problem

The current config schema collapses role definition and role binding into a
single per-workflow block:

```json
{
  "workflows": {
    "code_edit": {
      "pattern": "draft_then_adjudicate",
      "roles": {
        "drafter":     { "adapter": "claude_code_text",  "model": "kimi-k2.6", ... },
        "adjudicator": { "adapter": "claude_code_text",  "model": "opus", ... },
        "editor":      { "adapter": "claude_code_agent", "model": "opus", ... }
      }
    },
    "bug_verify": {
      "pattern": "propose_critique_synthesize",
      "roles": {
        "proposer":    { "adapter": "claude_code_text",  "model": "kimi-k2.6", ... },
        "critic":      { "adapter": "claude_code_text",  "model": "sonnet", ... },
        "synthesizer": { "adapter": "claude_code_text",  "model": "opus", ... },
        "editor":      { "adapter": "claude_code_agent", "model": "opus", ... }
      }
    }
  }
}
```

Three real problems with this:

1. **Duplication.** A role like `drafter` or `editor` that appears in
   multiple workflows must have its model, adapter, and parameters
   restated in each workflow's block.

2. **Drift.** Switching the drafter model from `kimi-k2.6` to
   `deepseek-v4-pro` requires editing every workflow that uses a
   drafter. The user has no single point of control.

3. **No reusable identity.** A role is just a name inside one
   workflow. Two workflows that both have an "adjudicator" role have
   no enforced relationship between those adjudicators. The user
   cannot say "use the same adjudicator across all workflows" except
   by manually keeping the entries in sync.

The current schema works for one workflow with one set of role bindings.
It does not scale to a real library of patterns and projects.

## Proposed schema

Two-tier: top-level role bindings define the actor identities once,
workflows reference them by name.

```json
{
  "roles": {
    "drafter": {
      "adapter": "claude_code_text",
      "model": "kimi-k2.6",
      "parameters": {}
    },
    "adjudicator": {
      "adapter": "claude_code_text",
      "model": "opus",
      "parameters": {}
    },
    "synthesizer": {
      "adapter": "claude_code_text",
      "model": "opus",
      "parameters": {}
    },
    "critic": {
      "adapter": "claude_code_text",
      "model": "sonnet",
      "parameters": {}
    },
    "proposer": {
      "adapter": "claude_code_text",
      "model": "kimi-k2.6",
      "parameters": {}
    },
    "editor": {
      "adapter": "claude_code_agent",
      "model": "opus",
      "tools": "default",
      "parameters": {}
    }
  },
  "workflows": {
    "code_edit": {
      "pattern": "draft_then_adjudicate"
    },
    "bug_verify": {
      "pattern": "propose_critique_synthesize"
    }
  }
}
```

Workflow blocks become minimal: a pattern name and (optionally) overrides.
The role bindings live once at the top level. Changing the drafter model
across all workflows is a single one-line edit.

## Per-workflow overrides

Sometimes a workflow legitimately needs a different binding for a role
than the global default. The schema supports per-workflow overrides as
an explicit exception:

```json
{
  "roles": {
    "drafter": {
      "adapter": "claude_code_text",
      "model": "kimi-k2.6",
      "parameters": {}
    },
    "editor": {
      "adapter": "claude_code_agent",
      "model": "opus",
      "tools": "default",
      "parameters": {}
    }
  },
  "workflows": {
    "code_edit": {
      "pattern": "draft_then_adjudicate"
    },
    "code_edit_aggressive": {
      "pattern": "draft_then_adjudicate",
      "role_overrides": {
        "drafter": { "model": "deepseek-v4-pro" }
      }
    }
  }
}
```

`role_overrides.<role>` is a partial RoleBinding. Keys present override the
top-level binding; keys absent inherit from the top level. So the example
above keeps drafter's adapter (`claude_code_text`) and parameters but
swaps the model.

A workflow without `role_overrides` resolves all role bindings purely
from the top-level `roles` table.

## Resolution semantics

For each role required by a workflow's pattern:

1. If the workflow has `role_overrides.<role>`, take the top-level
   binding for `<role>` (which must exist) and apply the override
   keys on top of it.
2. Else if the top-level `roles.<role>` exists, use it as-is.
3. Else fail validation with a clear message.

Override only works on top of an existing top-level binding. There is
no "override-only" mode where a workflow defines a role that has no
top-level entry. This forces every role identity to live in one place.

Per-workflow `tools` overrides for edit-agent roles are supported through
the same mechanism: `role_overrides.editor.tools = "Edit,Write"` produces
an editor binding that inherits adapter and model from the top level but
narrows the tool set for that workflow.

## Validation

The validator (`_validate_role_bindings`) is simpler than the current
two-pass version because role identity is resolved once globally:

1. Walk all workflow patterns to collect the set of role names each
   pattern requires.
2. For every (workflow, required role) pair, resolve the role binding
   per the rules above. If resolution fails (no top-level binding,
   override references a missing top-level role, etc.), accumulate
   the error.
3. For every resolved binding, verify the adapter exists in the
   registry and the adapter's actor kind matches the workflow state's
   actor kind (model state needs text adapter, agent state needs
   agent adapter). This is the same kind-mismatch check the current
   validator performs, applied to the resolved binding.
4. Raise `ConfigError` with all accumulated mismatches if any.

A new error class is unnecessary. `ConfigError` already covers this.

## Migration

The current schema has shipped. Existing config files have per-workflow
role blocks and no top-level `roles` key. The migration path:

**Option A: hard cutover.** The new schema is the only schema.
Old configs fail to load with a clear error message naming the
expected top-level `roles` key. The user updates their config once.

**Option B: support both transiently.** The loader accepts either
shape. If a workflow has a `roles` block, treat it as a fully
self-contained binding (current behavior). If it has `pattern` only
or `role_overrides`, resolve through the top-level table.

Option A is cleaner and avoids carrying two code paths. Given that
the only user of this schema today is the smoke-test config (one file)
and McLoop's own future configs (none yet), the cost of a hard cutover
is one config file to rewrite. Choose Option A.

The smoke-test config gets rewritten as part of the implementation:

```json
{
  "roles": {
    "editor": {
      "adapter": "claude_code_agent",
      "model": "opus",
      "tools": "default",
      "parameters": {}
    }
  },
  "workflows": {
    "code_edit": {
      "pattern": "single"
    }
  }
}
```

Same behavior as the current smoke-test config, just expressed in the
new shape.

## Implementation surface

Files to change in `/Users/mhcoen/proj/orchestra`:

1. **`orchestra/config.py`**: change `OrchestraConfig` to carry a
   top-level `roles: dict[str, RoleBinding]` plus
   `workflows: dict[str, WorkflowConfig]` where `WorkflowConfig` has
   `pattern: str` and optional `role_overrides: dict[str, dict]`.
   The `load_config` function parses both. The default config (when
   no file exists) populates a sensible default top-level binding for
   `editor` (claude_code_agent + opus + default tools) and a single
   workflow entry mapping `code_edit` to `single`.

2. **`orchestra/api.py`**: `_resolve_role_binding(workflow_name, role_name, config) -> RoleBinding`
   handles the top-level + override resolution. `_validate_role_bindings`
   uses it instead of the per-workflow `roles` lookup. `_PerRoleDispatcher`
   constructor receives resolved bindings, not the raw per-workflow
   block.

3. **`orchestra/api.py:run_workflow`**: when building the registry
   for a run, walk the workflow's required roles, resolve each via
   `_resolve_role_binding`, and register the resulting adapter under
   the role name. Same dispatcher behavior as today; only the source
   of binding data changes.

4. **`tests/test_config.py`**: tests for top-level + override
   resolution, missing-top-level errors, override-references-missing
   errors, kind-mismatch errors, default-config shape.

5. **`tests/test_api.py`** or wherever role validation lives:
   tests confirming the validator catches the same misconfigurations
   it catches today, expressed in the new schema.

6. **Update `/Users/mhcoen/proj/orchestra-smoke-test/.orchestra/config.json`**
   to the new shape. Re-run the smoke test (steps 1, 2, 3) and confirm
   all three still pass.

7. **Update `design/orchestra-mcloop-integration-plan.md`** to reflect
   the new schema. The example config blocks in that document need
   rewriting.

Files NOT to change:

- `orchestra/workflows/*.orc`. The `.orc` files declare role names;
  they do not bind models. They are unchanged.
- `orchestra/workflows/templates/*.md`. Same.
- `mcloop/code_edit.py`. The McLoop-side wrapper passes the loaded
  config to `orchestra.run_workflow`. The shape of the config is
  opaque to McLoop. The wrapper does not need to change.

## Risks and considerations

**Override semantics get muddy if extended carelessly.** The current
proposal allows partial overrides (single keys). If we later add
nested-key overrides (e.g., `parameters.timeout`), we have to decide
whether overrides merge or replace. To keep this simple now: an
override key replaces the top-level value entirely. `parameters: {x: 1}`
in the override replaces the entire parameters dict, it does not merge
into it. The only fields where this matters today are `parameters` and
`tools`. Document this explicitly.

**Multiple workflows with the same pattern but different role
identities.** Suppose a user wants two flavors of `draft_then_adjudicate`:
one with kimi-as-drafter, one with deepseek-as-drafter. With overrides,
this is two workflow entries with the same pattern and different
`role_overrides.drafter.model`. The user can name them
`code_edit_kimi` and `code_edit_deepseek` and pick at the McLoop
call site by passing the right workflow name. This works but reveals
that workflow names are now config-dependent identifiers, not just
pattern aliases. Document this.

**Tools key for edit-agent roles.** Today `tools` is a top-level key
inside the per-workflow role binding. In the new schema, `tools` is
a top-level role-binding key (alongside adapter, model, parameters).
A workflow override can narrow it. This matches the existing
RoleBinding shape; no new field is introduced.

**Future "agents" abstraction.** The orchestra grammar already has an
`agent` declaration that bundles a model, adapter, and context policy
under a name. The current schema doesn't expose agents; the proposed
schema doesn't either. A future iteration could let top-level role
bindings reference an agent by name instead of restating
adapter+model. Out of scope for this proposal.

## Out of scope

- Workflow-discovery improvements (a CLI or doc that lists available
  patterns).
- Per-project agent declarations (orchestra grammar feature, not
  config schema).

## Addendum: global plus project config merge

The original proposal listed cross-project shared bindings (a
`~/.orchestra/config.json`) as out of scope. That has since landed
because the verb-style CLI needs a global config and McLoop's
project-local configs would otherwise drift out of sync.

The on-disk schema does not change. The loader assembles a merged
config from up to two files:

- `~/.orchestra/config.json` (global).
- `<project>/.orchestra/config.json` (project, optional).

The merge rule is the same replace-not-nest rule the workflow-level
`role_overrides` already uses, applied one level up:

- Section absent in project config: use global as-is.
- Section present in project config: per-key override. A role or
  verb or workflow defined in project config replaces the global
  entry of the same name in full. Entries in the global section
  the project does not redefine are inherited.
- Section present in project config but not global: just use
  project's.

Two layers, same rule. A workflow's `role_overrides` can still
narrow a single role binding for a single workflow, on top of
whatever the merged config produced for that role at the top level.
Validation runs against the merged config. The default-config
fallback applies only when neither file exists.

## Acceptance criteria

The change is done when:

1. A config with the new shape (top-level `roles` plus `workflows.<name>.pattern`)
   loads, validates, and runs `single`, `draft_then_adjudicate`, and
   `propose_critique_synthesize` workflows correctly.

2. A config with `role_overrides` correctly inherits unspecified keys
   from the top-level binding and substitutes specified keys.

3. Validation errors clearly identify which workflow, which role, and
   what went wrong (missing top-level binding, kind mismatch, etc.).

4. The smoke test (steps 1, 2, 3) passes with the rewritten config.

5. The integration plan and any other documentation reflecting the
   schema is updated.

6. `pytest`, `ruff check .`, and `mypy orchestra` all clean.
