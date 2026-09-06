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

## Attempt 3

Run `6287eb71b38e`, September 6, 2026. All three judgments requested revision.
The invocation was stopped during the fourth author pass. No proposal was accepted.

| File | Contents |
| --- | --- |
| [Inputs](attempt-003/inputs.json) | Snapshot of the specification and declared references. |
| [First proposal](attempt-003/proposal-1.json) | Design with 38 decisions. |
| [First review](attempt-003/review-1.txt) | Findings about delivery and unrepresentable notes dependencies. |
| [First judgment](attempt-003/judgment-1.json) | Requested revision. |
| [Second proposal](attempt-003/proposal-2.json) | Design with 39 decisions, including retained-context ownership. |
| [Second review](attempt-003/review-2.txt) | Remaining delivery and resource-lifetime defects. |
| [Second judgment](attempt-003/judgment-2.json) | Required corrections for the third author pass. |
| [Third proposal](attempt-003/proposal-3.json) | Design with 42 decisions. |
| [Third review](attempt-003/review-3.txt) | Findings about window publication and shared-cache ownership. |
| [Third judgment](attempt-003/judgment-3.json) | Confirmed contradictions and verification defects. |
| [Receipt](attempt-003/receipt.json) | Completed stages and file digests. |

The second review found that the revised delivery ladder treats every
Accessibility error as proof that no write occurred. It also found that model
copies inside service containers lack a deletion lifetime. The memory protocol
measures speech recognition while leaving diarization and external decoding
outside its long-recording measurements.

The third revision added per-window diarization commits while retaining a recovery
rule that said no partial window output was committed. Its cache path was shared
by content hash, while deletion was specified per installation. The next author
instructions require each protocol to have one definition and each changed rule
to be reconciled with its recovery behavior and verification assertions.
[Additional review notes](../ref/REVIEW_NOTES.md) explain why model leases need
eligibility rules that can operate before any recording exists.
## Attempt 4

Run `c0e0a4093266`, September 6, 2026. The first judgment requested revision.
The invocation was stopped before its next author pass to change the role
configuration. No design was accepted.

| File | Contents |
| --- | --- |
| [Inputs](attempt-004/inputs.json) | Specification and references supplied to this invocation. |
| [Proposal](attempt-004/proposal-1.json) | The complete final document from Claude's response. |
| [Review](attempt-004/review-1.txt) | Codex's findings on installation ownership and other protocols. |
| [Judgment](attempt-004/judgment-1.json) | Requested revisions and the disputed media-resume ruling. |
| [Receipt](attempt-004/receipt.json) | File digests and recovery provenance. |

Claude restarted its JSON document twice after output limits. Orchestra joined
the abandoned drafts to the final document. The parser now recognizes a complete
replacement beginning at a message boundary. Replaying the retained response
produces the same 43-decision document used by the reviewer. The exported proposal
excludes the abandoned fragments; the receipt identifies the original payload.

The review found that the installation smoke test requires a model lease whose
eligibility depends on a later installation commit. It also found uncoordinated
delivery attempts and an undefined transition for resuming interrupted
diarization. The judge confirmed those findings. Its acceptance of a playback
position as sufficient pause-ownership evidence conflicts with the specification;
the next invocation's review notes preserve that objection.

## Attempt 5

Run `4f83b8a08682`, September 6, 2026. Astra's author call was killed by the
ten-minute stream-inactivity timer. No final proposal was produced, and Fable
did not receive a draft to review. The configured total call limit was one hour.

The Codex text adapter now applies its total call limit to the inactivity timer
as well. A missing final-message file returns no proposal. Diagnostic output
remains in the local log; it can contain echoed inputs and tool output.

The failed attempt's proposal field originally contained that diagnostic output.
The field was cleared after the defect was identified. The [receipt](attempt-005/receipt.json)
records the original payload's digest and the correction. Its
[input snapshot](attempt-005/inputs.json) is retained. The next invocation uses
the complete proposal from attempt 4.

## Attempt 6

Run `1b155d9dc144`, September 6, 2026. Astra completed two revisions, each reviewed
by Fable. The first judgment requested revision. The second also requested
revision, but returned its feedback as an array. The workflow requires a string
and rejected the response. No design was accepted.

| File | Contents |
| --- | --- |
| [Inputs](attempt-006/inputs.json) | Specification and references supplied to this invocation. |
| [First proposal](attempt-006/proposal-1.json) | Astra's first complete design. |
| [First review](attempt-006/review-1.txt) | Fable's findings. |
| [First judgment](attempt-006/judgment-1.json) | Required corrections. |
| [Second proposal](attempt-006/proposal-2.json) | Revised design with 43 decisions. |
| [Second review](attempt-006/review-2.txt) | Remaining defects and disputed choices. |
| [Second judgment](attempt-006/judgment-2.json) | Original response, with invalid feedback type. |
| [Receipt](attempt-006/receipt.json) | File digests and the schema error. |

The revisions define capture ownership before database registration and supply
staging leases for installation checks. Unfinished diarization output has a
publication protocol. Remaining findings concern an undefined capture backlog,
the scope of durability claims, and missing ownership for run manifests. The
translation stage also needs a chosen runtime.

The paste-delivery protocol requires manual resolution after each unacknowledged
paste. The next inputs ask the author to compare that interaction cost with a
preselected Unicode-event mechanism, and to distinguish a successful Accessibility
setter from messaging failure. Target compatibility remains untested.

The judge prompt now states the feedback type explicitly. A separate recovery
record preserves the second judgment's original array for revision history.
The original failed record remains intact; neither record grants acceptance.
