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
mkdir -p local/local-dictation/.orchestra
cp examples/local-dictation/SPEC.md local/local-dictation/SPEC.md
cp examples/local-dictation/ref/*.md local/local-dictation/ref/
cp examples/local-dictation/orchestra-config.json local/local-dictation/.orchestra/config.json
cd local/local-dictation
duplo design --plan
```

The command invokes configured model services. It sends the specification and
reference material for design review, then generates the implementation plan.
The example configuration uses Astra (`gpt-6-astra`) through Codex to author
and judge, with Fable reviewing through Claude. The Astra model ID is explicit;
Bob's general `codex` alias still selects GPT-5.6 Sol.
It does not build the application. Re-running it reuses a current accepted design
and resumes planning from the first unsaved phase.

Inspect `SOFTWARE_DESIGN.md` alongside the review attempts in
`.duplo/software-design.json`. Each plan phase names the design digest and
decisions it implements. Keep `.duplo/design-runs/` to inspect the review rounds.

The [workflow documentation](../../packages/duplo/SOFTWARE-DESIGN.md) describes
rejection and recovery behavior. Scripted regressions exercise the state machine;
they do not establish the quality of a model-generated design.

The [recorded attempts](evidence/README.md) preserve rejected proposals with their
reviews. The first attempt exposed a truncated-response bug in Orchestra and
conflicting role instructions. Both have been corrected. The second found missing
meeting provenance and unsafe delivery confirmation. Its judge also waived the
specification's media-ownership condition, so the condition was clarified before
another invocation. [Review notes](ref/REVIEW_NOTES.md) accompany the new inputs.

The third attempt exposed contradictory definitions retained across revisions.
The authoring instructions now require each protocol to have one definition.
Review continues with the completed judgments and additional model-lease findings.
The fourth attempt exposed JSON restarts after output limits. Orchestra now
recovers the complete replacement document. Its review also identified a staging
lease that could not be acquired during installation and overlapping delivery
attempts without a shared executor. The next invocation uses Astra as author
with Fable reviewing, and retains the findings as revision inputs.
No implementation plan has been generated. Model outputs in the evidence directory
retain their wording.

The first Astra call reached the stream-inactivity limit without a final response.
The Codex text adapter now uses the total call limit for that timer too. Its
diagnostic output is excluded from proposals. Review continues from the last
complete design with Astra authoring and Fable reviewing.

The next invocation completed two Astra/Fable review rounds. Its second judgment
requested revision but failed schema validation because feedback was an array.
The prompt now specifies a string. [Attempt 6](evidence/README.md#attempt-6)
preserves the response and the remaining findings. Design review is continuing;
the implementation plan remains unfinished.

The [XPC probe](probes/xpc-isolation/README.md) checks whether a bundled inference
service can deny network access independently of its containing app's sandbox
status. Its recorded results state the tested OS and the limits of the check.
