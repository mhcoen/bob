# Local macOS dictation design example

[SOFTWARE_DESIGN.md](SOFTWARE_DESIGN.md) records the accepted design for the
application specified in [SPEC.md](SPEC.md). [PLAN.md](PLAN.md) contains the
complete implementation sequence, with 18 phases and 191 unchecked tasks.
Each phase carries the accepted design's digest and decision references.

The application has not been implemented. Recognition quality, resource use and
practical text delivery still require the experiments specified in the plan.
Model acceptance records the reviewers' conclusions. Passing future tests will
establish their assertions under the tested conditions. The assertions themselves
require engineering review against the intended behavior.

The scope comes from the user's historical Superwhisper experiments and current
requirement for unrestricted local use on macOS. [Research notes](ref/RESEARCH.md)
identify candidate runtimes and their documented limits. The historical generated
plans remain reference material.

[Review evidence](evidence/README.md) preserves the rejected proposals and the
corrections leading to acceptance. The final continuation used eight model calls,
including one timed-out author call whose completed patch was recovered from
saved construction data. Local corrections and validation preceded the final
judgments. The run required operator intervention; it does not demonstrate an
unattended invocation completing successfully. The receipt records both call
allowances and the prompt-size adjustment for the complete plan review.

To run a new design exercise, create a separate project directory. From the Bob
repository root, after activating its environment:

```sh
mkdir -p local/local-dictation/ref
mkdir -p local/local-dictation/.orchestra
cp examples/local-dictation/SPEC.md local/local-dictation/SPEC.md
cp examples/local-dictation/ref/*.md local/local-dictation/ref/
cp examples/local-dictation/orchestra-config.json local/local-dictation/.orchestra/config.json
cd local/local-dictation
duplo design --plan
```

The command sends the specification and declared references to the configured
model services. Astra (`gpt-6-astra`) authors and judges the design through Codex;
Fable reviews through Claude. Astra authors the whole implementation plan and
Fable reviews it once. The command does not build the application.

Keep `.duplo/software-design.json`, `.duplo/design-plan.json` and the review-run
artifacts with the working project. Re-running the command reuses a current
accepted design and can recover a saved plan candidate. Call allowances persist
across invocations. [Workflow documentation](../../packages/duplo/SOFTWARE-DESIGN.md)
and [review limits](../../packages/duplo/REVIEW-COSTS.md) describe recovery and the
limits of the available cost controls. CLI call counts and submitted prompt bytes
do not bound internal model activity or establish a token total.

The separate [XPC probe](probes/xpc-isolation/README.md) records its tested OS and
observed isolation behavior. Its findings were excluded from the model inputs.
