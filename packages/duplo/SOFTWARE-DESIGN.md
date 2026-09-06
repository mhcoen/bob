# Software design before planning

Duplo reviews a software design before generating a new roadmap or its
implementation phases. The design describes component responsibilities and
the lifetime of data. Consequential choices carry alternatives and reasons for
the selected approach, including maintenance costs and conditions for revisiting
the decision.

## Running the workflow

For a specification with named requirements under `Scope`, run:

```sh
duplo design
```

The command reads `SPEC.md` and its declared references. A scope list uses
indented entries:

```markdown
## Scope

include:
  - Recording controls
  - History
```

Use `duplo design --plan` to generate the complete phase sequence in one author
call and review it in one independent call. The command validates the canonical
plan and scope coverage before review. It publishes accepted plans through the
planfile API. A rejected candidate and its findings remain in
`.duplo/design-plan.json`; no partial plan is published. An interrupted review
can reuse its saved candidate after an explicit new allowance. A pre-existing
plan without a matching receipt is preserved and refused.

Ordinary `duplo` runs use the same design stage after feature extraction and
before new roadmap generation. Gap additions also require a reviewed design.
Existing unfinished implementation tasks retain their execution contract;
design review does not silently rewrite previously authored tasks. Explicit
`fix` and ledger-driven `reauthor` retain their separate repair workflows.

## Review and evidence

An existing proposal enters review directly, including a rejected proposal from
an earlier invocation. A new project needs one author call first. The reviewer
returns findings with stable IDs and concrete supporting scenarios. The judge
assigns each finding a disposition and explains it. Unsupported objections can
be dismissed.

If correction is required, the author submits one patch naming the candidate's
digest. Unchanged decisions remain intact. The follow-up review receives the
changed decisions and contracts connected by explicit references, with global
sections retained. Section-wide findings retain all decisions. The final judge
receives the full candidate to assess effects across components. Reopening a
settled finding requires a new finding with supporting evidence.

Each invocation allows at most one correction and one follow-up review. A new
design therefore uses at most six model invocations; an existing draft uses at
most five. Publication still requires an accepting judgment with every required
check satisfied, valid decision references, and no unresolved blocking questions.
An exhausted allowance leaves the design unaccepted.

The review examines architectural reasoning and coverage of the requested
behavior. It also examines failure handling and the effect of future changes.
Assumptions must state their basis and the conditions for reconsidering them.
An unperformed experiment must remain identified as unperformed.

Model acceptance records a judgment. It does not establish that the design is
sound. Verification includes examining whether expected behavior is appropriate;
passing tests can confirm an implementation against incorrect expectations.

The project retains these artifacts:

| Path | Contents |
| --- | --- |
| `SOFTWARE_DESIGN.md` | Readable projection of the accepted design, including decisions and review. |
| `.duplo/software-design.json` | Versioned attempts with input snapshots, design digests, and final judgments. |
| `.duplo/design-runs/<run-id>/` | Orchestra logs and model artifacts from each invocation. |
| `.duplo/design-plan.json` | Complete plan candidate with its review and design digest. |
| `.duplo/review-budget.json` | Call reservations and responses, grouped by allowance. |
| `PLAN.md` | Canonical phases with the design digest and applicable decision IDs in their prose. |

Keep the evidence directory if the design or plan will be shared for review.
Generated design files are not committed or pushed by `duplo design`. The usual
Duplo pipeline retains its existing artifact-commit behavior.

## Changes and interruption

The input digest covers the specification passed to design, selected requirement
descriptions, and build preferences. It also covers the bytes of `SPEC.md` and
declared reference files, with `local.md` included when present. Feature completion
status does not change the requirement identity.

The design command checks the input snapshot again before publishing the complete plan. Changed
review prompts or schemas also require a fresh review. A changed
specification or reference makes the review stale. Put corrections and additional
constraints in `SPEC.md`, then run `duplo design --refresh`. A new design revision
requires a matching plan receipt. Preserve the existing plan and its receipt
before starting a replacement plan.

Review attempts are recorded before model execution. An interrupted refresh
leaves the previous design available for inspection and blocks reuse as current
review evidence. Re-run the design command to make a new attempt. Failed attempts
remain recorded. Model calls are not resumed midway through a round.
The next attempt receives the latest available proposal and its objections,
including a rejected proposal. Current inputs take precedence. Carrying that
material forward does not accept it. The new attempt starts with review and
uses the remaining call allowance.

`SOFTWARE_DESIGN.md` is generated from the reviewed record. Edits to that file
are detected and preserved; move the desired changes into the specification and
restore the reviewed projection before regenerating. A missing projection can
be restored from accepted evidence. Publication interrupted between saving the
record and updating the projection can also recover a known prior projection.

Project ownership prevents cooperating Duplo processes from publishing together.
It cannot prevent an external editor from writing at the same instant. The
snapshot checks detect observed changes; they do not make the entire project a
transaction or provide signed evidence against deliberate tampering.

## Model configuration

The default software-design actors use `opus` for authoring and judgment, with
`codex` for review. Override the compound role in `.orchestra/config.json`:

```json
{
  "role_bindings": {
    "software_design": {
      "pattern": "software_design",
      "author": {"model": "opus"},
      "reviewer": {"model": "codex"},
      "judge_role": {"model": "opus"}
    }
  }
}
```

The finite sequence replaces `max_rounds` for software design. Existing role
bindings remain usable; their `max_rounds` value does not extend this sequence.
The reviewer must resolve to a different actor from both the author and judge. The current implementation accepts
`claude_code_text` and `codex_text` adapters. Their authentication and service
access must be available. Local operation of the application being designed is
a separate requirement from the services used to develop its design.

## Call allowance

Design and whole-plan generation share a persistent allowance. Defaults are eight
model invocations, 200,000 UTF-8 bytes per submitted prompt, and 800,000 submitted
prompt bytes in total. Each invocation has a 600-second timeout. Responses over
160,000 bytes are saved as evidence and rejected after generation.

Override these values in `.duplo/review-limits.json`:

```json
{
  "max_calls": 8,
  "max_prompt_bytes": 200000,
  "max_total_prompt_bytes": 800000,
  "max_output_bytes": 160000,
  "timeout_seconds": 600
}
```

Reservations are saved before execution. Failures and interruptions consume a
call. Re-running the command uses the remaining allowance. To start another
allowance explicitly, preserving all previous receipts, use:

```sh
duplo design --new-review-budget
```

Changing limits also requires this flag. An interrupted plan review can then
resume with `--plan`. A rejected plan requires correction; starting another
allowance alone only submits its unchanged candidate for review. Preserve the
rejected receipt before authoring a replacement.

These limits count CLI invocations and submitted prompt bytes. Provider overhead,
reasoning, and tool activity are outside the byte count. The response-size check
runs after generation and cannot cap generation cost. The current text adapters
cannot enforce a token cap; setting `max_tokens` fails before provider execution.
No token or monetary savings have been measured for the live example.

Whole-plan generation uses the configured `plan_author` proposer and reviewer,
including the effective project criteria. Without that binding it uses the
design author and reviewer with Duplo's default plan criteria. Existing ordinary
pipeline phase-generation APIs retain their separate workflow; the finite
whole-plan path is `duplo design --plan`.

The low-level roadmap and phase-generation library functions accept an optional
reviewed-design record for composition by other callers. The CLI and pipeline
own the required review and freshness checks. Calling those library functions
alone does not run the preflight workflow.

See the [local dictation example](../../examples/local-dictation/README.md) for
the full input specification and evidence status.
