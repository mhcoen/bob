# GLM and Luna coding trials

## GLM 5.3

On September 6, 2026, GLM 5.3 completed a Swift implementation task through
McLoop's Orchestra editing backend. The requested model was `z-ai/glm-5.3`.
OpenRouter recorded the served model as `z-ai/glm-5.3-20260816`.

The task implemented `ownedSegments`, which clips timestamped speech segments
to a half-open ownership interval. Eight tests were written before the run.
They checked clipping and boundary exclusion, validation of invalid segments
outside the window, stable ordering with duplicate entries, empty input and
text, preservation of input, and behavior at `Int64.max`.

The stub compiled and failed those assertions. After implementation, all eight
tests passed. An independent rerun also passed, and file hashes confirmed that
the package manifest, specification and fixed tests were unchanged. McLoop
committed the implementation as `f6691be` in the temporary trial repository and
marked its task complete.

The first launch failed before editing because Orchestra did not recognize the
GLM provider. Its reported token usage was zero. Adding `z-ai/` routing in both
McLoop and Orchestra allowed the next launch to reach GLM. The routing regression
run completed with 197 tests passing.

The successful session took 113.99 seconds and made eight API requests.
OpenRouter's generation records report 68,024 input tokens, including cached
input, and 4,636 output tokens. Their total charge was $0.04712256. The CLI
reported $0.185588 with an unknown pricing basis, so its estimate was not used
for the billing result.

The trial used one coding session with a 180-second timeout and a $0.50 CLI
budget. It disabled custom CLI integrations and external notifications. Git
pushes went to a local temporary bare repository. The CLI budget uses its cost
estimate and does not establish an exact provider billing cap.

The working files and local logs are under `/private/tmp/bob-glm-coding-trial`.
The trial establishes that GLM can perform this task through McLoop and meet
these assertions. It does not establish performance on the dictation application
or a ranking against other coding models. The configured fallback order was
unchanged by this trial.

## GLM 5.3 Flash and GPT-5.6 Luna

Later on September 6, 2026, both models completed the same Swift task through
Claude Code and McLoop's Orchestra editing backend. Each started from the
original stub in a separate temporary repository with identical fixed tests.
Each received one coding session, a 180-second timeout and a $0.50 CLI budget.
The sessions ran concurrently. No model retries or manual code corrections
were used.

| Requested model | Served model recorded by OpenRouter | Session time | API requests | Actual charge |
| --- | --- | ---: | ---: | ---: |
| `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash-20260826` | 138.271 s | 8 | $0.003535205 |
| `openai/gpt-5.6-luna` | `openai/gpt-5.6-luna-20260709` | 43.823 s | 7 | $0.006589460 |

Charges are the sums of OpenRouter's generation records. The CLI reported
$0.247381 for Flash and $0.1525915 for Luna; those estimates were not used as
billing results. Flash used 80,395 input tokens, including 63,232 cached tokens,
and 5,198 output tokens, including 3,437 reasoning tokens. Luna used 53,479 input
tokens, including 42,148 cached tokens, and 2,429 output tokens, including 264
reasoning tokens. These charges reflect the providers and rates at trial time.

Both implementations passed the eight fixed tests, including independent reruns.
File hashes confirmed that neither model changed the specification, package
manifest or fixed tests. McLoop committed Flash's implementation as `2b2139b`
and Luna's as `24abdb6` in their respective temporary repositories. Both CLI
sessions reported success with no permission denials. Luna expanded the source
manifest to 146 lines, which was excessive for this function. Flash kept it to
three lines.

Before either model was invoked, McLoop's preflight push failed because the
temporary repositories lacked tracking branches. The local upstreams were
configured and the completion receipts reconciled before the coding sessions
started. All pushes remained within local temporary bare repositories.

The results establish that both models can complete this task through Claude
Code and McLoop. The tested assertions cover particular behaviors and do not
establish general implementation quality. Luna was faster in this run, but a
single task does not establish a performance ranking. The trial made no changes
to the configured fallback order.

Local evidence is under `/private/tmp/bob-low-cost-trials`: `results.json`, the
per-model `*-usage.json` billing records and `*-verified.log` test results, plus
the `glm-flash` and `luna` trial repositories. These temporary files are not
part of Bob's repository and may be removed by the operating system.
