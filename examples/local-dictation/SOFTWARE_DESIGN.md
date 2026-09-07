# Software design

Design revision: bounded-clarified-8f41241ede15
Input digest: 7409a26d549b4c36b987c8cf86547de642a684814f002829a36223c3a0b32de9
Design digest: 51ddecba91f4623c2a2eda86149c6299f653d0c98c19da9c0b8631438e6d56a9

## Purpose

Build a macOS dictation application whose inference runs on the user's Mac. The product specification governs scope. Cloud inference, account synchronization, subscriptions, enterprise administration, Windows and iOS clients are excluded. Actual hardware and model capabilities determine operation without artificial recording-duration or usage quotas. D-001 and D-036 establish deployment and distribution. D-006 defines capture ownership, D-029 defines result provenance, and D-008 through D-010 define delivery. Detailed implementation phases follow design acceptance. No project files are changed.

## Architecture

A live recording first acquires a session owner and bounded capture storage. Mode resolution and context collection proceed independently. Stopping capture seals verified audio and publishes a recording. Imports acquire an owned source copy and decoded audio before publication. Processing consumes immutable manifests and publishes successive results. Delivery operates on a committed result through a separate journal. Reprocessing creates another run. Deletion invalidates publication eligibility before reclaiming artifacts. D-006, D-019 and D-024 through D-033 govern this lifetime.

One SPM package contains Ports, Core, Store, CaptureStore, AudioIngest, InferenceIPC, SystemIntegration, runtime adapters and UI targets. Ports defines application values and capability contracts. Core imports Ports and owns workflow policy. Store implements transactional metadata persistence. CaptureStore owns capture journals and audio manifests independently of SQLite. Platform adapters implement ports using macOS frameworks. Runtime adapters translate application contracts into inference APIs. SwiftUI views observe view models, with AppKit supplying desktop integration. The application shell constructs these dependencies. Core and Ports import no SQLite, AppKit, AVFoundation or inference-runtime types. D-002 records the maintenance costs.

RunActor coordinates one processing run. ResourceScheduler owns admission and preemption under D-004 and D-027. ArtifactBroker serializes eligibility changes, publication claims and reclamation under D-033. DeliveryExecutor owns insertion and app-initiated clipboard mutation under D-010. Store has one database writer. CaptureStore has a dedicated storage executor. Real-time callbacks transfer frames into preallocated storage and signal the writer without allocation, inference, target lookup or database work.

Separate sandboxed XPC services host preview recognition, final recognition, generation, diarization and decoding. DecodeService also supports optional archive compression. Preview and final recognition share adapter code but have separate process identities and resource charges. Services cannot write the library database. D-003 defines file access and output handoff. D-028 defines network isolation. A separate downloader exists only for explicit model acquisition. The app's DiarizationCoordinator performs checkpoint publication and speaker-proposal comparisons outside the UI thread under D-043.

The authoritative port contracts follow. AudioSource emits AudioFrame values from a live or seekable input under byte credit and reports discontinuities. DecoderProvider probes actual container and codec data, enumerates tracks and supplies a seekable AudioSource. SpeechRecognizer accepts bounded audio windows and returns timestamped segments with optional language evidence. Diarizer accepts one analysis window and returns local clusters, intervals and optional centroids. TextGenerator accepts bounded instructions and data, then emits credited text batches. RuntimeCapabilityDescriptor declares accepted assets, hardware support, parameter ranges, language-evidence policy and resource charges. ContextProvider returns a sampled representation or a typed omission. TextInserter compiles a DeliveryPlan from a TargetProfile or UserRoutePreference and executes it under D-009. Store supports preparation and publication transactions. LeaseBroker returns opaque handles for D-033's ownership scopes. Platform ports expose permissions, shortcut events and identified-player capabilities.

IPCEnvelope contains protocol version, job ID, execution epoch, input hashes, message kind and payload length. Responses echo execution identity. AudioFrame contains stream ID, sequence, sample format, frame count, host timestamp, clock domain, discontinuity and payload length. Receivers validate lengths before allocation. Audio messages contain at most two seconds, with four outstanding messages per stream. Text and metadata batches are at most 64 KiB. Large artifacts use read handles. Credit returns after consumption or durable staging. A disconnected connection loses authority to complete outstanding requests. D-042 governs memory retained inside adapters beyond transport queues.

WhisperKit, llama.cpp and SpeakerKit are the selected runtime families under D-020, D-021 and D-023. Translation uses GenerationService under D-017. The application permits one live capture, with separate dictation and meeting controls defined in D-026. Meeting source selection and analysis are defined in D-023. New input sources and replacement runtimes reuse the existing ownership and publication contracts.

## Data lifecycle

The library resides in Application Support/LocalDictation. SQLite holds metadata and searchable text. Sessions/<sessionID> remains the permanent owner directory after recording publication. It contains capture journals, audio representations, copied import sources, manifests, retained context and run staging. Models contains content-addressed asset sets and installation notices. Service containers hold registered materializations and scratch. Records use paths relative to enumerated roots. Default filesystem permissions restrict access to the current user. At-rest protection otherwise depends on macOS storage protection and the user's disk-encryption configuration. No telemetry pipeline receives library contents.

The following is the authoritative logical schema. Persistent structures carry schema versions. IDs are UUIDs except identified content hashes. Chronological times use UTC. Audio records native frame counts, sample rates and mappings onto a 48 kHz recording timeline. Timeline conversion does not resample stored audio. Original frame counts remain available for playback and archive verification.

CaptureSeed is an immutable file containing session ID, session kind, owner instance, source configuration, creation time, interruption preference, trigger process identity, explicit mode selection and the trigger's immutable settings registry. Session kind is dictation, meeting or import. Source configuration is independent of transformation mode. Import seeds identify an owned operation without retaining an external access capability. SessionControl is an append-only journal containing sequence, generation, disposition, progress state, payload length and checksum. Dispositions are active, keep_interrupted, discard and published. Active has no final user disposition. Explicit live cancellation commits discard under D-007; the seed's interruption preference cannot override that committed intent. Keep_interrupted requests preservation of verified audio. Discard forbids publication. Published records completed database ownership transfer. D-031 defines recovery use. sessions registers the seed hash, disposition, generation, optional recording ID and owner instance. recordings stores session ID, generation, creation time, duration, audio state, interruption and alignment status, displayed result ID and deletion time. deletion_records retains session ID, optional recording ID and final generation after payload reclamation.

audio_artifacts identifies logical audio by recording, stream role, timeline hash, canonical content hash, availability and active representation ID. audio_representations stores artifact ID, manifest path and hash, representation state and creation owner. States are staging, ready, active, superseded and reclaimed. D-030 owns their transitions and retention effects. RepresentationManifest is the authoritative chunk catalog. Its header identifies the artifact, schema and canonical sample layout. Each length-prefixed entry contains sequence, physical asset key, encoding discriminator, codec revision, decoder parameters, native frame count, sample rate, channel layout, timeline interval, encoded byte length and hash, decoded byte length and hash, and discontinuity. Initial discriminators are pcm_s24le_v1 and lzfse_pcm_s24le_v1. Each entry is independently decodable. A representation can contain either or both encodings. A paged offset index supports seeking. The footer contains entry count, total frames and a digest of ordered entries. There is no authoritative audio_chunks table. Database seek and membership caches are rebuildable. Missing caches never establish that bytes are unreferenced.

source_artifacts identifies a copied import by hash, length, container and selected track, with descriptive external-source metadata. Decoded chunks reside beside that owned source. processing_runs stores recording ID, expected generation, kind, manifest hash, state, optional resume stage, execution epoch, retry reason, start time and optional end time. RunManifest contains exact input IDs, mode and vocabulary revisions, installation IDs, model asset hashes, runtime and adapter revisions, pipeline revision, language policy, parameters, context descriptors and delivery policy. Canonical bytes live at Sessions/<id>/Manifests/<hash>.json and are synchronized before the run row references them. Preview jobs use session ownership and frozen settings without publishing final processing results. LanguageResolution records selected language, evidence and fallback reason as output of the pinned language policy.

prepared_sets identifies a run, execution epoch, owner generation, payload hash and preparation state. prepared_text stores bounded text chunks and offsets. prepared_segments stores segment identity, timing, text range, stream role and optional local cluster ID. results publishes a prepared set with result kind, primary input, disposition and creation time. result_dependencies identifies additional inputs and their roles. Readers expose prepared data only through results. A completed candidate held for review is published with that disposition and remains distinct from incomplete output.

speaker_revisions identifies a recording, diarization result, optional parent revision and content hash. speaker_segments represents edits and splits through source segment identity and frame ranges. speaker_assignments records identity, attribution state and proposal origin. speaker_names freezes display names. cluster_evidence stores stream and window IDs, optional centroid artifacts and overlap summaries. speaker_proposals stores candidate links, distances, ranks, ambiguity and provenance. D-023 defines evidence generation. D-037 defines interpretation transitions.

modes, mode_revisions, vocabulary_revisions and rule_revisions contain versioned settings. D-012 defines ModeContract and CheckReport. context_samples identifies its session or run owner, source, sampling time, representation, status, digest and optional retained artifact. retained_artifacts identifies owner, kind, path, hash and length. UI settings hold panel placement, bindings and preferences independently of run snapshots.

TargetProfile contains immutable ID and revision, evidence status, applicability predicates, preferred route and permitted alternates. Applicability identifies bundle and code-signing identity, evaluated app and OS ranges, editable role, input method and observable editor settings. The profile includes destination identity rules, web-element equivalence, required AX attributes and settable checks, content eligibility, operation-size limits, composed-character chunking, key-event parameters, line-break mapping and submission policy. Line-break mapping is literal_selected_text, verified_key_sequence or unsupported. A verified sequence names exact keys, modifiers and required settings. Evidence records identify fixture coverage and acknowledgment qualification. D-009 defines evaluation status, version drift and route selection.

UserRoutePreference contains ID and revision, bundle and signing identity, editable-role scope, optional website-origin restriction, selected route, optional source profile revision, allowed content class, persistence scope, version-continuation choice and revocation state. Persistent choices survive restart until revoked. One-shot choices belong to a single attempt. User preferences record authorization and cannot confer evaluated status. DeliveryPlan contains result hash, route source and revision, observed app/OS versions, target snapshot, full-content eligibility report and an ordered disk-backed operation sequence. delivery_attempts records result ID, queue sequence, plan hash, target snapshot, requested submission, insertion state, submission state, effect-started status, optional clipboard session and outcome. delivery_operations records text ranges, operation kind, committed intent, whether the API returned, acknowledgment and diagnostic time. TargetSnapshot contains process launch identity, window and element evidence, selection evidence, acquisition status and target policy. ClipboardSession records app instance, original and written change counts, snapshot availability, ownership and barrier state. Clipboard payload snapshots remain memory-only. Retirement records identify acknowledgment or explicit informed resolution. D-010 owns these transitions.

model_staging stores acquisition state, owner instance, proposed set hash, manifest hash, path, error and optional discard deadline. model_installations contains set hash, runtime kind, capabilities, provenance, notices, execution state and historical parameter descriptions. model_assets identifies physical blobs and caches by root, path, hash, size and state. asset_references records owner, asset and reference purpose. publication_claims excludes competing publication or reclamation. leases records scope, owner, physical asset keys or immutable manifest roots, holder instance, process identity, connection and state. service_artifacts registers job scratch, owner, epoch, path and bytes before creation. D-040 defines materialization keys. jobs records execution identity and resource charges. ArtifactBroker maintains a root revision incremented whenever a retention root, lease or creator changes, supporting D-033's reclamation protocol.

The invariants have one definition here. I1: published payloads, producing manifests and dependency identities are immutable. Recognition and diarization depend on audio. Cleanup depends on recognition. Translation and rewriting depend on an identified transcript. Notes additionally identify diarization and speaker interpretation. Manual edits create derived results. I2: publication atomically checks eligible owner generation and current execution epoch. Discarded sessions and tombstoned recordings cannot publish. I3: physical reclamation requires no qualifying durable reference, live lease, registered live creator or publication claim, followed by ArtifactBroker's exclusive deleting mark. Historical metadata, superseded representation rows and idle cache associations do not qualify as retention claims. I4: a published artifact reference resolves to verified bytes committed under D-030's durability scope. Unreferenced bytes may await reconciliation. I5: every delivery effect requires committed intent and DeliveryExecutor ownership. I6: assigned_by_user records direct action on the current speaker revision. Inferred carry-forward cannot acquire that status. I7: working memory has finite configured buffers and spill paths independent of total recording duration. I8: terminal runs have ended_at and cannot resume. Suspended runs have no ended_at.

## Failure behavior

Failures identify the affected stage and preserve eligible committed inputs. D-007 defines cancellation, D-031 restart recovery and D-032 permission transitions. Incomplete preparation never appears as completed output. A failed rewrite leaves its source transcript available. A failed import leaves its external source unchanged.

Worker interruption fences its execution epoch before replacement work begins. Its resource charge remains until process exit or verified release. A watchdog can request termination, but elapsed time cannot authorize artifact deletion. Recognition and generation have no automatic crash retry. An explicit retry creates a new run. Two consecutive crashes with the same installation and parameters mark the installation suspect and offer diagnostics or another model; successful completion resets that counter. Orderly preemption preserves recognition progress under D-020. Missing quality measurements never establish runtime incompatibility.

Capture storage failure follows D-006's outstanding-frame bound. The panel reports storage backlog, a requested stop and the retained prefix when applicable. D-030 identifies the covered durability failures. Context collection, target lookup, model eviction and database preparation cannot gate capture ownership or chunk writing. Disk exhaustion during optional compression leaves the active representation usable. Recovery of imports and live sessions follows their distinct D-031 transitions.

D-009 holds unsupported content before any effect and defines operation outside evaluated profiles. Once insertion may have occurred, D-010 governs uncertainty and recovery of partial effects. Its clipboard-only barrier rule applies during ordinary operation and restart. A user may continue capturing while delivery waits. Permission loss before dispatch holds the result and offers explicit Copy.

D-026 rejects dictation controls during a meeting without altering its audio or mode. The panel identifies the active meeting and its separate Stop control. Resource pressure queues or preempts processing under D-004. An exceeded envelope retains inputs and reports the configuration and observed pressure. Monitoring cannot promise to intercept every allocation spike, so adapter bounds and measured admission estimates are both required. D-012 governs generation findings. D-037 governs speaker attribution. Desktop restoration requires D-025's continuing-ownership evidence.

## Evolution

Design acceptance precedes a detailed phased implementation plan. Dependencies are established here so implementation work does not invent persistence or concurrency policy independently.

Capture ownership and publication support all persistent workflows. Lease eligibility and service materialization precede accepted runtime integration. F2 establishes the selected sandboxed loading arrangement. Long imports and meetings depend on bounded decoding and recoverable audio. Delivery can be evaluated independently using committed fixture results, compiled profiles and user route preferences. Generation review depends on retained source transcripts and mode contracts. Notes depend on completed per-stream analysis and a versioned speaker interpretation. Release packaging exercises the same service boundary as development.

A new AudioSource supplies timing, discontinuity and credit behavior. A decoder extends DecodeService with a compiled provider. Both reuse publication and deletion. A runtime replacement changes its adapter, package dependencies, asset compatibility and parameter translation. Historical presentation uses D-038's stored descriptors. D-040 bounds physical materialization copies. Another model or input format does not introduce another recording lifecycle.

Maintenance includes database migrations, native dependency updates, signing configuration and recovery fixtures. Target profiles require evaluation by application version, field class, editor settings and input method. Browser and Electron updates can change several targets together. Profile releases must carry applicability evidence and known failures. Remembered unverified choices reduce repeated setup while increasing the need for visible status and accessible revocation. They do not extend an old evaluation claim to a new version. F4 measures that operating state.

Audio representation readers require exact decode fixtures. The per-chunk discriminator permits another codec without changing the recording schema. D-030's state-controlled references let storage migration coexist with playback. Speaker proposals create confirmation work measured in F5. Player integrations require control-ownership evidence beyond successful pause calls. Hardware expansion requires D-001's separate evaluation.

Evidence may change a default model, resource estimate or adapter implementation. A failed experiment does not authorize removing a requested capability. If no compliant implementation supplies a required operation, the affected integration remains blocking until resolved. D-034 and D-035 define acceptance consequences. The open questions distinguish measurements of chosen mechanisms from decisions that would otherwise prevent implementation.

Under the single-target alternative in D-002, replacing an inference runtime would touch application scheduling code wherever it retained native handles, parameter types or callbacks. Removing those dependencies would require a port extraction before replacement. Adding a new input source would require coordinating its callbacks with the same application state that owns persistence. Recording records could remain compatible if they already stored application-defined manifests; native serialized results would instead need conversion and migration. With the chosen ports, those changes remain in source/runtime adapters unless the replacement exposes a new capability requiring a versioned port and manifest extension. A single repository still allows those interfaces to change atomically. Separate adapter repositories preserve the same boundary only with versioned compatibility releases, conformance fixtures and coordinated migration support. In-process inference would also move crash and memory failure into capture's process, requiring a new failure policy even if its API looked identical. The chosen process boundary retains capture and committed inputs when an inference process exits.

## Verification

Review specification-derived state/event tables, I1 through I8 and decision contracts before writing assertions. Include persisted effects and visible outcomes for instruction-like questions, delayed targets, unsupported paragraphs, concurrent source edits, storage stalls and deletion during publication. A passing test establishes conformance to its assertions under the tested conditions. Those assertions can encode incorrect expectations. No application benchmark, typing trial, diarization experiment or signing feasibility experiment described here has been performed.

F1 evaluates defaults on identified hardware and OS, with runtime revisions, asset hashes and parameters recorded. Freeze datasets before scoring. Initial evaluation covers English, Spanish and a separate code-switching set without restricting other languages or compatible models. Compare WhisperKit and whisper.cpp using equivalent checkpoints where available, recording conversion differences. Use twenty preselected recordings per language covering short utterances, conversation, terminology and noise. Report word error rate and meaning-changing errors separately. Proposed default criteria are corpus word error rate at most 12 percent and at most 15 percent relative degradation from the comparator. If comparator error is below five percent, allow at most one percentage point of degradation.

Default acceptance also requires at most two correction actions per hundred reference words. An action is a deliberate contiguous replacement or editing command under a frozen rubric. Several word errors can require one action; whole-transcript replacement cannot count as one. Report corrected word counts. Neither criterion establishes semantic fidelity or gates imported models.

Preview targets are a first update within two seconds of speech reaching a warm model, updates at least every three seconds during speech, and final recognition p95 within six seconds after thirty-second captures. Measure trigger-to-first-text during cold launch, preview eviction, repeated dictation within and beyond the speech-model linger, capture during generation and preemption of older recognition, including unloading and loading. A waveform alone cannot satisfy recording display. Paired preview-enabled and disabled recognition must have a one-sided 95 percent upper confidence bound below one percentage point of added word error rate. Test tentative detection differing from final language, explicit selection, fallback and awaiting_language. Verify sequential preview of both meeting streams with bounded retained text.

Use an external audio marker to measure trigger-to-first-admitted-sample and inspect Starting/Recording indicators. The provisional p95 target is 300 ms with permissions granted, idle and under processing load. Report first permission requests separately. Failure requires a startup change or revised interaction proposal before a latency claim.

Run at least twenty thirty-second dictations once per minute while two-hour meeting recognition is queued. Compare shared-model use with a larger meeting model A and smaller dictation model B, each within its admitted envelope. Record exact configurations, preview/final residency, switches, loading/unloading, window completion and meeting completion time. Consecutive B windows reuse B; switching releases prior residency before loading within D-027's charge. Include switch costs in F1 preview and final-recognition latency criteria.

With finite completing windows and continued admission, D-004 must give the oldest waiting run a completed window after at most two latest-live windows across either model. Preserve checkpoints through ordinary preemption. Test epochs, quota-boundary arrivals and oldest-run cancellation. Report elapsed progress and switch overhead alongside window-count fairness. Batching or switch limits remain unrequired without measurements; failure needs a D-035 proposal and reevaluation of latency and fairness together. Worker/application crashes must follow D-031's recognition failure and explicit retry policy.

Generation review uses forty frozen examples per built-in mode and evaluated language. Two reviewers score meaning, factual additions, question preservation and formatting on a written 0 to 2 rubric and adjudicate disagreements. Accept no severe violation scored 0, at most ten percent with a minor meaning or question issue, and at most twenty percent with minor terminology or formatting issues. Include negation, quantities, indirect questions, quoted instructions and malicious context that passes lexical checks. Bilingual reviewers assess translation against its retained transcript through the selected llama.cpp model. Report recognition errors separately. Check whether cited passages support notes; valid ranges cannot establish support. Exercise every D-012 disposition and unavailable-check policy.

F2 exercises each model kind through D-003's signed services: handles, materialization, output handoff and interruption. Attempt worker networking in both directions and unrelated library access. Check downloader separation, peers, malformed messages and disabled acquisition in ad hoc/Developer ID builds. Failure blocks acceptance without an unsandboxed fallback.

F3 interrupts each write, fsync, rename, journal append and publication transaction boundary, including interruption during recovery. Reference PCM defines the verified prefix. Exercise startup during long result preparation, materialization, archival and eviction. Healthy storage must show no resulting leading-audio loss. Inject two-, ten-, thirty- and over-sixty-second write stalls. Stalls clearing before the stop threshold must preserve admitted frames. Inspect buffers and native-frame counters against D-006, including delayed control execution. Fatal storage errors must stop promptly within its outstanding-frame bound. Process-crash evidence covers application interruption only; kernel panic and power-loss claims require separate evaluation.

Check D-030 with known samples at unity, reduced and excessive microphone gain. Verify chunk-boundary timing, coalesced pending edits, clipping counts and conversion revision against specified gain, saturation and quantization. Preview and waveform use effective live gain. Playback and reprocessing never reapply capture gain or consult the current slider. Check unity treatment of system audio and imports, recovery of gain metadata and exact post-gain samples through representation replacement. Demonstrate disclosed permanent clipping. F1/F5 separately review quantization and sample-rate fidelity.

F3 covers thousands of chunks, mixed encodings, unknown codec revisions and torn indexes. Unsupported encoding yields an explicit playback error. Switch representations while playback and recognition lease the old one. Unshared old chunks remain until those leases end, then become reclaimable. Shared chunks remain reachable through the new manifest. Interrupt around the pointer transaction and reclamation. Recovery selects the database-active representation without requiring superseded files. Publication transaction size stays independent of chunk count. Missing seek or membership caches preserve playback and defer uncertain reclamation.

Compare LZFSE and per-chunk ALAC for exact samples, size, CPU, temporary space and seeking, assuming no savings. Test low space throughout replacement and cancellation before publication. Interrupt every import stage; check unchanged external files, owned source/decoded storage, complete-input publication and D-031 recovery. Measure decode-on-demand latency/storage before reconsidering D-019.

F4 evaluates TextEdit, Mail, Notes, Safari, Chrome, Xcode, Terminal, VS Code, Slack, Messages, Figma and an installed application selected before testing. Record versions, field types, settings and input methods. Run at least fifty dictations per route and target, including twenty back-to-back, choosing each route before its attempt. Measure text errors, duplicates, holds, resolution actions and clipboard-blocked time. Inspection before requested submission counts as resolution; count stop shortcuts separately. Routine acceptance permits at most 0.1 additional resolution actions per dictation, excluding and separately reporting one-time setup.

Apply that threshold to remembered unprofiled Unicode, remembered paste and out-of-range profiles continued as unverified. Include restart and revocation. Report single-line and multiline outcomes separately. Paste's unresolved-consumption review will generally prevent routine-default qualification. Before advertising routine support for a failing target, return traces and a concrete integration or behavior proposal to the owner or delegate. Explicit alternatives remain usable with measured costs disclosed. Finite coverage cannot establish universal compatibility.

AX qualification requires settable selected text, reviewed setter semantics and zero success-without-insertion or wrong-selection outcomes across declared fixtures. Include equal-length selection replacement, empty selections, multiline text and every claimed field class. Observe target content independently. Contradictory success disqualifies the profile pending narrower scope or a fix. Readback remains diagnostic. Fixtures support the scoped interpretation without proving universal acknowledgment behavior. Unicode event success never acknowledges consumption.

Test missing/overlapping profiles, version drift, signing changes, known failures, unavailable web identity, send-on-Return settings and secure fields. Preflight all content before effects. Exercise CRLF, lone CR, LF, Unicode separators, tabs and other controls. Unsupported mappings hold with zero effects. Verified break sequences must not submit. Generic remembered Unicode stays single-line without an applicable mapping. Review generic paste's target-specific paragraph interpretation.

Change focus after each operation and crash around invocation and outcome persistence. Posted events remain diagnostic, without consumption evidence. After uncertainty, Deliver here cannot become an unqualified retry. Inspection and an edited remainder create a new result/attempt. A paste barrier blocks clipboard replacement and restoration while independent eligible AX/Unicode work continues. Verify eligible FIFO ordering, all retirement events and restart. Modifier submission follows qualifying acknowledged AX insertion plus separate target/selection checks. Unacknowledged insertion requires inspection.

Return to an edited original field and wait. Held text remains held with zero additional effects. Resume delivery and Deliver here each revalidate selection. Deletion during unresolved paste removes library content while retaining the barrier, discloses the possible clipboard copy and performs no unsafe restoration.

F5 uses consented labeled meetings with overlap, returning participants, silence and speakerphone echo. Evaluate per-stream recognition/diarization, sequential scheduling and merged timelines. Report missed speech, false speech, speaker confusion, boundary error and correction time. Check D-023 ownership intervals and D-043 handoff. Preserve simultaneous speech and trace echo to both streams. Measure proposal precision, ties and confirmations per speaker-hour. Compare windowed SpeakerKit with whole-input analysis at increasing durations; flat measurements support reconsideration alongside inspection for duration-proportional retention. Evaluate voice processing on actual meeting-app routes. Independent timestamp markers have a proposed maximum disagreement of 50 ms over sixty minutes.

For each meeting source combination, exercise dictation toggle, push-to-talk, per-mode shortcuts and missed release. Preserve the meeting session and mode. Start Meeting during an existing session is visibly rejected without creating another. Stop seals once. Discard recording commits discard independently of interruption preference. With keep_interrupted selected, a crash preserves eligible audio and explicit discard removes it. Test the discard/publication race. Notes stay in History until explicit delivery.

Test cancellation disabled, rebound and explicitly bound to Escape. During dictation, use Escape in Slack's emoji picker and other applications' transient controls. Disabled or differently bound cancellation leaves Escape to the foreground application and capture continues. Explicitly bound, eligible Escape consumes the matched key gesture and invokes D-007; the foreground control does not receive it. Unmatched events pass through. Verify key-up pairing, ignored repeats, conflicts, unavailable tap permissions and that dictation cancellation cannot discard a meeting. Menu cancellation remains available.

Restart valid diarization checkpoints into interrupted_resumable with visible Resume and no inference admission or automatic model load. Waiting and repeated launches preserve that state. Resume queues the next checkpointed window under pinned inputs and normal resource admission. Test admission-time eligibility, new epochs, missing assets, invalid tails, deletion while held/queued, cancellation and repeated interruption. Orderly preemption still follows D-004; recognition crashes still require a new run.

Resource verification samples process footprint and attributable accelerator allocation every second, stage-boundary peaks and settled checkpoints every five processed minutes after ten minutes of warm-up. Use ten-minute, one-hour, three-hour and twelve-hour repeated-content inputs with fixed parameters. Three pilot runs estimate centered variance and autocorrelation, excluding fitted growth. Before confirmation, set tolerance M to twice the estimated slope standard error for twenty-four runs, rounded upward to whole MiB per processed hour, minimum 1 and maximum 4. A pilot requiring over 4 triggers better allocation tracing without enlarging the ceiling.

Choose eight, sixteen or twenty-four confirmation runs from pilot precision, then freeze count and M. Use run-level or block-bootstrap intervals respecting autocorrelation. Acceptance requires a one-sided 95 percent upper slope bound below M, no established positive growth, every observed footprint within its admission ceiling and inspection supporting I7. Any identified accumulating structure fails. An unresolved bound is inconclusive and blocks resource acceptance pending better instrumentation or implementation. Tolerance represents uncertainty and permits no known duration-dependent allocation. Finite trials cannot prove unlimited-duration behavior.

State suites cover smoke tests without recordings, identical concurrent imports, live creators during sweeping, shared-cache archival, representation retirement, late responses after deletion and checkpoint cancellation. Search tests include phrases crossing chunks. Platform cases cover settings edits during capture, URL timeout, replacement precedence and display removal. Media tests include intervening actions returning a player to its original position. D-035 governs acceptance.

Screen-context fixtures place unrelated sensitive-looking fixture text beside and behind the selected window on each display. Verify exclusion from pixels and OCR. Test spanning displays with different scales, movement before sampling, closure, minimization, foreground changes, unavailable identity, secure-input suppression and permission revocation. Check the four-megapixel cap, one-second deadline, typed omissions and absence of display fallback. Retained context contains only the supplied representation and source metadata; pixels release after OCR.

F5's provisional ceiling is fifteen percent for summed missed, false and misassigned speaker-time divided by labeled reference speaker-time. Include overlap, each error component and no unreported boundary exclusion. Require at least ninety-five percent precision for top-ranked non-tied cross-window proposals and report proposal coverage. Freeze corpus and scoring before evaluation. Correction time is at most five minutes per meeting hour; identity confirmations are at most two per speaker-hour. Each confirmation command, including a batch, counts once. Segment review counts toward time. Report initial naming and other corrections separately within the time total. One person present for an hour contributes one speaker-hour. A two-hour five-speaker meeting permits twenty confirmations and ten correction minutes; sixty link decisions need not require sixty clicks. These unmeasured criteria govern default-quality claims. Failure requires an alternative window, proposal or runtime design for D-035 review while preserving Meetings scope and compatible imported-model use.

F3 changes gain during injected writer stalls, including a thirty-second backlog. Use a known input waveform and independently record admitted chunk boundaries. Frames admitted before the next boundary must retain the old gain even if written after the slider edit. The first subsequently admitted chunk uses the new gain. Check preview samples and stored post-quantization samples against the same latched gain, including saturation and writer retries. A delayed writer must neither change a chunk's gain nor apply it twice.

Cancellation fixtures select a running operation in History, switch to another application, and press an explicitly configured Escape binding with no live dictation. The operation must continue and the foreground application must receive the gesture. Repeat with this app frontmost, with a live dictation, and with cancellation disabled. Change focus between an eligible key-down and key-up to verify the existing gesture-ownership rule.

## Assumptions

- macOS 14 and initial arm64 packaging follow the selected Argmax package's documented prerequisites and Apple Silicon focus. Intel remains an engineering compatibility question. Reconsider after a complete local stack is evaluated on identified Intel hardware.
- Container-local materialization can support the selected path-oriented runtimes inside independent service sandboxes. The complete signed arrangement remains unperformed. F2 must establish it or require a compliant adapter change.
- A successful operation on a supported, settable selected-text attribute can acknowledge insertion under D-009's scoped qualification. The supplied Apple contract supports that interpretation. Contradictory target behavior invalidates the relevant profile. Messaging failures remain uncertain.
- Remembered Unicode routes can reduce repeated interaction for unprofiled single-line destinations. Coverage and silent-failure rates are unmeasured. D-009 records authorization scope and version drift, and F4 evaluates those states without calling them validated.
- One live capture is sufficient for the initial application. Concurrent dictation during meetings is not specified. Separate controls preserve meetings without adding capture or preview allocations. Reconsider only with an explicit concurrency requirement and revised resource evidence.
- The 60-second capture ring and its thresholds trade additional memory and a larger stalled-crash loss window for tolerance of transient filesystem delays. No stall measurements establish the chosen constants. F3 must evaluate them before capture acceptance.
- The D-027 envelopes are planning estimates. F1 and F5 select exact default configurations and replace estimates with measurements. Default-quality evaluation remains separate from compatible-model use.
- Durability covers application interruption while the OS and filesystem remain operational. fsync does not establish power-loss durability on macOS. Stronger claims require a separately specified flush policy and failure evaluation.
- Native-rate signed 24-bit capture is a reversible storage and fidelity choice. F1 and F5 compare quantization and sample-rate effects before fidelity claims are made. Compression is optional and disabled by default until its cost and benefit are evaluated.
- English and Spanish are the initial evaluation languages because they provide a tractable bilingual test set for recognition, translation and code-switching. They do not limit model selection. Reconsider the development dataset when intended usage supplies another priority language.
- Target, URL and context deadlines are provisional responsiveness budgets. Their basis is preventing optional context work from delaying visible mode resolution. F1 and platform trials must report omission rates before changing them.
- Unavailable semantic checks do not establish safe or unsafe output. Built-ins use prompting and development review with visible missing-check notices. Users may select review before delivery without disabling model execution.
- Speaker centroid and overlap thresholds are uncalibrated proposal heuristics. F5 may change ranking parameters while preserving attribution provenance and unknown confidence.
- No real player currently has established evidence for D-025's conditional-resume contract. Explicit player resumption remains available where control integration is validated.
- The import matrix reflects expected AVFoundation coverage. Baseline fixtures must establish each combination. Failure requires decoder work while audio and video import remain required.

## Decisions

### D-001: Deployment baseline and hardware

Target macOS 14.0 and package arm64 initially. Evaluate first on Apple Silicon with 16 GB memory. Guard later APIs by availability.

The [Argmax repository](https://github.com/argmaxinc/argmax-oss-swift) documents macOS 14 and Xcode 16 prerequisites and focuses on Apple Silicon. That supports this engineering choice without attributing an Intel exclusion to the product specification.

Alternatives considered:

- Require macOS 15: Provides newer capture APIs while excluding macOS 14 without evidence requiring it.
- Ship universal binaries initially: Broadens hardware coverage and adds complete Intel speech, generation and diarization compatibility work.

Consequences: Intel requires whisper.cpp and llama.cpp CPU evaluation plus a compatible local diarizer. Smaller-memory Macs need measured configurations and scheduling adjustments. Limitations are reported as hardware or runtime constraints.

Reconsider when: Dependency availability changes, alignment requires a later API, or Intel evaluation establishes a complete usable stack.

### D-002: Swift package dependency direction

Use architecture's targets and ports. The shell is the composition root. SPM builds reusable libraries and service executables. Packaging constructs application and XPC bundles.

Sources and models need common ownership policy. Framework callbacks belong in adapters so Core behavior can be exercised without permissions or model loading.

Alternatives considered:

- One application target: Reduces wiring while coupling persistent transitions to native-runtime and UI types.
- Separate adapter repositories: Allows independent releases at the cost of additional version coordination.

Consequences: Port changes require adapter conformance work. CaptureStore stays independent of SQLite scheduling. Package revisions and service packaging must be reviewed together.

Reconsider when: A port repeatedly exposes framework details or an adapter needs an independent release schedule.

### D-003: Service storage and artifact handoff

Use independently sandboxed services with container-local model materializations and Jobs/<jobID>/<epoch> scratch. Register scratch and its creator before creation. Pass read handles for inputs. Services return output handles, relative paths, lengths and hashes. The app verifies and copies accepted output into owner staging before acknowledging durable receipt.

The supplied Apple XPC evidence supports independent service sandboxes and interruption handling. Materialization accommodates path-oriented runtimes without library-root access.

Alternatives considered:

- A shared app-group model container: Can eliminate duplicate weights while adding shared-container authority and signing configuration.
- A scoped read-only grant to model blobs: Avoids copies with limited access, but path-loading compatibility and grant lifetime need separate validation.
- In-process inference: Removes IPC and materialization while sharing native crashes and shell privileges.

Consequences: The blob store and both speech services may contain three physical copies of one set. D-040 accounts for the cost. Acknowledged scratch is disposable. Before acknowledgment, interruption can require repeating that work unit. Durable checkpoints use app-owned storage under D-043.

Reconsider when: F2 or measured storage cost favors shared storage or scoped access with equivalent isolation.

### D-004: Scheduling and reservation lifetime

Keep D-027's standing reservation while recording controls are enabled. Capture storage and current preview have priority over final analysis. Schedule final recognition in D-020's complete-window units. An admitted window finishes its output handoff before ordinary preemption; capture pressure can still terminate a worker, with D-031's failure policy.

Preview needs capacity before a trigger. A new recording cannot depend on an older generator releasing memory.

Alternatives considered:

- Reserve only at the trigger: Reduces idle reservation while adding eviction and unloading to preview startup.
- Allow concurrent meeting and dictation capture: Supports chat dictation during meetings while requiring source sharing, separate preview work and revised admission charges.

Consequences: One live session is admitted under D-026. Conflicting triggers acquire no capture or model resources. Meetings share capture capacity and one preview lane. Imports and reprocessing may queue concurrently. For the final speech lane, the latest stopped live run receives at most two consecutive windows while an older run is waiting; then the oldest waiting run by enqueue sequence receives one window. The quota persists across new dictations. A finished or cancelled run leaves the queue. With finite window completion and continued resource admission, every finite older run eventually finishes even when new dictations continue arriving. Disk stalls, worker failures and sustained resource denial suspend that guarantee and remain visible. A newly stopped dictation may wait for the active window and a fairness window; F1 measures that delay. Other lower-priority inference preempts only at its declared checkpoint. A preempted run returns to queued with resume_stage and reason=preempted. Its charge remains until unload or exit. D-006 owns storage-backlog thresholds. No elapsed-time assumption authorizes reclaiming a live worker's resources.

Reconsider when: Measurements support smaller reservations, reveal starvation, or an explicit concurrent-capture requirement justifies another resource design.

### D-005: Database publication and search

Use GRDB over SQLite WAL with one writer and synchronous=FULL. Prepare long text and segment payloads in hidden sets through transactions of at most 256 rows. Reduce batches when measured occupancy exceeds 10 ms and yield between batches. Final publication creates result ownership and dependency records. Audio publication references an authoritative manifest.

SQLite supplies atomic eligibility checks. Bounded preparation separates large payload work from the transaction that makes it visible.

Alternatives considered:

- Publish every payload row in one transaction: Simplifies rollback while extending writer occupancy with recording length.
- Use SwiftData: Provides native observation while requiring additional full-text and lifecycle control.

Consequences: Use FTS5 over approximately 16 KiB text chunks. Search collects candidate results containing query terms and verifies phrases with a streaming tokenizer across boundaries. Page queries and cap database caches. Derived audio indexes rebuild in bounded batches. SQLite and GRDB availability are integration assumptions to verify against the pinned build. A bundled SQLite remains a concrete substitute if baseline FTS5 support is unsuitable.

Reconsider when: Library-scale measurements justify another index, batch policy or persistence implementation.

### D-006: Capture startup and outstanding audio

At the trigger, retain the immutable settings registry and start input on demand into preallocated 60-second rings. Keeping recording controls enabled does not keep microphone capture running. Show Starting until the first admitted buffer, then show Recording and issue the configured recording cue. Audio spoken before acquisition is unavailable. CaptureStore creates its session directory under the live-instance lock, synchronizes CaptureSeed and initial SessionControl, then writes chunks. Input initialization and those file operations are the only storage prerequisites. Database registration, model access, target lookup and context proceed independently.

Recoverable capture needs an owner without depending on shared transactions. A 60-second ring uses a modest part of D-027's capture charge and tolerates longer storage stalls than a five-second ring. Its cost is a larger possible loss window during a stalled application crash.

Alternatives considered:

- Use a five-second ring: Reduces maximum uncommitted audio while a two-second commit stall can stop capture during otherwise recoverable storage contention.
- Drop frames and keep the session running: Preserves session continuity while deliberately losing audio and complicating the user's understanding of meeting completeness.

Consequences: Backlog is admitted audio beyond the last journal-committed frame, including in-flight writes. Ring slots remain occupied until that commit. No other uncommitted audio queue exists. At ten seconds suspend other app disk work and show storage delay. At forty-five seconds request input stop. Callbacks independently refuse admission before outstanding audio exceeds sixty seconds per stream. Fatal storage errors request immediate stop. Drain accepted frames if storage recovers, otherwise retain the verified prefix. A crash during a stall may lose up to sixty seconds of admitted audio. The thresholds are provisional and do not promise tolerance of every filesystem failure.

Target lookup has a provisional 500 ms watchdog and URL sampling 200 ms. Session progress states are arming, capturing, import_probing, import_copying, import_decoding, sealing, published, interrupted and discarded. Processing states are queued, awaiting_language, recognizing, diarizing, refining, rewriting, translating, noting, interrupted_resumable, completed, cancelled and failed. Only the final three are terminal. Admission increments execution_epoch. The microphone-in-use indicator can appear during arming and capture and ends after input has stopped; model residency alone does not activate it. F1 measures trigger-to-first-sample separately from model readiness. A first-time permission prompt is reported separately from steady operation.

Reconsider when: F3 measures unacceptable stop frequency or loss exposure, or negotiated input formats require different finite capacity and charging.

### D-007: Cancellation semantics

Canceling arming discards the session. During live capture, explicit cancellation stops input and commits discard, independently of the interruption-preservation preference. The meeting control is labeled Discard recording; Stop keeps the recording. During sealing, persist discard before acknowledging cancellation and clean up the verified prefix. If recording publication has already won that race, complete the discard through D-024's deletion protocol. Canceling import probing, copying or decoding discards owned staging. Queued or awaiting_language cancellation removes pending work. Recognition and diarization discard unpublished output. Refining preserves recognition. Generation stages preserve inputs and discard incomplete output.

Cancellation must account for durable inputs and effects already attempted at every stage.

Alternatives considered:

- Discard all artifacts on every cancellation: Uses one visible rule while losing useful inputs when only a later transformation was unwanted.

Consequences: Meeting cancellation uses its explicit control under D-026. Canceling interrupted_resumable removes its checkpoint. Compression cancellation leaves the active representation. Delivery follows D-010. Acknowledgment waits for durable intent, while worker termination and reclamation may finish later. Persistence failure is reported without claiming cancellation committed. Terminal retries create new runs. The seed's interruption preference applies only when no explicit disposition was committed. Refining means D-015's post-recognition cleanup pass. Its cancellation keeps the committed source transcript.

Reconsider when: The product adds deliberately retained partial outputs as a separately labeled artifact kind.

### D-008: Target identity and recovery destination

Latch the foreground process launch identity at a dictation trigger. Acquire its window and editable element within D-006's deadline. Default delivery requires the same element to remain focused. An explicit per-mode follow-field policy permits another field within that application. Revalidate at dispatch and before every operation using the selected route's identity rule.

Process identity alone does not identify an intended field after focus changes. Web-element replacement needs evidence beyond matching text.

Alternatives considered:

- Deliver to the frontmost application at completion: Reduces holds while allowing delayed text to reach unrelated work.
- Automatically refocus the original destination: Improves completion frequency while interrupting current activity.

Consequences: Unavailable identity or a pre-effect mismatch holds output. Deliver here acquires a fresh destination only when no effect has been attempted. After uncertainty, D-010 requires informed resolution before reusing the text. Explicit delivery from History obtains its target at that action. Recovery suppresses automatic delivery. Generic APIs cannot atomically lock focus against competing actions. A held attempt never becomes queued merely because focus returns or permission becomes available. Before any attempted effect, the user must choose Resume delivery for the original element or Deliver here for a fresh destination. Both actions revalidate current identity and selection. The app does not refocus a destination automatically. Once an effect is uncertain, only D-010's informed-resolution path can create another attempt.

Reconsider when: A target supplies stable destination IDs or an atomic insertion protocol.

### D-009: Delivery routes, persistence and qualification

Select the route before compiling the entire result into a DeliveryPlan. Prefer a qualifying AXSelectedText profile when the actual attribute is settable. Interpret synchronous kAXErrorSuccess as acknowledgment only within that profile's evaluated scope. Never use AXValue for insertion. Otherwise use an applicable Unicode profile or an explicitly authorized user route. No effect may trigger automatic fallback to another mechanism.

The supplied Apple setter contracts support scoped acknowledgment while messaging failures remain uncertain. Apple warns that frameworks may ignore [Unicode keyboard-event payloads](https://developer.apple.com/documentation/coregraphics/cgevent/keyboardsetunicodestring(stringlength:unicodestring:)). Persistent user choice can support repeated dictation without inventing compatibility evidence.

Alternatives considered:

- Require a new experimental choice for every unprofiled dictation: Limits authorization lifetime while imposing one setup action per utterance.
- Automatically use generic Unicode everywhere: Broadens immediate coverage while permitting silent no-ops and unknown control-character behavior without user selection.
- Continue expired profiles as validated: Avoids interruption while falsely extending old evidence to a changed application.

Consequences: An unprofiled destination initially holds and offers Unicode single-line insertion, clipboard paste or Copy. Unicode and paste choices can apply once or be remembered for the bundle, signing identity and editable-role scope, optionally narrowed to a website origin. Remembered choices survive restart, remain labeled unverified, and can be revoked in the panel or settings. Revocation holds undispatched work and stops remaining operations before the next effect. It cannot undo earlier effects. Generic paste is available independently of any TargetProfile. Its authorization persists, while each consumption barrier still follows D-010.

A profile outside its evaluated app or OS range loses validated status. It holds once and offers a remembered Continue unverified choice or another route. A previously selected continuation preference can continue with a visible notice. Structural predicate changes, signing-identity changes and known incompatible behavior always hold. Unverified continuation disables automatic submission and treats insertion as unconfirmed even if AX returns success. It never bypasses missing content mappings.

Preflight streams the entire output before an effect. Unicode payload chunks exclude raw line breaks and other control characters. CRLF, CR, LF and Unicode separators require a profile's verified non-submitting break sequence. Tabs and remaining controls require explicit mappings. Generic remembered Unicode is control-free single-line only. Unsupported content holds the whole result and offers another preselected route. Clipboard paste retains paragraph text without synthesizing break keys, with target-specific paste interpretation disclosed. AX profiles declare multiline behavior and chunk support. Chunking respects composed characters and operation-size limits. Profile qualification requires F4's contract review and zero contradictory success outcomes in its declared fixtures. User authorization cannot confer qualification.

Reconsider when: F4 supports more profiles, demonstrates unacceptable unverified-route costs, or a target supplies stronger insertion semantics.

### D-010: Delivery journal and clipboard barrier

One DeliveryExecutor selects the lowest queue sequence among currently eligible attempts. Held attempts remain visible without blocking unrelated eligible work. D-008 requires an explicit action to make held pre-effect work eligible again. Insertion states are queued, held, dispatching, confirmed, unconfirmed and cancelled. Submission states are not_requested, pending, dispatching, confirmed, unconfirmed and cancelled. Persist each operation's intent before invocation and returned evidence afterward. Restart converts potentially dispatched operations to unconfirmed without replay. Only proven pre-effect failures return to held.

Serialization prevents overlapping app effects. Durable intent permits conservative recovery when a crash separates an external action from its recorded outcome.

Alternatives considered:

- Sequence pastes under one clipboard snapshot chain: Avoids per-paste review while allowing an earlier delayed paste to consume a later transcript.
- Retire a paste barrier at the next capture trigger: Uses a common action without establishing consumption or cancellation.
- Block all delivery behind a paste barrier: Simplifies queue ordering while withholding independent operations that do not change the clipboard.

Consequences: Operation ranges and API-return counts are diagnostic. Posted Unicode events do not establish consumption. Stop remaining operations on cancellation, failure or detected target change. Confirm insertion only when every required operation qualifies under D-009 and final checks pass. Uncertain attempts offer inspection, dismissal or editing a remaining passage into a new derived result. Repeating full text requires explicit acknowledgment of possible duplication. Requested submission after confirmed insertion rechecks target and expected selection, journals the selected submission sequence and dispatches once. Submission remains unconfirmed unless separately acknowledged. Unacknowledged insertion requires inspection before submission.

A paste barrier blocks clipboard-dependent operations only, including subsequent paste, app Copy and restoration. Independent AX and Unicode attempts remain eligible under the queue rule. Before paste, snapshot eager clipboard data up to 10 MiB, recheck changeCount, persist intent and the barrier, then write text and post Command-V. Record the written changeCount when available. Retirement requires supported consumption acknowledgment, definitive cancellation, proof of no dispatch or explicit informed user resolution. Timeouts, clipboard edits and new captures cannot retire it. Restore only after retirement while a recorded changeCount proves ownership. Unsnapshottable content requires explicit replacement without restoration. Restart preserves the barrier and loses the memory-only snapshot. Copy during a barrier requires resolution first. A crash before recording the written count prevents automatic restoration. The app cannot prevent the user or another application from changing clipboard contents during a delayed paste.

Reconsider when: A supported consumption protocol removes uncertainty or F4 requires another preselected integration.

### D-011: Provisional recording display and residency

Use a movable nonactivating AppKit panel with waveform, effective mode, provisional text and stage status. Remember placement relative to its display's visible frame and clamp it after display changes. Preview uses bounded rolling windows. Published final output replaces provisional text.

The panel must remain visible without taking destination focus. Preview needs independent capacity while earlier work finishes.

Alternatives considered:

- Use an activating window: Simplifies interaction while changing destination focus.
- Unload final speech immediately after every job: Releases residency sooner while making repeated dictation reload a model whose reservation already stands.

Consequences: Load preview when recording controls are enabled and retain it while idle. Keep final speech resident for a provisional sixty-second idle linger, evicting sooner for pressure or a different model. Generation and diarization unload when idle. Reservations remain separate from residency. Evicted preview shows cold-loading status. Meeting display identifies the session kind, selected streams and separate Stop control. One preview model services bounded stream windows sequentially, retaining only a recent visible text tail. Preview errors remain visible. D-017 defines tentative language behavior.

Reconsider when: F1 establishes another linger, residency policy or preview adapter with better measured behavior.

### D-012: Mode contracts and output checks

ModeContract contains instructions, formatting rules, allowed operations, answersQuestions defaulting false, language policy, context configuration, model selections and unavailableCheckPolicy. Users create custom modes and immutable revisions. unavailableCheckPolicy is allow_with_notice or review_before_delivery. Built-ins use allow_with_notice for missing semantic checks and apply available structural checks. CheckReport contains check ID, revision, applicability, finding class and source/output spans.

Missing semantic coverage supplies no result-specific evidence of a violation. Mandatory inspection solely for absent coverage adds recurring interaction without establishing fidelity. The alternative remains selectable per mode and never blocks model execution.

Alternatives considered:

- Require validator coverage for execution: Restricts compatible imported models without proving correctness.
- Require review of every generated result: Makes uncertainty visible while adding an action to routine dictation.

Consequences: Structural checks validate schema, literal formatting constraints, required fields and citation ranges. Violations hold the complete candidate. English faithful rewrites check interrogative punctuation, auxiliary or wh patterns and indirect-question cues. Lost aligned questions or appended answer cues produce suspected-semantic findings and hold delivery. Quantity, negation and terminology differences are advisory. Expansion beyond twice source length and eight-token context copying are advisory where tokenization applies. Translation uses structural checks and advisory quantity or name differences. Notes require valid citations and report attribution inconsistencies. Missing checks follow the frozen policy. Prompting and development review carry remaining expectations, including residual unwanted answering and context obedience. Editing or explicit review can release held candidates.

Reconsider when: Measured false positives, missed violations or language coverage justify changing a named check or disposition.

### D-013: Built-in transformation contracts

Provide Standard, Email, Note and Meeting notes. Standard preserves recognition wording apart from configured deterministic cleanup. Email may adjust punctuation, capitalization and paragraphs and format dictated salutations or closings. Note may organize content into headings and lists without dropping substantive claims. Meeting notes may summarize supported discussion into timestamped topics, decisions and actions while retaining uncertainty and citations.

Formatting and summarization permit different transformations and need distinct fidelity expectations.

Alternatives considered:

- Use one prompt with style labels: Reduces mode structure while leaving permitted transformations implicit.

Consequences: Every built-in sets answersQuestions=false. Email cannot invent recipients, commitments or answers. Unsupported meeting sections can remain empty. Built-ins are read-only revisions. Customization creates a custom mode. A custom mode can explicitly enable answering, shown in its editor and indicator. Selecting Meeting notes as a transformation does not start a meeting capture. D-012 controls candidate disposition.

Reconsider when: Repeated customization establishes another distinct built-in operation.

### D-014: Automatic mode resolution

Resolve once from the trigger's registry. A per-mode shortcut wins. Otherwise evaluate user-ordered rules using first match, followed by the default mode. Predicates support bundle identifiers and supported browser URL host or path. Unavailable input makes its predicate non-matching.

Visible order makes conflicts predictable and keeps processing instructions stable during capture.

Alternatives considered:

- Infer specificity: Reduces ordering work while making overlapping rules less apparent.
- Reevaluate throughout capture: Tracks browsing while changing the interpretation of audio already recorded.

Consequences: Show the effective mode and selection reason, including fallback. Identify shadowed rules. Exclude query strings and fragments by default. The panel may briefly show resolving. Recovery without completed resolution uses the seed's unavailable-input fallback. Explicit meeting setup selects its mode and does not inherit a later foreground change. Source configuration remains separate.

Reconsider when: Rule-set size, observed URL omissions or new supported predicates require another resolution policy.

### D-015: Terminology and cleanup ordering

Populate recognition bias with pinned terms, then selected-mode terms, then global terms in user order. Deduplicate before model-tokenizer budgeting and include complete terms that fit. Preserve raw recognition. Apply replacements once using longest match, then list order. Apply optional filler removal next, language processing afterward and explicitly designated output replacements in one final pass.

Recognition bias and text replacement act on different inputs. Deterministic precedence prevents loops and unexplained output changes.

Alternatives considered:

- Replace only after generation: Uses fewer passes while letting generation interpret misrecognized terminology.
- Cascade replacements: Supports chains while introducing loops and order-sensitive behavior.

Consequences: Report omitted bias terms. Rules expose literal, case-sensitive and whole-word options. Whole-word behavior uses locale-aware segmentation. Unsupported boundaries require literal matching. Filler removal defaults off and uses standalone patterns. Terminology changes recognition probabilities and never guarantees a name will be recognized.

Reconsider when: A runtime exposes evaluated biasing controls or cleanup review finds recurring errors.

### D-016: Context sampling and authority

Sample enabled focused-application text and clipboard text within 200 ms of the trigger. Screen context selects the foreground application's focused window identified at the trigger and captures that window alone for local Vision OCR within one second. Cap each text source at 32 KiB and the image at four megapixels. Record omissions and truncation. Supply extracted terms by default, with prose selectable per source.

Window selection bounds collection. Trigger-time sampling reflects the original task. Deadlines and caps remain unmeasured responsiveness budgets.

Alternatives considered:

- Sample before generation: Obtains newer material while potentially collecting another task.
- Capture a display: Includes surrounding material while collecting unrelated windows and requiring explicit display selection.
- Capture continuously: Tracks changes while expanding collection and retention.

Consequences: Latch process launch and window identities independently of editable-field lookup. Capture only that same foreground focused window's current bounds, excluding other windows, desktop and shadows. For spanning windows, normalize display scales into one window image before applying the cap. Record window identity, bounds, intersected display IDs and sampling time in the source descriptor. Missing identity or isolated capture, closure, minimization, focus change or expiry produces a typed omission without display fallback. D-032 governs permission and secure-input exclusions.

Ignore late samples. Context is subordinate data without tools or authority to change instructions. Release pixels after OCR and transient text after its last consumer or termination. D-039 opt-in retention stores the supplied representation and source descriptor, never pixels. Collection cannot delay capture storage. Delimiters cannot eliminate every model error.

Reconsider when: Measured omissions, OCR latency or fidelity review justify different bounds, representations or an explicitly selected region.

### D-017: Language resolution and English translation

Offer explicit recognition language or model-supported detection. Setup asks for a fallback supported by the selected speech model, with UI locale only a suggestion. Detection uses a bounded initial speech window and an adapter policy identifying documented label and score semantics. Apply thresholds and margins only where their scales are meaningful. Missing, conflicting or uninterpretable evidence uses the frozen fallback.

Score scales differ by runtime. A named fallback supplies predictable behavior without inventing calibrated certainty.

Alternatives considered:

- Always preview in the fallback language: Simplifies startup while showing misleading provisional text when a bilingual user speaks another language.
- Translate from audio with Whisper: Provides a local alternative while adding a speech pass and audio-dependent derivative alongside required source recognition.

Consequences: In detection mode, preview uses the model's tentative detected language when available and visibly labels it. Until usable evidence arrives it uses the fallback. Final recognition makes its own pinned-policy resolution and may replace preview text. Explicit selection overrides both. Without valid evidence or fallback, final work enters awaiting_language with audio retained. A user selection creates a replacement manifest and run. Preview without either language source requests selection. English translation uses the selected llama.cpp model over the retained source-language transcript, in translating with the generation charge. Cleanup precedes translation. A subsequent rewrite names translation as its parent. Initial default evaluation covers English and Spanish under F1. Unknown language quality remains labeled without disabling compatible imports.

Reconsider when: Bilingual evaluation supports another translator, detection policy or development language set.

### D-018: Sources and initial format matrix

Use AVAudioEngine for microphone capture, ScreenCaptureKit for system audio and AVFoundation readers in DecodeService for imports. Initial support covers WAV PCM or IEEE float, AIFF PCM, CAF PCM or ALAC, MP3, M4A AAC or ALAC, and MOV, MP4 or M4V with supported AAC, ALAC or PCM audio tracks. Probe contents and require track selection when ambiguous.

Container extensions do not establish codec support. The supplied ScreenCaptureKit evidence supports system capture. Later microphone-stream APIs require availability checks.

Alternatives considered:

- Bundle FFmpeg initially: Broadens formats while adding native dependency, licensing and security-update work.
- Decode whole files into memory: Simplifies adapters while violating long-file memory behavior.

Consequences: DRM and unsupported codecs produce specific errors. Compiled DecoderProvider implementations extend the matrix within the service. User executable decoders are excluded. Every source reports timing and discontinuities. Baseline fixtures establish each advertised format combination.

Reconsider when: Common unsupported formats justify another bundled decoder.

### D-019: Import ownership and decoded storage

Persist import_probing before probing an external read-only handle, import_copying before incremental copying, and import_decoding after verifying the owned copy. Record copied hash and length. Compare source identity, size and modification metadata before and after copying, rejecting observed changes. Decode into session-owned chunks. Enter sealing only after successful end-of-input validation.

An owned source survives external movement. Eager decoded storage provides stable playback and repeated analysis without repeating container decoding.

Alternatives considered:

- Decode on demand from the owned copy: Avoids full decoded storage while adding repeated decoder cost, seek behavior and decoder-version dependence to playback and reprocessing.
- Reference the external file: Saves storage while introducing missing-source and access-capability recovery.
- Retain decoded audio only: Saves video storage while preventing decoder comparison against original bytes.

Consequences: Import never modifies, moves or deletes the external source. Metadata checks cannot prove perfect stability during concurrent edits. Imported identity is the copied byte sequence. Preflight estimates source-copy and decoded-audio storage together. Failed imports publish no truncated recording. D-031 defines restart outcomes. D-024 permits later removal of the copied source.

Reconsider when: F3 shows decode-on-demand has acceptable repeat and seek costs, or video usage warrants an explicit audio-only import option.

### D-020: Speech runtime

Use WhisperKit with explicit local paths and acquisition disabled. Pin a reviewed revision during integration. Preview and final recognition use separate service instances. Final recognition receives bounded overlapping windows and assigns tokens to half-open ownership intervals by timestamp midpoint.

Swift integration reduces bridging work alongside SpeakerKit. Windowing is application-controlled regardless of upstream file-loading convenience APIs. The pinned adapter still needs inspection for retained output and hidden allocations.

Alternatives considered:

- Use whisper.cpp initially: Provides an Intel-capable route while adding C integration beside the diarization package.
- Use Apple speech services: Reduces asset-management work without supplying the required user-controlled model surface.

Consequences: Keep a whisper.cpp comparison harness. Record boundary ambiguity and evaluate missing or repeated words. Missing precise token timing uses declared segment timing with reduced boundary precision shown. Upstream mechanisms do not establish application latency or accuracy. Final recognition initially uses 30-second windows with a 25-second step, clipping token ownership at overlap midpoints. These are provisional latency and boundary-quality parameters evaluated by F1. After each window the coordinator validates its epoch, stages the result in app-owned run storage, and commits a checkpoint containing input hash, stream, next-window index, runtime revision and parameters before acknowledging worker output. Ordinary preemption retains those completed windows and resumes at the next window under the same inputs with a new execution epoch. Unacknowledged output is discarded. No partial transcript becomes a completed result. Application or worker crash still follows D-031's deliberate failed-run policy; only orderly preemption resumes recognition automatically. A crash retry is an explicit new run over retained inputs.

Reconsider when: F1 rejects the adapter, Intel support is pursued, or another bounded path improves fidelity.

### D-021: Language runtime

Embed llama.cpp in GenerationService and accept compatible GGUF asset sets. Configure tokenizer, chat template, context length and KV cache explicitly. Stream output through the application protocol without server tooling.

The supplied llama.cpp research supports local C/C++ inference and macOS acceleration. Embedded integration gives the application ownership of execution and input access.

Alternatives considered:

- Use MLX generation: Offers Apple-focused execution while requiring equivalent import coverage and another asset format.
- Use an installed local HTTP server: Reuses tooling while leaving forwarding behavior outside application control.

Consequences: Maintain a pinned C++ bridge. Resource descriptors include accelerator allocation and KV cache. Translation, rewriting and notes share runtime infrastructure with separate parameters. Immediate end-of-sequence is a valid contract outcome whose quality is assessed separately.

Reconsider when: Another embedded runtime provides better measured behavior and equivalent local imports.

### D-022: Model acquisition and activation

Create model_staging and a creator lease before acquisition. Accept regular data files and reject escaping paths, symlinks and executable plugins. Hash behavior-affecting assets, freeze staging and obtain D-033's staging execution lease. Verify structure and smoke-test loading and result contracts. Acquire a setHash publication claim. Verify an existing blob or synchronize and publish by same-volume atomic no-replace rename. Publish notices, then transactionally create installation references and update the active pointer.

Acquisition must protect working installations and coordinate identical concurrent imports.

Alternatives considered:

- Replace the destination directory: Simplifies installation while risking active assets or another importer's publication.

Consequences: Followers verify the winning blob. Corrupt content requires repair without overwriting live bytes. Failure preserves the active pointer. Failed staging remains inspectable for seven days or until dismissal, with live ownership preventing collection. Recovery reconciles activation before sweeping orphan blobs and notices.

Reconsider when: Measured acquisition cost supports verified deduplication optimizations.

### D-023: Meeting sources and speaker evidence

Start Meeting opens a dedicated setup control with a remembered source preset. Default to microphone plus system audio when permissions allow, showing each source and requiring acknowledgment of a missing requested source before starting. Users may select either source alone. Choose a notes mode explicitly, defaulting to Meeting notes. Freeze those settings in the meeting seed. D-026 governs conflicting controls. Stopping a meeting stores its recording and starts configured local analysis without automatic external delivery.

Session kind and input selection must remain distinct from output transformation. Separate source tracks preserve provenance for meeting playback and analysis.

Alternatives considered:

- Start meetings through an ordinary mode shortcut: Uses fewer controls while making capture lifetime depend on a transformation selection.
- Analyze a mixed stream: Uses fewer passes while losing source separation and complicating echo diagnosis.
- Use whole-meeting analysis: May improve continuity and reduce confirmation work if F5 and inspection establish bounded retention.

Consequences: Recognize each selected stream independently. Retain stereo system audio and use declared mono analysis conversion. Run SpeakerKit separately per stream, sequentially under one charge, with explicit modelFolder, download=false and segmenter/embedder concurrency of one. Analyze 600-second windows at a 570-second step. The midpoint of adjacent overlap defines half-open ownership intervals. Clip segments to those intervals and discard empty clips.

Fit native frames to host timestamps and preserve gaps. Alignment is host_aligned_unverified when timestamps are valid and maximum fit residual is at most 50 ms, otherwise degraded. Map results onto the common timeline, sorting by start and stable IDs without collapsing simultaneous speech. Associate text with intersecting same-stream speaker intervals and retain ambiguous candidates. Propose up to three centroid matches at distance at most 0.35, with differences within 0.02 treated as ties. Adjacent-window overlap proposals require two shared seconds and intersection/union at least 0.6. The supplied versioned SpeakerKit evidence supports optional centroids, without calibrated confidence. Thresholds are uncalibrated display heuristics. Cross-stream identity is never automatic.

Speakerphone echo can duplicate transcript and notes content. Preserve stream citations and show that limitation. Voice processing remains disabled initially because its effectiveness against another application's output and its effect on local speech are unmeasured. F5 compares it on actual playback routes. The initial diarization adapter consumes 16 kHz mono Float32 analysis windows. A 600-second decoded window is 38.4 MB, approximately 36.6 MiB, leaving approximately 1499 MiB of the provisional 1.5 GiB charge for model residency, embeddings, workspace and transport. That arithmetic covers the audio buffer only; F5 and allocation inspection must establish the remaining footprint. Ten minutes is a provisional compromise that limits window storage while giving clustering more continuous speech than short recognition windows. The thirty-second overlap supplies boundary evidence. Compare shorter windows and whole-input processing under the same quality, effort and resource criteria before choosing release defaults. For two hours, the 570-second step produces thirteen windows per stream. Four continuing remote speakers and one continuing local speaker create sixty cross-window link decisions over twelve boundaries. Batch confirmation can reduce actions, while inspection time remains part of F5's correction burden. Meeting setup exposes system-audio scope: All system audio, visibly including other applications' playback, or an explicitly selected application. The first setup shows All system audio as its initial scope. Store that choice with the source configuration. If the selected application's capture is unavailable, report the missing source and require the existing acknowledgment before continuing; never broaden its scope silently.

Reconsider when: F5 supports calibrated proposals, bounded whole-input analysis, concurrent passes within measured charges or validated echo handling.

### D-024: History, retention and deletion

Group recordings with searchable result chains, paged transcripts and timeline playback. Reprocessing creates a new manifest over chosen inputs. Default retention is manual deletion. Optional age-based policies use the same protocol. Tombstone a recording and increment generation transactionally, then hide it and signal workers. Before publication, persist discard and the deletion record under broker serialization.

Logical invalidation must precede reclamation so late workers cannot recreate deleted material.

Alternatives considered:

- Wait for workers before hiding a recording: Avoids a deleting state while allowing a hung process to delay the request.
- Overwrite previous results: Simplifies History while losing provenance and comparisons.

Consequences: Users can separately remove copied sources, audio or context. Removal excludes new dependent jobs while existing leases drain. Text reprocessing remains after audio removal. Cancel undispatched delivery plans. For attempted effects, retain minimal detached attempt and barrier metadata required by D-010, without deleted text. Deletion cannot undo external text. Compact deletion records prevent resurrection. Meeting setup displays uncompressed storage rate and free-space estimates. Secure SSD erasure is not promised. Library deletion does not promise system-clipboard clearing. An unresolved paste may leave the deleted transcript there; detached barrier metadata contains no transcript. Explain that remaining copy in the deletion result and offer D-010's informed resolution. Only after barrier retirement and an ownership check may the app change the clipboard.

Reconsider when: Observed storage use warrants another explicit retention default.

### D-025: Desktop controls and restoration ownership

Provide menu bar controls and application-local microphone gain with clipping indication. Do not change hardware input volume. A validated adapter may pause an identified player. Automatic resumption requires confirmed pause completion and atomic conditional resume using an ownership token that changes on every intervening control action, including competing clients.

Playback position, device activity and incomplete notifications cannot establish continuing ownership. A read followed by unconditional resume leaves a competing-action race.

Alternatives considered:

- Resume when position appears unchanged: Supports more integrations while potentially reversing an intervening user pause.
- Send a global media-key toggle: Requires little integration while identifying neither recipient nor pause completion.

Consequences: No real player is currently claimed to qualify for automatic resume. Validated player-specific controls may pause and offer explicit Resume. Music and Spotify require control validation before listing. Unsupported players receive no automatic toggle. Local gain creates no external value to restore. Revoked permission invalidates ownership.

Reconsider when: A documented integration supplies continuing ownership and conditional control under competing-action tests.

### D-026: Dictation and meeting controls

Provide configurable toggle and push-to-talk bindings with per-mode variants and conflict detection. Cancellation is configurable, may be disabled or rebound, and defaults to no global binding. Users may explicitly bind Escape. A fast session event tap tracks keys and modifiers, ignoring repeat. Push-to-talk stops dictation on release and samples submission intent then. Toggle and per-mode triggers stop active dictation without changing its frozen mode. During meetings, dictation triggers are rejected with a visible Meeting recording notice and preserve the session.

Explicit ownership preserves meetings. An unbound cancellation default leaves Escape to foreground controls. Concurrent dictation is not required.

Alternatives considered:

- Bind Escape cancellation by default: Provides an immediate shortcut while allowing a foreground Escape gesture to discard audio.
- Let dictation shortcuts stop every capture: Simplifies transitions while unexpectedly ending meetings.
- Capture concurrent dictation: Enables chat replies during meetings while requiring source, preview and delivery concurrency.

Consequences: Start Meeting, Stop Meeting and Cancel Meeting have separate menu/panel actions and optional bindings. D-007 names Discard recording and defines cancellation. Start Meeting is rejected during any live session. Stop Meeting seals the active meeting. Source presets belong to these controls independently of modes.

Dictation cancellation matches a live dictation or explicitly selected processing operation and cannot discard meeting capture. Eligible bindings consume non-modifier key-down, repeats and paired key-up, even if key-down ends the session. Rejected dictation triggers during meetings are also consumed. Disabled, unmatched and ineligible bindings pass through. Observe and forward modifier events. Cancellation follows D-007 without duration-based confirmation or undo. Menu/panel cancellation remains available.

Tap disable, sleep and lock recover push-to-talk state and clear stale gesture ownership. Physical polling detects missed release. Missing permissions visibly disable affected bindings and consumption while available menu controls remain usable. No maximum hold duration applies. A selected processing operation is eligible for the global cancellation binding only while this application is frontmost when the binding's key-down is observed. Its selection may remain in History after focus leaves, without retaining global cancellation eligibility. With another app frontmost and no live dictation, the cancel gesture passes through and leaves background processing unchanged. A live dictation remains eligible under its explicitly configured global binding. Once an eligible key-down owns a gesture, D-026's existing paired-key-up consumption rule still applies if focus subsequently changes.

Reconsider when: A supported shortcut API reduces maintenance or an explicit concurrent-dictation requirement changes ownership.

### D-027: Resource admission envelopes

Start with a configurable 8 GiB admission budget. Reserve 512 MiB for shell and Store, 128 MiB for capture, 1 GiB for preview and 2 GiB for final speech. The standing total is 3.625 GiB, leaving 4.375 GiB. Choose an initial generation configuration with a total envelope at most 4 GiB. Charge each decode or compression job 512 MiB, materialization 256 MiB and diarization 1.5 GiB.

Separate charges make concurrency arithmetic reviewable and protect recording capacity independently of model compatibility.

Alternatives considered:

- Admit a 5.5 GiB generator under the standing budget: Exceeds remaining capacity and requires a higher budget or smaller configuration.
- React only to memory pressure: Avoids estimation work while responding after pressure develops.

Consequences: Capture includes Float32 input rings, one two-second conversion buffer per stream, resampler state and metadata. Sixty seconds across three 48 kHz channels uses approximately 34.6 MB for rings, with approximately 1.2 MB of Float32 conversion scratch. Negotiate actual rates and channels before admission and revise the charge if necessary. One live session and one sequential meeting-preview lane add no second capture reservation. Model charges include runtime baseline, resident mappings, workspace and accelerator allocation. Larger compatible models may wait, use smaller parameters or request a higher budget. Disabling live controls releases their standing reservations for batch work.

Reconsider when: Measurements replace estimates or another hardware class requires different defaults.

### D-028: Local inference and acquisition isolation

Enable App Sandbox for inference and decode services with network client and server entitlements absent. Provide no downloader connection. Disable runtime acquisition explicitly. ModelDownloader starts only for user-requested acquisition, accepts an approved URL and staging capability, validates redirects and receives no recording, transcript or context handles.

A loopback endpoint can forward data. Owning the inference process and sandbox establishes an evaluable boundary.

Alternatives considered:

- Trust an installed local model server: Reduces integration work while transferring privacy behavior to another process.
- Download during model loading: Simplifies setup while introducing implicit network use.

Consequences: Models remain data assets. Executable plugins and HTTP inference endpoints are excluded. Authenticate XPC peers by code identity. The shell implements no inference network path or telemetry. Routine operation works offline after installation. F2 checks entitlement behavior because symbol inspection cannot establish isolation.

Reconsider when: A replacement runtime needs another compliant integration. Local execution remains required.

### D-029: Result provenance and replay

Use data_lifecycle's schema and I1 as the provenance contract. Persist exact manifest bytes and input identities. Manual edits publish derived results. Selecting the displayed result changes presentation only.

Current settings and timestamps cannot explain concurrent recognitions or successive transformations.

Alternatives considered:

- Keep only current text and settings: Reduces storage while making earlier output unexplained.
- Store dependencies only in opaque JSON: Avoids relationship tables while complicating History and retention queries.

Consequences: Replay requires retained inputs and matching assets. Missing context, models or runtime revisions require explicit replacement choices in a new run. Source recognition remains available with retained translation or rewriting. Notes preserve their speaker revision. Lossless audio representation changes preserve logical audio identity. Provenance does not promise bit-identical stochastic output.

Reconsider when: A new operation needs another input kind or reproducibility parameter.

### D-030: Audio persistence and representation retirement

Capture approximately two-second signed 24-bit PCM chunks at negotiated native rates, defaulting to mono microphone and stereo system audio. Persist microphone samples after application gain, saturation and quantization. Gain defaults to unity; system audio and imports bypass it. Apply gain changes at the next capture chunk boundary, coalescing pending edits to the latest value. Each chunk has one effective gain. Preview and waveform use that effective gain. Headers record schema, stream, sequence, sample layout, frame count, host timestamp, effective linear gain, conversion revision and saturated-frame count. Frame ranges locate gain changes.

Native rates retain playback bandwidth and integer storage reduces Float32 size. Saved adjusted samples provide the captured level without a replay gain transform, at the cost of permanent gain-induced clipping. Verified chunks support exact-frame recovery.

Alternatives considered:

- Store microphone PCM before gain: Preserves samples from software amplification damage while requiring a versioned gain timeline and consistent playback/analysis transforms.
- Live ALAC CAF: Can reduce storage while requiring encoded-byte ordering and complete valid-frame, priming and remainder recovery.
- Default post-seal compression: May save space while adding unmeasured CPU and temporary-storage costs.
- Capture microphone at 16 kHz Int16: Reduces storage while losing playback bandwidth and requiring fidelity evaluation.

Consequences: Capture chunk boundaries are fixed by each stream's admitted native-frame indices, independently of storage progress. Latch the latest pending gain when the chunk's first frame enters capture storage and keep it unchanged for that entire chunk. Apply that gain to Float32 frames at admission into the bounded live ring. Preview and waveform consume those adjusted frames. The storage writer subsequently saturates and quantizes them without applying gain again or reading the slider. Writing and retries retain the chunk's latched gain metadata. Frame ranges locate whole chunks; there are no sub-chunk gain changes. A slider edit during a backlog affects only the next newly admitted chunk. It cannot alter frames already in the ring. Apply gain into bounded live buffers without another audio queue. The conversion revision specifies saturation/PCM rounding. Clipping counts cover software saturation without detecting device clipping. Preview may use adjusted Float32 before quantization. Playback/reprocessing use saved PCM and declared conversions without reapplying capture gain or reading the current slider. Lost clipped samples cannot be recovered. Show pending/effective gain and disclose its inclusion in saved audio. Import chunks record unity gain; D-019 retains their source.

Write .part, fsync, rename without replacement and fsync the directory. Append/fsync a length-prefixed journal entry with header fields, filename, length, hash and CRC before releasing ring frames. Durability covers application interruption with an operational OS/filesystem; fsync does not establish power-loss durability.

Incrementally verify a RepresentationManifest and publish its active pointer under I2. Default to PCM retention. Explicit Optimize storage uses per-chunk LZFSE through DecodeService, retaining PCM entries where compression does not save bytes. Assume no savings. The manifest discriminator selects decoding. Replacement preserves gain metadata and exact post-gain samples. Future ALAC requires a reader and exact decoded-hash validation before publication.

Owned replacement staging becomes ready after its bytes and manifest are verified and synchronized. A creator or publication claim protects it. In a short broker transaction, check owner generation and expected active representation, activate the replacement and supersede the old representation. State determines retention without a chunk-count-dependent transaction. Existing leases pin their acquired manifest and reachable bytes; new leases resolve the active pointer. Shared chunks remain retained through the new manifest. D-033 reclaims unshared superseded bytes after leases drain; diagnostic metadata may remain.

Recovery uses the database-active pointer. Before-switch interruption keeps the old representation and abandons or explicitly resumes staging. After-switch recovery resumes retirement without superseded files. Reserve conservative transient space P+C plus manifests and bounded codec buffers, where P is original storage and C is new encoded storage. Keeping PCM on expansion bounds C by P apart from headers. Low space postpones optimization. At 48 kHz, mono PCM uses about 518 MB/hour and three channels about 1.56 GB/hour. Recovery accepts verified journal prefixes and complete adjacent chunks, rejecting torn tails. D-006 supplies the stalled application-crash loss bound. Kernel panic and power failure have no bounded-loss claim.

Reconsider when: F3 supports automatic compression, another codec/sample format or stronger durability. Reconsider pre-gain retention if clipping losses warrant its replay contracts.

### D-031: Interrupted-operation recovery

Reconcile deletion records and database publication before scanning sessions. Discard arming sessions without verified chunks. Interrupted live capture/sealing applies SessionControl disposition and seed preference to verified audio. Database publication remains authoritative after an interrupted file-marker update. Recovered live recordings await Process or Discard and never deliver automatically. Diarization with a valid D-043 checkpoint becomes interrupted_resumable and awaits explicit Resume.

Explicit recovery actions prevent stale delivery. Held diarization retains completed windows without launch-time inference.

Alternatives considered:

- Automatically repeat interrupted work: Reduces interaction while repeating generation and reviving stale delivery intent.
- Queue checkpointed diarization at launch: Advances work while starting analysis before a continuation request.
- Discard interrupted work: Simplifies recovery while losing verified audio and completed windows.

Consequences: Interrupted import_probing/copying discards incomplete owned data and offers fresh source selection. Interrupted import_decoding keeps the verified source, discards partial decoded chunks and offers Restart decoding. Import sealing finishes only with complete-input evidence and a complete manifest, otherwise returns to restart decoding. Recovered publication awaits processing. Queued runs remain queued; awaiting_language remains held. Crash-interrupted recognition and generation fail with inputs retained; refining may requeue. D-020 governs orderly recognition preemption. Failed recognition checkpoints are reclaimed after leases drain and explicit retry recomputes in a new run. D-004 pressure termination uses that failure policy.

Diarization recovery accepts D-043's checkpoint prefix. interrupted_resumable has no ended_at and remains held across launches without inference admission, a model execution lease or model loading. Its eligible owner retains checkpoints. Show progress, Resume and Cancel. Resume validates pinned inputs, checkpoint integrity and required assets, then queues the same run with resume_stage=diarizing. Missing/incompatible assets leave it held with an explanation. Replacement inputs or settings require a new run. Normal resource admission rechecks eligibility, acquires model leases and assigns a new epoch before continuing at the next stream/window. Queued Resume survives restart as queued work. D-007/D-024 govern cancellation and deletion. No valid checkpoint means failed diarization with inputs retained. Orderly preemption follows D-004's automatic queue transition.

Terminal retry creates a new run. Missing transient context follows D-039, representation recovery D-030 and delivery recovery D-010.

Reconsider when: Another stage acquires a verified persistent checkpoint contract.

### D-032: Permission changes

Probe capabilities at setup and before affected operations. Microphone denial disables that source while imports remain usable. Revocation stops its stream and retains its prefix. Screen permission loss stops system audio and screen context. A microphone meeting may continue with a missing-stream marker. Accessibility loss holds insertion and application-text collection. Global-input loss invokes D-026 recovery.

Permissions affect separate capabilities and should preserve unrelated completed work.

Alternatives considered:

- Require every permission at launch: Simplifies readiness while blocking useful import and transcription workflows.

Consequences: Browser automation denial makes URL predicates unavailable. Player automation loss invalidates ownership. Secure fields and secure input suppress context and automatic delivery. Explain omissions with the relevant settings action. Restored permission applies at a safe boundary without retrying uncertain effects. Revocation ends a system-only capture with its retained prefix. If every selected stream is lost, seal an interrupted session.

Reconsider when: A macOS update changes API permissions or revocation behavior.

### D-033: Eligibility, leases and reclamation

ArtifactBroker serializes acquisition, publication, archival and deletion marking. Pre-registration session leases require a live-instance lock and valid journal generation. The live-instance creator protects directory construction before the journal exists. Recording leases require an untombstoned row and matching generation. Installed-model leases require an executable installation. Staging execution leases require a frozen row in verifying, smoke_testing or ready with matching owner and set hash. Mutable acquisition uses its staging creator lease.

Capture, idle preview and smoke tests begin under different owners. Shared models cannot inherit recording ownership. Long manifest traversal must also avoid blocking capture or database publication.

Alternatives considered:

- Require a recording for every lease: Uses one check while preventing setup tests and pre-capture model use.
- Reclaim after heartbeat expiry: Recovers stalls quickly while potentially removing assets held by a live process.

Consequences: Model leases pin actual blobs and materializations. Audio leases pin an immutable manifest and its reachable bytes. Active representation state provides durable reachability under D-030. Creators register before file creation. Acquisition and removal exclude one another under the broker. Archival excludes new execution leases while existing holders drain. Session registration transfers eligibility only after database association commits.

For long reachability checks, snapshot retention roots and broker root revision, scan manifests in bounded pages outside the critical section, then reenter the broker and mark candidate assets deleting only if the revision is unchanged and no claim intervened. Otherwise repeat the affected check. A missing membership cache causes a manifest scan or deferred reclamation. New roots and leases cannot attach to deleting assets. Holder death requires process-identity evidence or confirmed handle closure. I3 determines final eligibility.

Reconsider when: Measured broker contention or reclamation delay requires a different index with equivalent atomic checks.

### D-034: Development evidence dependencies

Use F1 for speech and language defaults, F2 for isolation and loading, F3 for persistence, F4 for practical delivery and F5 for meetings. Collect evidence before accepting dependent integration.

Properties that can invalidate ownership, resource bounds or required behavior need investigation before substantial implementation relies on them.

Alternatives considered:

- Measure after complete integration: Produces visible features earlier while risking extensive changes around failed assumptions.

Consequences: Records identify hardware, revisions and parameters. Unperformed or inconclusive work stays labeled. Failure requires a concrete adapter change, compatible configuration or blocking integration issue. F4's interaction threshold includes remembered unverified routes. Default-quality evaluation never becomes an imported-model feature gate.

Reconsider when: A new dependency introduces another property capable of invalidating a design boundary.

### D-035: Acceptance authority

The user or named product delegate accepts expected behavior and consequential changes. Engineering reviewers examine invariants, state tables and experiment methods. Design acceptance establishes proposed criteria as the evaluation basis. Routine experiments require no additional permission round.

Assertions may encode mistaken expectations. Practical behavior and semantic fidelity require review beyond automated conformance.

Alternatives considered:

- Treat passing tests as product acceptance: Reduces review work while overlooking incorrect or impractical expectations.
- Require approval before each experiment: Adds oversight while putting routine evidence gathering on the user's critical path.

Consequences: Criteria cannot change silently after results. Failed routine delivery returns with measured costs and a concrete alternative. Acceptance records distinguish design approval, integration evidence and release claims. A failed required capability cannot be resolved by removing it from scope.

Reconsider when: An established delegate or acceptance process changes authority.

### D-036: Direct distribution and packaging

Distribute directly. Use ad hoc signing locally and Developer ID signing with hardened runtime and notarization for releases. Keep the shell unsandboxed and workers independently sandboxed. SPM builds source targets. A checked-in packaging script assembles the app and XPC bundles from SPM products, copies reviewed Info.plist and entitlement files, embeds services, and signs services before the containing application. Development and release use that same bundle layout.

The specification settles distribution and requires no separate product-owner decision.

Alternatives considered:

- Mac App Store distribution: Adds store delivery while requiring another entitlement and behavior assessment.
- Unsigned releases: Avoids certificate management while impairing installation and permission identity.

Consequences: Release maintenance includes dependency notices, notarization and signed update packages. Updates are initially user-initiated. Ad hoc identity changes may require permission grants again. F2 validates both signing arrangements. No application account is required.

Reconsider when: Distribution requirements change.

### D-037: Speaker interpretation and timestamped notes

Use unknown, unverified and assigned_by_user attribution states. Missing labels are unknown. Model clusters are unverified. Proposals change no assignment. Direct assignment, merge or confirmation creates assigned_by_user in a new revision. Clearing returns attribution to unknown. Re-diarization carries earlier identities only as unverified proposals.

Within-window grouping and identity across streams or windows are different inferences. Saved names must preserve how the user established them.

Alternatives considered:

- Apply nearest-cluster names automatically: Reduces editing while extending uncertain identity into named notes.
- Mutate original segment rows: Uses fewer records while invalidating historical citations.

Consequences: Users may batch-confirm selected proposals after reviewing affected segments. Notes pin the merged transcript, aggregate diarization and speaker revision. They use confirmed names only for assigned_by_user and label other attribution. Each item cites stream-qualified segment ranges and timestamps. Renaming or splitting reports drift and offers regeneration. Echo-related duplicates remain inspectable through citations.

Reconsider when: Calibrated evidence supports another explicitly defined attribution state.

### D-038: Runtime replacement and historical storage

Installations use installed, archived, orphaned_runtime and missing states. Archival removes execution references while retaining hashes, notices and parameter descriptions. Adapter removal marks installations orphaned_runtime without automatically deleting weights.

Historical attribution must remain readable without permanently requiring every runtime and model binary.

Alternatives considered:

- Retain weights for every historical result: Simplifies replay while growing storage with every model tried.
- Delete weights when an adapter disappears: Reclaims storage while removing user assets during application updates.

Consequences: Stored parameter schemas keep History readable. The manager reports unique weights, shared bytes, staging and service caches with archival estimates. Restoring execution requires matching assets and a compatible adapter. Runtime updates drain old workers before replacing executables. D-040 governs cache replacement.

Reconsider when: Users request automatic archival or migration requires another compatibility state.

### D-039: Context retention and replay

Persist context status, sampling time and digest by default. Opt-in retention stores the exact bounded representation supplied to processing as an owned artifact before committing its reference. Exclude context from search.

A digest identifies a sample without reproducing its contents. Deliberately retained context needs explicit ownership.

Alternatives considered:

- Retain all samples: Improves replay while accumulating unrelated application content.
- Never retain samples: Simplifies storage while preventing deliberate replay with original context.

Consequences: Delete context with its owner or through explicit controls. Reprocessing without original bytes requires an omit-or-resample choice and a new manifest. Interrupted work cannot silently treat missing transient context as exact replay. Logs contain statuses and hashes without context payloads.

Reconsider when: A broader at-rest protection requirement applies to the library.

### D-040: Materialization ownership and storage bounds

Key materializations by setHash, service kind, runtime revision and cache-format revision. Register temporary paths under creator leases. Copy from read handles, verify hashes and synchronize files plus completion manifests. Publish without replacement under per-key claims, then register assets before releasing creators.

Shared content needs shared retention checks. Runtime-specific copies require explicit identity and accounting.

Alternatives considered:

- Copy per job: Simplifies lifetime while repeatedly copying large assets.
- Keep unregistered service caches: Reduces persistence work while losing deletion coordination and complete accounting.

Consequences: Steady state permits one blob plus one current materialization per consuming service for each set. Publication permits one temporary replacement per key. Old runtime caches become evictable after holders drain. Weight references retain blobs. Idle cache associations permit eviction, while leases pin both. Preview and final speech may cost two additional copies. Archiving one installation cannot remove another's shared weight or active cache.

Reconsider when: F2 establishes shared-group or scoped read-only loading with lower cost and equivalent ownership.

### D-041: Compatible local model imports

Identify models through canonical manifests covering weights, tokenizer, templates, feature configuration and companions. Compatibility validates architecture, structure, loading and result contracts. Publisher labels, benchmarks and quality scores may be unknown. Empty recognition, empty diarization and immediate generation end-of-sequence are valid contract-shaped outcomes.

Compatible user-created models must remain usable without invented metadata or catalog approval.

Alternatives considered:

- Require a catalog entry: Simplifies support claims while excluding compatible local assets.
- Reject empty smoke-test output: May detect poor models while misclassifying valid output as incompatibility.

Consequences: The manager supports browsing, import, removal and mode selection. It labels unmeasured quality and separates declared language coverage from evaluated coverage. Resource admission remains separate. Download catalogs require exact hashes and component notices. SpeakerKit redistribution needs an asset-specific license inventory. Local import does not depend on catalog publication.

Reconsider when: Another model family requires additional structural or hardware checks.

### D-042: Long-input working memory

Adapters declare finite buffers and configured ceilings. Decode incrementally. Supply bounded audio windows to recognition and diarization. Bound generation context and output batches. Faithful rewriting processes owned source spans with neighbors supplied only as context. Notes stage cited items and reduce them through bounded fan-in passes. UI, Store and delivery preflight page accumulated data.

Transport credit cannot prevent hidden whole-input accumulation inside a runtime or interface adapter.

Alternatives considered:

- Limit recording duration: Bounds work by refusing required long recordings.
- Trust streaming API names: Avoids investigation while leaving retained allocations unexamined.

Consequences: Disk output and processing time may grow with duration. Generation ceilings apply per unit, with truncation held or retried using a new bounded configuration. Overall recordings are never silently truncated. Target operation sizes are checked against route chunk support before delivery. Content exceeding an actual route limit holds with explicit alternate actions. Boundary fidelity requires review. Measurements and inspection jointly evaluate I7.

Reconsider when: A runtime exposes a stronger incremental contract or boundary errors justify another bounded strategy.

### D-043: Diarization checkpoint ownership

DiarizationService returns one stream window through D-003. The app coordinator validates the epoch, copies output into run-owned staging and applies D-023's clipping. It scans prior durable centroids through a 256-entry cache and paged disk access. Write a window payload containing output, centroids, overlap summaries and proposals. Synchronize payloads before appending a checksum-protected checkpoint containing stream ID, input hash, window index, parameters, runtime revision and artifact lengths. Acknowledge service output after that commit.

Resumable evidence must outlive disposable service scratch. App-side vector comparisons can use bounded memory.

Alternatives considered:

- Keep checkpoints only in service scratch: Avoids copying while coupling resume to execution-specific cleanup.
- Publish each window immediately: Simplifies progress display while exposing incomplete analysis as a completed result.

Consequences: Recovery accepts the checkpoint prefix and discards unfinished tails. Resume starts at the next stream/window under pinned inputs. Earlier centroids remain app-owned. New clusters receive IDs without a speaker-count quota. Output and centroid storage grow on disk. Complete coverage of selected streams enters hidden preparation and publishes once under I2. Cancellation removes staging after leases drain. Service cleanup cannot remove app checkpoints.

Reconsider when: A tested incremental or whole-input diarizer improves continuity while satisfying I7.

## Requirement coverage

- Automatic mode selection (D-014, D-006, D-032): Ordered application and website rules resolve once with explicit precedence, unavailable-input fallback and a visible effective mode.
- Built-in modes (D-013, D-012): Each built-in defines permitted transformations and question preservation, with specified output checks and their limits.
- Context (D-016, D-039, D-028, D-032): Application text, screen OCR and clipboard data have bounded sampling, retention choices, permission behavior and subordinate instruction authority.
- Custom modes (D-012, D-029, D-041): Users control prompts, formatting and local model selections through immutable revisions, including delivery policy for unavailable checks.
- Desktop behavior (D-025, D-036): Menu bar access, microphone gain and identified-player controls include continuing-ownership requirements and explicit resumption where automatic restoration is unsupported.
- History (D-024, D-029, D-005, D-030, D-031, D-033): Search, playback and reprocessing retain provenance. Tombstones prevent resurrection. Representation retirement preserves active playback while permitting later reclamation.
- Input sources (D-018, D-019, D-030, D-042): Microphone, system audio and audio/video imports use bounded ingest with explicit formats, owned decoded storage and unchanged external sources on failure.
- Languages (D-017, D-020, D-021, D-041): Recognition selection and detection define tentative preview, fallback and held states. Transcript-to-English translation uses GenerationService and preserves its source.
- Meetings (D-023, D-026, D-037, D-043, D-042): Dedicated meeting setup selects sources and protects capture from dictation shortcuts. Local per-stream analysis supplies editable speaker interpretation and timestamped notes with durable checkpoints.
- Models (D-020, D-021, D-022, D-028, D-033, D-038, D-040, D-041): Local management and compatible imports use explicit identities, staged checks and transactional activation. Runtime replacement has bounded materialization effects without quality-score gates.
- Recording controls (D-026, D-006, D-007, D-032): Global toggle and push-to-talk include per-mode bindings, submission intent, missed-release recovery and stage-specific cancellation. Conflicting meeting controls have explicit outcomes.
- Recording display (D-011, D-004, D-027): The movable panel remembers placement and distinguishes provisional text from final output, with preview residency, meeting status and visible failures.
- Text delivery (D-008, D-009, D-010, D-032): Scoped profiles and revocable remembered routes define evaluated, unprofiled and version-drift behavior. Qualified AX insertion permits modifier-requested submission after revalidation. Unacknowledged insertion requires inspection. Clipboard barriers protect delayed paste while independent delivery may continue.
- Vocabulary (D-015, D-012): Recognition bias, replacements and filler removal have ordered behavior and preserve original recognition without guaranteeing terminology accuracy.

## Open questions

- Do pinned adapters execute through the selected signed service arrangement? Resolution: D-003 chooses the arrangement. F2 is required before adapter acceptance. Failure requires compliant integration work. If no compliant adapter supplies a required operation, that integration becomes blocking.
- Which exact model configurations should be offered as defaults on the evaluation Mac? Resolution: F1 and F5 select asset revisions within D-027's provisional envelopes. Runtime families, initial evaluation languages and translation execution are decided. Default measurements do not prevent implementing compatible imports.
- Which delivery routes meet the practical interaction threshold across evaluated, unprofiled and changed-version destinations? Resolution: D-009 defines all operating states and persistence scopes. F4 measures each, including paragraph handling and requested submission. Failure returns to the owner with a concrete integration or behavior proposal before routine support is advertised.
- Does optional compression provide enough storage benefit to enable automatically or to favor ALAC over LZFSE? Resolution: D-030 selects default PCM retention and optional LZFSE with no assumed savings. Its codec-independent publication and retirement protocol is settled. F3 compares decoded fidelity, storage, CPU use and seeking before changing the default.
- Can voice processing reduce echo from actual meeting-app playback without harming local speech? Resolution: D-023 selects separate streams with disclosed duplicate-content risk. F5 evaluates the actual reference path before enabling automatic processing.
- Can whole-input SpeakerKit analysis satisfy I7 and reduce speaker-confirmation work? Resolution: Windowed per-stream analysis is selected. F5 compares continuity, correction effort and allocation growth. Flat measurements with bounded internals may justify replacing the window strategy.
- Can the complete local stack support Intel Macs acceptably? Resolution: D-001 selects initial arm64 packaging. Intel support requires identified hardware, speech and generation evaluation, and a compatible local diarizer before advertising support.
- Which diarization assets may be offered through optional downloads? Resolution: Compile exact component hashes and notices before catalog publication. Local import and runtime integration proceed independently of redistribution permission.
- Which real players expose ownership-preserving conditional resumption? Resolution: D-025 supplies explicit resumption when ownership evidence is insufficient. Automatic restoration requires documented semantics and competing-action trials.
- Do the selected capture thresholds and resource envelopes meet their acceptance criteria? Resolution: F3 evaluates D-006's bounded backlog and interruption behavior. The finite resource protocol fixes precision before confirmation. Inconclusive evidence blocks the affected integration's acceptance and requires better instrumentation or implementation, without becoming an application usage quota.

## Review

{"findings": [{"id": "F011", "targets": ["D-030", "D-006", "verification"], "blocking": false, "problem": "D-030 applies a gain edit at the next capture chunk boundary and says preview and waveform use that effective gain, but it does not state whether the boundary is fixed when frames are admitted to the ring or when the writer converts them. D-006 allows the writer to fall up to sixty seconds behind admission, so the two readings diverge exactly when a user is most likely to touch the slider.", "basis": "Scenario: an F3-style write stall leaves the writer 30 seconds behind. The user sees a low waveform and raises gain from 1.0 to 2.0. If gain is latched at write time, the chunks written next cover audio admitted 30 seconds earlier; they are saved at 2.0 with permanent saturation, their headers record 2.0, and the waveform showed them at 1.0. If gain is latched at admission time, saved audio matches the display. The chosen representation is post-gain and clipping is irrecoverable, so the ambiguity decides which audio is permanently altered. 'Frame ranges locate gain changes' also sits oddly beside 'each chunk has one effective gain'; if sub-chunk ranges are intended, the per-chunk header field is insufficient, and if not, the sentence adds nothing. State that gain is latched per chunk at admission, independent of writer backlog, and add a gain change during F3's injected stalls to the chunk-boundary-timing check."}, {"id": "F012", "targets": ["D-026", "D-007", "verification"], "blocking": false, "problem": "D-026 makes the global cancellation binding eligible for 'a live dictation or explicitly selected processing operation', and eligible bindings consume the key gesture so the foreground application never receives it. Nothing states whether a processing operation selected in the app's own UI remains 'explicitly selected' while another application is frontmost.", "basis": "Scenario: cancellation is bound to Escape. The user selects a running Note rewrite in History to watch its progress, switches to Slack, and presses Escape to dismiss a popover. Under the text as written the binding matches the selected run, the event is consumed, D-007 discards the incomplete generation output, and Slack keeps its popover open. The user bound Escape expecting it to cancel live dictation, which is the case F5's emoji-picker fixture covers; the fixture does not exercise a selected background operation. Restrict global-binding eligibility to live dictation, or to selected operations only while the app is frontmost, and add the background-selection case to the Escape fixtures so pass-through is verified."}]}

The corrections resolve F011 and F012 without contradicting their dependent contracts. All required design checks are true. Earlier resolutions and dismissals remain supported, and no required correction remains open. Acceptance permits phased implementation planning. The specified experiments remain unperformed and are still required before accepting dependent integrations or making release claims.

Workflow evidence: evidence/bounded-001/receipt.json
