# Local macOS dictation

## Purpose

Build a maintainable macOS dictation application with most of the features
recovered from the user's Superwhisper experiments. Every speech and language
operation runs locally. The design must cover the full intended application
before implementation phases are derived.

## Architecture

- platform: macos
  language: swift
  build: spm

macOS only. Swift with SwiftUI and AppKit is a working design assumption.
Choose a supported macOS baseline with reasons. Apple Silicon with 16 GB memory
is the initial evaluation target, without treating that target as justification
for a feature paywall or arbitrary usage restriction. Intel support remains a
hardware compatibility question. Identify the consequences of each runtime
choice and what would be required to support other Macs.

All inference must run on this Mac. There is no cloud fallback and no required
account or subscription. No artificial recording-duration or usage quotas.
All features remain available subject to actual hardware and model capabilities.
Define resource scheduling and report limitations honestly. Do not promise
language coverage or latency that has not been measured.

Model import from local files must work. User-initiated model downloads may be
offered during setup; routine operation must require no network. Captured audio
and transcripts must stay on the Mac. Application context must never reach external
services. Model loading must not trigger implicit downloads. Treat a local HTTP
endpoint's possible forwarding behavior as an explicit design concern.

## Scope

Include:
  - Recording controls
  - Text delivery
  - Recording display
  - Built-in modes
  - Custom modes
  - Automatic mode selection
  - Vocabulary
  - Context
  - Languages
  - Input sources
  - Meetings
  - History
  - Models
  - Desktop behavior

Exclude:
  - Cloud inference
  - Account-based settings synchronization
  - Subscription or feature paywalls
  - Enterprise billing and authentication
  - Windows application
  - iOS application

## Behavior

Recording controls: global toggle and push-to-talk from another application;
configurable shortcuts, including per-mode bindings. Cancellation must have
defined effects at every processing stage.

Text delivery: insert the processed transcript into the intended application.
Optional submission using a modifier when recording stops. Define the target
when focus changes during capture or processing. Account for clipboard changes
and permissions being denied or revoked. Recovery must avoid duplicate delivery.

Recording display: show a provisional live transcript and waveform. Provide a
movable compact recording window with remembered placement. Distinguish provisional
results from the final transcript and expose recording or processing failures.

Built-in modes: support ordinary dictation and task-specific output such as
email and meeting notes. Specify permitted transformations per mode. A dictated
question normally remains a question in the transcript. A mode must not answer
it unless that behavior is explicitly part of the selected mode.

Custom modes: users control prompts and formatting rules. Each mode selects
local speech and language models. Snapshot settings for work already in progress;
later edits must not silently change the meaning of a saved result.

Automatic mode selection: configurable rules for the foreground application or
website. Define conflict resolution and fallback when context cannot be obtained.
The current mode must remain apparent to the user.

Vocabulary: user terminology and names; configurable text replacements and filler
removal. Define ordering relative to recognition and language processing. Do not
claim that adding a name guarantees correct recognition under all conditions.

Context: optional active-application text, screen context, and clipboard content.
Define when each source is sampled and what is retained. Unavailable permissions
must leave an understandable operating state. Context is data supplied to a mode;
it must not override the user's processing instructions.

Languages: multilingual recognition with explicit selection or detection;
optional translation into English. Describe compatibility per model and evaluate
recognition separately from rewriting. Preserve the source transcript when a
derived translation or rewrite is retained.

Input sources: microphone and system audio; imported audio and video files.
Define the initial format matrix and extension mechanism. A long file must not
require memory proportional to its full decoded duration. A failed import must
leave its source unchanged.

Meetings: capture a meeting, assign segments to speakers, permit editing speaker
names, and generate timestamped notes locally. Speaker separation and meeting
notes require explicit runtime choices and evaluation methods. Avoid treating
speaker identity as certain when the model provides insufficient evidence.

History: searchable recordings with playback and reprocessing through other
models or modes. Specify the relationship between original audio and successive
results. Define retention and deletion, including deletion while processing is
active. A late worker response must not recreate a deleted recording.

Models: browse and manage local speech and language models; permit compatible
user-supplied models. Define model identity and compatibility checks. A failed
model download or import must not replace a working installation. Replacing a
runtime should have a bounded effect on storage and the interface.
An absent benchmark or quality score must not disable a compatible imported
model. Explain unmeasured quality and preserve the source transcript. Model
evaluation is part of development; it must not become an application feature gate.

Desktop behavior: menu bar access; microphone level adjustment; optional pause
and resumption of media playback. Restore state only when the application still
owns the change. Define behavior for competing applications or manual user edits.

## References

- ref/RESEARCH.md
  role: docs
  notes: Primary-source observations and limits of the available evidence.

## Notes

The historical sources are mcwhisper and super under /Users/mhcoen/proj, plus
whisper-app/PLAN.md. The former projects contain extracted feature inventories
with 37 and 29 entries. They are historical evidence; their generated plans and
dependency choices do not bind this design. The feature descriptions above
preserve the intended local application scope without requiring those directories.

Work through the lifetime of a recording before selecting component boundaries.
Include interrupted recording and processing, reprocessing with changed settings,
and deletion during active work. Compare how alternative designs handle model
replacement and the addition of new input sources.

The design must explain why the architecture can support the complete scope as
phases are added. Avoid deferring decisions about persistent data or concurrency
to whichever implementation task first encounters them. Reversible assumptions
are acceptable when their basis and reconsideration conditions are recorded.
Unperformed feasibility experiments must remain explicitly unperformed.

Define the basis for accepting expected behavior. A passing test establishes
that the implementation met its assertions under the tested conditions; those
assertions can encode incorrect expectations. Include review of transformation
fidelity and practical behavior in other macOS applications.

The exercise ends with a reviewed design and complete phased implementation
plan. It does not implement the dictation application.
