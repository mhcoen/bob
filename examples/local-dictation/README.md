# Local macOS dictation design example

The example develops a software design and phased implementation plan for the
local dictation application described in [SPEC.md](SPEC.md). Its scope was
recovered from the user's historical Superwhisper experiments and constrained
by the user's current requirement for unrestricted local use on macOS.

The historical generated plans are not treated as current Duplo results.
[Research notes](ref/RESEARCH.md) identify candidate runtimes and the limits of
the available evidence. Hardware assumptions remain explicit in the specification.

Run the example in a separate project directory so generated state is kept with
its own inputs. From the Bob repository root, after activating its environment:

```sh
mkdir -p local/local-dictation/ref
cp examples/local-dictation/SPEC.md local/local-dictation/SPEC.md
cp examples/local-dictation/ref/RESEARCH.md local/local-dictation/ref/RESEARCH.md
cd local/local-dictation
duplo design --plan
```

The command invokes configured model services. It sends the specification and
reference material for design review, then generates the implementation plan.
It does not build the application. Re-running it reuses a current accepted design
and resumes planning from the first unsaved phase.

Inspect `SOFTWARE_DESIGN.md` alongside the review attempts in
`.duplo/software-design.json`. Each plan phase names the design digest and
decisions it implements. Keep `.duplo/design-runs/` to inspect the review rounds.

The [workflow documentation](../../packages/duplo/SOFTWARE-DESIGN.md) describes
rejection and recovery behavior. Scripted regressions exercise the state machine;
they do not establish the quality of a model-generated design.

The [recorded attempt](evidence/README.md) contains two model-generated proposals
and the review of the first proposal. Its judge requested revision. The second
proposal exposed an Orchestra bug: Claude continued a long response after its
output limit, and the adapter supplied only the last fragment to the reviewer.
The complete proposal was recovered from the retained stream. It remains unaccepted.

The response parser and conflicting role instructions have been corrected.
Retrying now preserves the prior proposal and its objections. The next run is
blocked by automatic approval review, which requires separate authorization for
Claude and Codex to reuse their generated drafts and reviews. Permission for the
specification and public research notes has already been given. No implementation
plan has been generated.
