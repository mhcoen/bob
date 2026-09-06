# Recorded design attempts

## Attempt 1

Run `16561ebc722c`, September 6, 2026. The judge requested revision of the first
proposal. The second proposal was recovered after an adapter error and has not
received a review of its complete text. Neither proposal is accepted.

| File | Contents |
| --- | --- |
| [Input snapshot](attempt-001/inputs.json) | Specification and research supplied to this attempt. |
| [First proposal](attempt-001/proposal-1.json) | Claude's initial software design. |
| [First review](attempt-001/review-1.txt) | Codex's objections to the first proposal. |
| [First judgment](attempt-001/judgment-1.json) | The judge's assessment and reasons for requesting revision. |
| [Second proposal](attempt-001/proposal-2.json) | Complete response recovered from Claude's continuation messages. |
| [Receipt](attempt-001/receipt.json) | Provenance and file digests. |

The first review identified a result model that could not represent repeated
speech recognition under different models. It also found undefined recovery
states and an insertion path that could replace a text field's contents.
The judge confirmed those defects and identified a recording-duration cap that
conflicted with the specification. The second proposal attempts to address the
objections and records five blocking questions.

The review also revealed a prompt conflict. The shared inputs instructed every
role to return the author's schema, so the reviewer encoded its objections as a
design-shaped JSON object. The role prompts now distinguish their output contracts
and the design stage from subsequent implementation planning.

Claude split the second proposal at an output limit. Its final result record held
only the continuation. Orchestra's parser preferred that record and discarded
the earlier text. The fix assembles responses across explicit `max_tokens`
continuations while excluding text from preceding tool turns. Replaying the
retained stream recovers a 168,329-character response with 35 design decisions.

The original pending attempt remains in the working project's state. A separate
recovery record identifies the source log by its digest and keeps acceptance
false. These exported model outputs preserve their original wording. CLI session
logs remain local; they are not part of this evidence directory.

## Attempt 2

Run `d16f5186f5a9`, September 6, 2026. The complete proposal reached the reviewer.
The judge requested revision. The invocation was stopped during its next author
pass because the judge had waived the specification's media-ownership condition.
No design was accepted.

| File | Contents |
| --- | --- |
| [Inputs](attempt-002/inputs.json) | The specification and references used in this invocation. |
| [Proposal](attempt-002/proposal-1.json) | Revised design with 35 decisions. |
| [Review](attempt-002/review-1.txt) | Codex's findings, including incomplete meeting provenance. |
| [Judgment](attempt-002/judgment-1.json) | Requested corrections and the disputed media-resume ruling. |
| [Receipt](attempt-002/receipt.json) | File digests and the reason for interruption. |

The review found a paste-confirmation rule that could insert text twice. The
capture journal could also record durable index entries before the corresponding
audio was durable. Meeting notes did not pin the speaker assignments used to
generate them. The judge confirmed those defects.

The judge dismissed the media-resume finding on the ground that the proposed
ownership proxy was disclosed and optional. The specification requires ownership
before restoration; disclosure does not satisfy that condition. The next inputs
make the condition explicit and record direct distribution as the example's
assumption. [Additional review notes](../ref/REVIEW_NOTES.md) identify the remaining
concerns and supply a versioned source for SpeakerKit's centroid API.

The live run also revealed that Claude's text adapter preapproved read tools
without removing tools inherited from local settings. The adapter now selects its
built-in read tools explicitly and disables inherited MCP servers. CLI verification
reported only `Read`, `Glob`, and `Grep`, with no MCP servers loaded.

The third invocation uses the clarified inputs and preserved review. The local
XPC probe is retained separately; its findings are excluded from the model inputs
after automatic approval review rejected transmitting that material.
