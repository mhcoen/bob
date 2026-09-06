# Review of d16f5186f5a9, proposal 1

The proposal contains 35 decisions and is not ready for implementation planning.
The findings concern that revision. Check them against each subsequent proposal;
retain the specification's requirements when assessing a judge's recommendation.

D-009 treats no observed target change within 500 ms as paste rejection. A delayed
consumer can paste after that interval, so falling through to synthetic typing can
insert twice. Value length can also stay constant when a selection is replaced
with text of the same length. An unrelated edit can change the value. A target
needs an explicit supported acknowledgment for confirmed delivery; otherwise the
outcome remains uncertain and must not trigger automatic fallback or submission.
Clipboard restoration after a guessed 750 ms has the same delayed-consumer race.
Keep the transcript on the clipboard until a confirmed consumption or user action
allows restoration. Preserve clipboard ownership checks.

D-025 acknowledges that a device-running flag cannot identify a player or confirm
a pause, yet retains automatic resume using that flag and elapsed time. Opting in
does not satisfy the specification's ownership condition. Use a player adapter
that can identify the affected player and observe its state transition. For an
unsupported player, omit automatic restoration. A global toggle can start a paused
player while some unrelated client keeps the output device active.

D-001's assumption that the specification excludes Intel is false. The specification
names an evaluation target and asks for hardware compatibility consequences. A
first-release limitation needs an explicit engineering reason and a stated decision.
Do not attribute it to the user.

The new blocking question about App Store distribution is unnecessary. Direct
local distribution can be a working assumption under the user's instruction to
complete the design without routine questions. No instruction requests App Store
distribution. Record its consequences and a condition for reconsideration.

D-030 needs a durable ordering for the encoded audio bytes before committing the
packet index and deleting PCM chunks. An fsynced index cannot make unflushed audio
recoverable. Specify the index record format and how recovery rejects a torn tail.
The sealer's claimed 30-second backlog also needs a scheduling rule; an occupied
batch lane cannot be assumed to keep draining capture data.

D-007 omits cancellation during sealing, despite the requirement for every stage.
D-033 requires a recording row before acquiring an artifact lease, while capture
creates that row only after sealing. Specify how capture obtains ownership and
how cancellation or deletion is represented before the History row exists.

D-012 makes multilingual rewriting depend on a validator pack, with an opt-in
bypass. The specification permits compatible imported models regardless of absent
quality measurements. Mode checks may report observed violations; pack coverage
must not become an unrelated restriction on model use. Treat this as a requirement
to check in the mode editor and processing flow. The proposed lexical
thresholds do not establish semantic fidelity and can reject valid translations
or summaries. Keep the original transcript and state the limits of each check.

A design must define expected transformation behavior and examine counterexamples.
It cannot claim that lexical checks establish semantic fidelity. Requiring a
runtime proof for every possible utterance would also be unsatisfiable. Define
the consequence of an observed violation and make the scope of each check explicit.

D-028's fallback to an unsandboxed worker weakens the chosen isolation contract.
Apple's XPC guide, cited in RESEARCH.md, describes independent service sandboxes.
Select the service boundary and specify the intended model-access mechanism.
Model-loading compatibility remains development work against that arrangement.
Failure of one access method must not silently relax the offline contract.

## Follow-up on the independent review

SpeakerKit exposes centroid embeddings in its v1.1.0 DiarizationResult API.
The public declaration includes speakerCentroidEmbeddings and cosine-distance
helpers. Some speakers may lack a centroid. The SDK defines no universal threshold
for cross-run speaker identity. This supports a design that retains uncertain
matches for user confirmation; it does not establish per-segment confidence or
margin outputs. The adapter must represent unavailable evidence explicitly.
Source: https://raw.githubusercontent.com/argmaxinc/argmax-oss-swift/v1.1.0/Sources/SpeakerKit/DiarizationResult.swift

The code was inspected; no diarization inference experiment was run. The independent
review's claim that centroid access lacks supplied evidence can be resolved with
this source. Its other provenance and resource-accounting objections still apply.

## Review of 6287eb71b38e, proposal 3

The third judgment requests revision. Its confirmed findings apply to the next
proposal. The following additional findings concern that same revision.

D-033 requires an existing, untombstoned recording for every lease. D-040 applies
that protocol to model caches. Model installation and its smoke test can occur
before any recording exists. Live preview also uses a model before the recording
row commits at sealing. Session-directory ownership cannot establish ownership
of a shared model cache.

Give model use its own eligibility rule, keyed to the installation and the
physical assets it pins. A recording lease checks the recording generation; a
model lease must also be usable by installation checks and pre-capture work.
State how acquisition excludes concurrent archival or removal. An active model
user must prevent removal of its blob and service cache.

A cache path keyed by service and setHash can be shared by several installations.
Archiving one installation must not remove a cache or blob still needed by
another. Retain a shared physical artifact while any qualifying reference or
active use exists. An in-progress copy needs an owner before its completion
marker and registered row exist; a sweep must not delete that live copy.

D-023 and D-042 commit segment rows per analysis window. The recovery rule says
no partial window results are committed. State where unfinished window output
lives and how it becomes a completed diarization result. A run-owned staging
artifact can be written incrementally and published in a final transaction;
cancellation removes it. Output storage may grow with the meeting while
decoded-audio windows and inference working memory remain bounded. Define the
cluster state retained across windows and its storage lifetime too.

The repeated definitions are causing contradictory revisions. Give each protocol
one authoritative location and reference it elsewhere. Keep the complete design
for this example within roughly 12,000 words by removing repetition and draft
history. Preserve every required behavior and the reasons for consequential
choices. A shorter document still needs exact ownership and recovery rules.
