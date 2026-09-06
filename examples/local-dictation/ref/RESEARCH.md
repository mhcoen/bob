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
