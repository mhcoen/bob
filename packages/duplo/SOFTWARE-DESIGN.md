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

Use `duplo design --plan` to continue through roadmap generation and the reviewed
phase-authoring loop. It writes all phases to `PLAN.md` through the planfile API.
Re-running the command resumes an interrupted plan from the first unsaved phase
when the saved design and roadmap still match. A pre-existing plan without the
command's roadmap receipt is preserved and refused.

Ordinary `duplo` runs use the same design stage after feature extraction and
before new roadmap generation. Gap additions also require a reviewed design.
Existing unfinished implementation tasks retain their execution contract;
design review does not silently rewrite previously authored tasks. Explicit
`fix` and ledger-driven `reauthor` retain their separate repair workflows.

## Review and evidence

The author proposes a structured design. A different model actor reviews it;
the judge assesses the proposal against the objections. Revision continues
within the configured round limit. The final publication check requires an
accepting judgment with every required check satisfied, valid decision references,
and no unresolved blocking questions. Exhausting the round limit cannot publish
an unaccepted design, even if its JSON is structurally valid.

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
| `.duplo/design-runs/<run-id>/` | Orchestra logs and model artifacts from each review round. |
| `.duplo/design-roadmap.json` | Roadmap receipt used by `duplo design --plan`. Ordinary runs retain roadmaps in `duplo.json`. |
| `PLAN.md` | Canonical phases with the design digest and applicable decision IDs in their prose. |

Keep the evidence directory if the design or plan will be shared for review.
Generated design files are not committed or pushed by `duplo design`. The usual
Duplo pipeline retains its existing artifact-commit behavior.

## Changes and interruption

The input digest covers the specification passed to design, selected requirement
descriptions, and build preferences. It also covers the bytes of `SPEC.md` and
declared reference files, with `local.md` included when present. Feature completion
status does not change the requirement identity.

Planning checks the input snapshot again before publishing each phase. Changed
review prompts or schemas also require a fresh review. A changed
specification or reference makes the review stale. Put corrections and additional
constraints in `SPEC.md`, then run `duplo design --refresh`. A new design revision
does not authorize appending phases to an old roadmap. Preserve the existing plan
and its roadmap receipt before starting a replacement plan.

Review attempts are recorded before model execution. An interrupted refresh
leaves the previous design available for inspection and blocks reuse as current
review evidence. Re-run the design command to make a new attempt. Failed attempts
remain recorded. Model calls are not resumed midway through a round.
The next attempt receives the latest available proposal and its objections,
including a rejected proposal. Current inputs take precedence. Carrying that
material forward does not accept it; the new attempt runs the complete review.

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
      "max_rounds": 4,
      "author": {"model": "opus"},
      "reviewer": {"model": "codex"},
      "judge_role": {"model": "opus"}
    }
  }
}
```

The round limit is between one and six. The reviewer must resolve to a different
actor from both the author and judge. The current implementation accepts
`claude_code_text` and `codex_text` adapters. Their authentication and service
access must be available. Local operation of the application being designed is
a separate requirement from the services used to develop its design.

The low-level roadmap and phase-generation library functions accept an optional
reviewed-design record for composition by other callers. The CLI and pipeline
own the required review and freshness checks. Calling those library functions
alone does not run the preflight workflow.

See the [local dictation example](../../examples/local-dictation/README.md) for
the full input specification and evidence status.
