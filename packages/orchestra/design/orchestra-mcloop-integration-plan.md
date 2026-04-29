# Orchestra integration into mcloop, minimal-path plan

## Goal

Get a working orchestra into mcloop at the highest-leverage decision
point (code edits) so that mcloop's mistakes drop and the human stops
being the relay between models. Two integration sites, then stop.
This adds adapters, packaged workflow lookup, and the Python library
entry point. It introduces no new execution semantics beyond what
slice 1 already provides.

## Scope and non-goals

In scope: two adapter kinds (text role, edit agent), a small Python
library API, three workflow files implementing three named patterns,
two integration points inside mcloop wrapped behind a config flag,
and a parity test proving the default path preserves current
behavior.

Out of scope until forced by a real need: profiles, schemas,
councils-as-DSL, slice 2's versioned workspace, agent declarations,
group declarations, retry sugar beyond what slice 1 has, and
integrations at the other four mcloop decision sites (crash diagnosis,
post-fix review, bug-fix model escalation, reviewer triage).

## Step 1: define the capability surface

Three named patterns ship as workflow files. They are defined
operationally to remove ambiguity about workspace mutation:

- `single`: one edit-agent performs the edit.
- `draft_then_adjudicate`: text-role A proposes an edit plan or patch
  instruction. text-role B selects or rewrites the final instruction.
  Then one edit-agent performs the edit.
- `propose_critique_synthesize`: text-role A proposes. text-role B
  critiques. text-role C synthesizes the final instruction. Then one
  edit-agent performs the edit.

Two role kinds, distinguished explicitly:

- **Text role.** Used for proposer, critic, adjudicator, synthesizer.
  Produces text (an instruction, a critique, a synthesis). Does not
  touch the workspace. May be served by any backend capable of
  text-only inference; in the initial integration, served by Claude
  Code in a constrained, non-mutating configuration.
- **Edit agent.** Used for the single final workspace-mutating
  invocation. Has tools and filesystem access. Served by Claude Code
  in normal mode.

For the initial code-edit integration, exactly one invocation per
orchestra call mutates the workspace. All earlier roles are text-only
advisory invocations. This avoids needing versioned-workspace
arbitration, which the plan explicitly defers.

These are workflow files (`.orc`), not Python. Each binds roles, not
models. Models, instructions, and parameters come from a per-project
config. Config vocabulary stays neutral (`instruction`, `inputs`) so
the same workflow can target a text role or an edit-agent role
without semantic mismatch.

## Step 2: real adapters

Slice 1's model adapter is a mock. Two real adapters land in this
step, distinguished by contract:

- **Text-role adapter.** Takes a prompt or instruction, returns text.
  The contract matches slice 1's model-adapter interface. The first
  implementation invokes Claude Code with the tool set restricted to
  `Read,Glob,Grep`. No `Edit`, `Write`, `Bash`, `WebFetch`, or
  `WebSearch`. The restriction is enforced via the `--allowedTools`
  flag at the subprocess invocation site (the same mechanism mcloop
  already uses for `run_diagnostic`). This is provisional: any
  pure-text inference backend can replace it later without touching
  workflows.
- **Edit-agent adapter.** Takes a task instruction and returns
  whatever the agent did (file changes, tool calls, output). Backed
  by Claude Code in normal mode (`--allowedTools
  Edit,Write,Bash,Read,Glob,Grep`, matching mcloop's current default).
  Lift relevant subprocess invocation, output capture, and
  working-directory handling patterns from `mcloop/runner.py`.

Both adapters keep their interfaces clean so a Codex adapter or
others drop in later without touching workflows or call sites.

## Step 3: library API

Expose:

```
orchestra.run_workflow(name, inputs, config) -> WorkflowRunResult
```

Library, not CLI. Mcloop imports it. The workflow name resolves to a
`.orc` file shipped in orchestra's package or a project-local
override directory under `.orchestra/workflows/`.

`WorkflowRunResult` carries the fields the call site needs without
forcing it to dig through the artifact store:

- `envelope`: the final state's result envelope.
- `artifacts`: a mapping of artifact name to committed version
  reference and value (or a way to fetch the value without opening
  the store directly).
- `run_id`: the orchestra run identifier.
- `log_path`: the path to the JSONL log.
- `summary`: a compact adapter-facing summary including final
  text/output, whether files changed, and the terminal outcome.

### Inputs dict for `code_edit`

Mcloop calls `run_workflow("code_edit", inputs={...}, config=...)`
with the following keys, mirroring the arguments mcloop's existing
`run_task` builds prompts from:

- `instruction`: the task text (current `task_text`).
- `context`: the rolling session context (current `session_context`).
- `prior_errors`: the tail of the previous attempt's error output
  (current `prior_errors`).
- `eliminated`: the list of approaches already ruled out (current
  `eliminated`).
- `project_dir`: the absolute project path.
- `description`: the project description (current `description`).
- `task_label`: the task identifier in PLAN.md (current `task_label`).
- `check_commands`: the project's check command list (current
  `check_commands`).
- `is_bug_task`: whether this task came from BUGS.md (current
  `is_bug_task`).

Note: the attempt counter is not passed in. Mcloop's current
prompt builders (`_build_normal_prompt`, `_build_bug_task_prompt`,
`_build_bug_prompt` in `mcloop/runner.py`) do not inject attempt
number into the prompt; only `prior_errors` (which is non-empty on
retry) signals retry context to the model. Including `attempt` in the
inputs dict would change what the model sees and break parity. The
retry counter remains mcloop's private state.

The workflow file declares these as `external_input`s. Each role's
instruction template references them by name.

### Config schema

Project-local Orchestra config lives at `.orchestra/config.json` in
the consumer project (mcloop's repo). It is not embedded in
`mcloop.json`, because Orchestra is intended to be callable from
mcloop, Duplo, or directly, and burying it in mcloop's config would
couple them.

Structure is nested:

```json
{
  "workflows": {
    "code_edit": {
      "pattern": "single",
      "roles": {
        "editor": {
          "adapter": "claude_code_agent",
          "model": "opus",
          "instruction_template": "templates/code_edit_editor.md",
          "tools": "default",
          "parameters": {}
        }
      }
    }
  }
}
```

For multi-role patterns, each role defined by the pattern (e.g.
`drafter`, `adjudicator`, `editor` for `draft_then_adjudicate`) gets
its own entry under `roles`.

### Role-binding keys

Two role-binding shapes:

- **Text role.** Keys: `adapter` (string, names a registered text
  adapter), `model` (string), `instruction_template` (path or inline
  string), `parameters` (dict, adapter-specific).
- **Edit-agent role.** Keys: `adapter` (string, names a registered
  edit-agent adapter), `model` (string), `instruction_template` (path
  or inline string), `tools` (string: `"default"` or a comma-separated
  override), `parameters` (dict, adapter-specific).

The `adapter` key is explicit and required. `model` alone does not
specify whether this is Claude Code, Codex, an API model, a desktop
bridge, or some other backend, and those have different invocation
contracts. The distinction between text role and edit-agent role is
captured by which adapter is named, plus the `tools` key (absent for
text roles, restricted by adapter implementation; present for
edit-agent roles, defaulting to mcloop's current tool set).

## Step 4: first integration

Wrap the code-agent invocation. Do not replace `run_task`.

Mcloop owns the task loop, workspace setup, git policy, approval
hooks, verification, retry scheduling, Telegram state, and commit
behavior. Orchestra owns only the advisory/edit pattern at the call
site.

### Wrapper interface

The wrapper sits at the code-edit invocation boundary, not at the
whole-task level and not at the raw subprocess level. It corresponds
to "one edit attempt" as mcloop currently understands it.

Signature:

```python
def invoke_code_edit(
    instruction: str,
    context: str,
    prior_errors: str,
    eliminated: list[str],
    project_dir: Path,
    log_dir: Path,
    description: str,
    task_label: str,
    check_commands: list[str] | None,
    is_bug_task: bool,
    model: str | None,
    timeout: int,
) -> CodeEditResult:
    ...
```

`CodeEditResult` carries:

- `success` (bool)
- `output` (str)
- `exit_code` (int)
- `log_path` (Path)
- `changed_files` (list[str], if mcloop currently observes it or can
  cheaply compute it)
- `summary` (dict, when produced by Orchestra; absent for the direct
  backend)

Two backends implement `invoke_code_edit`:

1. **`direct`**: the current behavior. Lifts the body of mcloop's
   existing `run_task` (the prompt build, `_build_command`,
   `_run_session`, `_write_log` sequence) into a function that
   produces a `CodeEditResult`. No subprocess-level changes.
2. **`orchestra`**: calls `orchestra.run_workflow("code_edit",
   inputs={...}, config=...)` and adapts the `WorkflowRunResult` into
   a `CodeEditResult`.

Mcloop's `run_task` is modified minimally: it still owns timeouts,
rate-limit detection, retry loop, success classification, session
context updates, etc. The only change is that the inner edit
invocation goes through `invoke_code_edit` (selected by config)
instead of inline subprocess work.

### Default config and parity

The default `.orchestra/config.json` (or its absence) maps `code_edit`
to the `single` pattern with the current model, preserving
zero-regression fallback.

The `single` pattern must be a thin wrapper over the existing Claude
Code invocation. It must not change prompt construction, working
directory, approval handling, environment variables, timeout
behavior, or output handling.

Step 4 includes a parity test: the `direct` backend and the
`orchestra` backend invoking `single` must receive the same
instruction and context and produce an equivalent mcloop-visible
result shape (same control-flow interface, not necessarily
byte-identical agent behavior). The test compares `CodeEditResult`
field-by-field on a representative task fixture, allowing
non-determinism in `output` content but requiring matching `success`,
`exit_code`, and `changed_files`.

After parity is established, change the project config to
`draft_then_adjudicate` with chosen role bindings. Compare runs
against the `single` baseline.

## Step 5: second integration

Same wrapping shape inside `run_bug_verify`. Default config =
`single` (current behavior). Switch to `draft_then_adjudicate` or
`propose_critique_synthesize` once the first integration is stable.

## Step 6: stop

Do not integrate the other four sites yet (crash diagnosis, post-fix
review, bug-fix model escalation, reviewer triage). The first two
prove the pattern. The rest get done after empirical evidence on
which configurations actually help.

## What this requires of orchestra

This adds adapters, packaged workflow lookup, and the Python library
entry point. It introduces no new execution semantics. The executor,
store, log, and replay machinery from slice 1 stand as-is.

Concretely:

- A text-role adapter and an edit-agent adapter (Step 2).
- Three workflow files in a library directory (Step 1).
- The `WorkflowRunResult` dataclass.
- The `orchestra.run_workflow` Python entry point.
- Workflow-name resolution from package and project-override
  directories.
- Summary construction from the final envelope and artifacts.

## Empirical loop

After each integration, run mcloop on real tasks with config A vs
config B. Diff the run summaries. Promote the winner. The library of
patterns grows from this loop, not from speculation.

## Handoff to Claude Code: implementation order

Code can implement these unsupervised:

1. Step 2: the two real adapters at
   `orchestra/adapters/claude_code_text.py` and
   `orchestra/adapters/claude_code_agent.py` against the existing
   adapter interface. Lift relevant subprocess and output-capture
   patterns from `mcloop/runner.py`.
2. Step 3: the `WorkflowRunResult` dataclass, the
   `orchestra.run_workflow` entry point, the workflow name resolution
   logic (package directory plus project-local override), and the
   config schema loader.
3. Step 1 workflow files: `single.orc`,
   `draft_then_adjudicate.orc`, `propose_critique_synthesize.orc`.
4. Step 4 parity test (without wiring into mcloop yet). The test
   should construct a `CodeEditResult` from both backends on a
   representative task and assert structural equivalence.

Code stops and reports after item 4. The human reviews the parity
test output. Wiring Step 4 into mcloop (the `invoke_code_edit`
wrapper and the `direct`/`orchestra` backend split) happens after
that review, in mcloop's own repo.
