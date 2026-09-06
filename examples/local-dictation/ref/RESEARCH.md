# Runtime research notes

Primary-source inspection, 2026-09-06. No application benchmarks were run.
Use these observations to compare candidates; they do not establish that a
runtime meets the application's accuracy or resource requirements.

Apple's ScreenCaptureKit material describes capture of system audio and screen
content. The WWDC24 additions include a separate microphone stream output.
The design must check API availability against its chosen deployment target;
earlier systems may require a separate microphone capture path.
[Apple session and sample references](https://developer.apple.com/videos/play/wwdc2024/10088/).

WhisperKit is distributed within Argmax's Swift package, which also provides
SpeakerKit for local diarization. The repository lists macOS 14 and Xcode 16
among its prerequisites. SpeakerKit documents a model-folder configuration that
skips downloading; its default initialization may download models. Integration
must explicitly control acquisition to meet this application's offline contract.
Model licensing and the suitability of long-recording behavior need examination
before choosing a distribution.
[Argmax repository](https://github.com/argmaxinc/argmax-oss-swift).

whisper.cpp provides a C-style speech-recognition API and supports macOS on
Intel and Apple Silicon. It documents CPU inference and Apple GPU acceleration.
Its command-line example has input-format constraints, so the application needs
an explicit decoding boundary. Evaluate its integration cost against the Swift
package alternative; the advertised platform support does not establish matching
performance across Macs.
[whisper.cpp repository](https://github.com/ggml-org/whisper.cpp).

llama.cpp provides local language-model inference with Metal support on Apple
Silicon and CPU implementations. Its repository includes both library-oriented
integration material and server tooling. Compare an embedded worker with a local
service, including process ownership and whether network access is possible.
Speech recognition and text generation need separate capability contracts.
[llama.cpp repository](https://github.com/ggml-org/llama.cpp).

Latency and recognition quality require measurements on identified hardware
with a documented model revision and representative audio. Speaker attribution
needs its own evaluation. Documentation alone does not resolve those questions.

## Follow-up inspection after the first design review

Apple documents separate sandboxes for bundled XPC services. A service can fail
or be terminated independently of its client; the client must handle connection
interruption and rebuild worker state. Evaluate signed XPC services with network
entitlements withheld for inference. An unsandboxed application shell does not
by itself settle the service's sandbox configuration. A scan for socket symbols
cannot establish that a process lacks network access.
[Apple XPC guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingXPCServices.html).

The WhisperKit model repository identifies its license as MIT. Preserve the
license and revision of each selected model in its installation manifest;
the SDK's license alone does not describe every model a user might import.
[WhisperKit model card](https://huggingface.co/argmaxinc/whisperkit-coreml/blob/main/README.md).

SpeakerKit's `PyannoteConfig` exposes an explicit `download` flag and local
`modelFolder`, with separate segmenter and embedder concurrency settings.
Defaults vary by OS version. The adapter must supply those values explicitly.
The available clustering threshold is not evidence of a calibrated probability
that a speaker label is correct. Represent unavailable confidence as unknown.
[Configuration source](https://github.com/argmaxinc/argmax-oss-swift/blob/main/Sources/SpeakerKit/Pyannote/PyannoteConfig.swift).

The public SpeakerKit model collection has component-specific license references.
Its embedder refers to WeSpeaker; its clusterer refers to VBx. Removing a
proprietary notice from a repository does not establish terms for every asset.
Model distribution needs an inventory of the exact weights and their notices.
Local import and runtime integration can be designed without bundling those weights.
[Embedder notice](https://huggingface.co/argmaxinc/speakerkit-coreml/blob/main/speaker_embedder/pyannote-v3/README.txt),
[clusterer notice](https://huggingface.co/argmaxinc/speakerkit-coreml/blob/main/speaker_clusterer/pyannote-v4/README.txt).

Apple distinguishes an accessibility object's value from its selected text.
For an editable field, setting its value can replace the field's contents.
Dictation insertion needs a selection-aware operation whose behavior has been
checked for that target. Posting a paste event supplies no general acknowledgment
that the target has consumed the clipboard contents.
[Value attribute](https://developer.apple.com/documentation/applicationservices/kaxvalueattribute),
[selected text attribute](https://developer.apple.com/documentation/applicationservices/kaxselectedtextattribute).

## Evidence from the second review

SpeakerKit v1.1.0 exposes `speakerCentroidEmbeddings` in `DiarizationResult`, with
cosine-distance helpers for comparing centroids. A speaker can have no centroid;
the SDK supplies no universal threshold for treating a match as the same person.
The result declaration does not establish per-segment confidence or margin outputs.
Keep unavailable evidence distinct from measured values. No diarization experiment
was run during this inspection.
[Versioned API source](https://raw.githubusercontent.com/argmaxinc/argmax-oss-swift/v1.1.0/Sources/SpeakerKit/DiarizationResult.swift).

## Evidence from the third review

`AXUIElementSetAttributeValue` can return `kAXErrorCannotComplete` for a messaging
failure. The header documentation includes an unresponsive application among the
causes. Such a result does not supply a guarantee that no write occurred. Treating
it as an uncertain outcome is an engineering consequence of the missing guarantee.
[Setter documentation](https://developer.apple.com/documentation/applicationservices/1460434-axuielementsetattributevalue?changes=_5&language=objc),
[AXUIElement header](https://developer.apple.com/documentation/applicationservices/axuielement_h).

A CAF packet table includes the number of packets and valid frames, with counts
for priming and remainder frames. Packet byte sizes alone do not describe the
exact valid duration. A recovery journal that deletes the source PCM must retain
enough information to reconstruct the table and trim decoded output correctly.
[Apple CAF specification](https://developer.apple.com/library/archive/documentation/MusicAudio/Reference/CAFSpec/CAF_spec/CAF_spec.html).

The existence of playback-change callbacks does not establish that every
transition reaches an observer. For example, mpv documents that property changes
can be coalesced so only the last change invokes the callback. The proposed Music
and Spotify adapters need separate evidence about their notification semantics.
[mpv property observation](https://mpv.io/manual/stable/#lua-scripting-mp-observe-property).
