# Decision Log

## 2026-09-09 — Versioned local protocol boundary

Decision: desktop and core exchange explicit protocol-v1 envelopes over a localhost-only HTTP/WebSocket boundary. Every event contains a protocol version, event ID, UTC timestamp and nullable correlation ID. Unknown event types, extra fields and incompatible versions are rejected before they reach assistant state handling.

Compatibility rule: the value is negotiated as an exact supported version, not as an implicit semantic-version range. A `1.3` client therefore rejects `1.4`. Protocol `1.4` is the current exact capability set and adds normalized provider, model-tier, location and fallback metadata to the `1.3` event set. During a migration the core may support multiple exact versions, but this implementation intentionally supports only `1.4`. Any breaking schema or behavior change requires a new major version.

Connection rule: the desktop proves liveness through a bounded health check and a correlated WebSocket handshake, then reconnects with exponential backoff capped at 30 seconds. The core owns the authoritative state on one event loop and correlates every outbound event to that session's hello event. Event IDs are unique within a stream and duplicate IDs are rejected; events from superseded desktop connection attempts are ignored.

Reason: it allows the Tauri UI and Python core to evolve independently without treating local payloads as trusted or silently changing assistant behavior.

## 2026-09-10 — Loopback Ollama conversation provider

Decision: the first main chat model is `qwen3.5:4b` through a replaceable
provider interface. The provider accepts only explicit HTTP loopback origins,
does not use environment proxy settings, validates bounded streaming frames and
never exposes Ollama-specific payloads to the desktop. Model weights are an
explicit local installation rather than an automatic application download.

Short conversation history is bounded and memory-only. Full overlay text uses
the streamed model answer; spoken output is a deterministic short derivative
of that answer. Persistent memory, tool calls and hardware-aware model routing
remain later milestones.

Reason: this establishes useful private local chat while keeping model runtime,
UI, future memory and future tool authorization as separate responsibilities.

## 2026-09-12 — Deterministic local-first model routing

Decision: the conversation orchestrator requests a model tier and required
capabilities from one deterministic router. Fast `qwen3.5:4b` and optional
Quality `qwen3.5:9b` use loopback Ollama; experimental Deep uses an explicitly
configured Qwen3.5 27B GGUF through loopback llama.cpp CPU/GPU hybrid offload.
The router considers request structure, bounded context size, capability needs
and request-driven RAM/VRAM/GPU-load telemetry. It does not call another model
to make the routing decision.

The stored data policy defaults to `local_only`. OpenRouter is an optional,
fixed-origin, text-only provider and is considered only after explicit
`cloud_allowed` opt-in; private mode, secret-like content, persistent memory,
screenshots and tool-required requests stay local. Provider choice never changes
the ToolEngine, permission, confirmation, watcher, memory-authorization or OS
safety boundaries. Downgrades are deterministic and visible in Protocol 1.4.

Secrets are accepted only by the core process environment, removed immediately
after startup and excluded from React, SQLite, ordinary configuration, logs and
tool child environments. Native Windows credential provisioning remains a
future hardening option; cloud is not required for V1.

Reason: normal voice interactions need low latency and predictable resource use,
while difficult analysis benefits from opt-in stronger tiers. One provider-
neutral router preserves local-first privacy and the existing authorization
architecture without making a large model permanently resident.

## 2026-09-10 — Per-launch local core credential

Decision: Tauri creates two distinct high-entropy credentials for each desktop launch and passes both only to the child core process environment. The ordinary session credential is exposed to the trusted application webview for health, chat, audio and WebSocket traffic. The action credential remains in Rust and can be used only by a narrow, typed confirmation command; it is never exposed to React. Credentials are never persisted or logged, and the owned core process is terminated with the desktop session.

Reason: loopback binding prevents remote access but does not by itself distinguish the desktop from unrelated local web pages or processes. This is a narrow application-session boundary, not an account system or a claim of isolation from malicious software already running as the same OS user.

## 2026-09-11 — Deterministic tool authorization boundary

Decision: model tool calls are untrusted proposals. Only names registered in the central tool registry can reach deterministic implementations, and only after strict argument validation and permission evaluation. `read` tools run automatically, `write.safe` follows a validated user-local policy that defaults to asking, and `sensitive` always requires a fresh confirmation. Persistent approval is never available to sensitive tools.

Confirmation tickets are unpredictable, expire after 90 seconds, are single-use and bind the operation ID, call ID, tool name and canonical argument digest. Replacing or cancelling an operation, core restart, or loss of the last authenticated UI event stream invalidates its pending tickets; no confirmation-requiring action may create a new ticket without an authenticated event subscriber. Destructive filesystem paths are canonicalized and policy-checked before the ticket is shown, then revalidated immediately before execution. Tool results—not model text—are the sole authoritative record of execution success.

Reason: this preserves the rule that AI chooses what to propose, deterministic code controls how it is performed, and policy/user confirmation decides whether it may execute. It also prevents a compromised or malformed model response from manufacturing authorization or success.

## 2026-09-11 — Request-driven bounded telemetry

Decision: system diagnostics run only through the existing typed read-tool
boundary. The core takes two short psutil samples and an optional NVML sample,
normalizes them into a bounded immutable snapshot and retains at most twelve
snapshots for sixty seconds in memory. Volume capacity and system-wide disk I/O
are explicitly scoped; unavailable metrics and counter resets remain visible
instead of being guessed. Process correlation is limited to PID/name, CPU, RSS,
I/O and GPU VRAM, with no command lines or executable paths.

Reason: this provides useful evidence for diagnostic questions without creating
a background monitor, persistent telemetry database or a new UI/core protocol
surface, and keeps OS observations separate from authorization and execution.

## 2026-09-09 — Polish-only V1

Decision: V1 supports Polish only.

Reasons:

- no language auto-detection needed;
- STT can force `pl`;
- one TTS language/voice path;
- wake-word confusable generation can target Polish phonetics;
- easier end-to-end evaluation and prompt tuning.

Future multilingual support remains possible because language is configuration, not baked into tool schemas.

## 2026-09-09 — User chooses assistant name

Decision: the assistant has no hardcoded public name.

During onboarding the user:

1. chooses a name;
2. calibrates microphone;
3. follows guided recording prompts;
4. records hard-negative/confusable phrases;
5. trains/builds a local wake model;
6. validates activation and false positives.

The user can rename/retrain later.

## 2026-09-09 — Guided wake training

Decision: never ask the user for generic unlabeled wake-word samples.

Each prompt must state:

- exact phrase;
- volume/style;
- microphone distance/position;
- intended intonation;
- whether it is a wake or non-wake example.

Reason: better training data and less user guesswork.

## 2026-09-09 — Overlay-first desktop UX

Decision: voice is the hands-free entry point, but the overlay is the primary visual interface.

Overlay handles:

- listening/thinking states;
- chat;
- rich results;
- confirmations;
- progress;
- proactive suggestions;
- screen-inspection indicator.

A hotkey remains as fallback.

## 2026-09-09 — Local-first architecture

Decision: V1 must not require a cloud AI service.

Cloud providers may be added later, disabled by default and never silently receive private screen/audio/file context.

## 2026-09-09 — Qwen3.5 4B as initial main model

Decision: begin with Qwen3.5 4B for the main local model and vision path.

Reason:

- practical for the initial RTX 3070 8 GB target;
- lower latency matters more than maximum model size for voice-assistant UX;
- preserves resources for the desktop and vision/context overhead.

Qwen3.5 9B is an optional quality tier. A larger Qwen/other candidate via llama.cpp CPU/GPU hybrid is experimental deep mode only.

Model choices must remain provider-configurable and can change after project benchmarks.

## 2026-09-09 — CPU/GPU work split

Decision: prefer CPU for lightweight supporting models and GPU for the main LLM on the initial hardware target.

Initial split:

- CPU: wake word, VAD, faster-whisper INT8, TTS, telemetry, orchestration;
- GPU: Qwen main model/vision;
- hybrid CPU+GPU: optional larger deep model.

Benchmark before hardcoding exact model sizes/settings.

## 2026-09-12 — Deterministic local Voice v2 routing

Decision: supersede the STT part of the initial CPU-first split on capable target
hardware. Prefer `large-v3-turbo` on CUDA with `int8_float16`, then a configured
local fallback and finally Medium CPU `int8`. Selection is deterministic and
based only on local runtime/model availability. Every model is opened with
local-files-only behavior; installation remains an explicit user/setup action.

The UI reads authenticated status and manages a local vocabulary but contains no
CUDA selection logic. Transcript confidence is an intentionally conservative
internal safety hint: low confidence can require a fresh ToolEngine confirmation
for a state-changing action and can never grant permission. Diagnostics remain a
bounded in-memory metadata buffer without audio or transcript content.

These controls fit the authenticated HTTP settings boundary, so no new desktop
event capability is required and Protocol 1.4 remains exact and unchanged.

## 2026-09-09 — Structured context before screenshots

Decision: screen awareness prioritizes structured context.

Order:

1. active app/window;
2. Windows UI Automation;
3. known structured app/log integrations;
4. active-window/selected-region screenshot;
5. full monitor capture only if justified.

No continuous full-screen vision inference.

## 2026-09-11 — Ephemeral multimodal screenshot side channel

Decision: screenshot bytes travel only as a single-use in-memory attachment to
the existing Ollama conversation request. Ordinary tool results and Protocol
events contain metadata only. Region selection uses an authenticated local HTTP
endpoint and returns a reduced preview to the webview; this does not change the
Protocol 1.3 event schema.

Reason: this preserves one conversation/provider boundary, prevents image data
from entering history or the event stream, and makes completion, cancellation,
expiry and shutdown cleanup deterministic.

## 2026-09-11 — Request-scoped Windows context on a dedicated COM worker

Decision: Milestone 7 provides structured active-window and Windows UI
Automation context only on demand through typed tools. All UI Automation access
runs on one bounded, dedicated MTA worker that owns COM objects for their entire
lifetime. Snapshot traversal is bounded and cancellable, and stale results are
discarded when the foreground-window identity changes.

Screenshot and selected-region capture move together with image normalization
and vision to Milestone 8. Until then, `inspect_screen` returns a typed,
truthful unavailable result. This keeps the privacy boundary explicit and avoids
shipping image collection without its user-visible capture and model-consumption
flow.

## 2026-09-09 — Typed tools, not arbitrary shell

Decision: ordinary assistant actions use typed allowlisted tools.

The model selects a capability and arguments; deterministic code validates and executes it.

Arbitrary shell execution is excluded from normal V1 behavior.

## 2026-09-09 — Three permission classes

Decision:

- `read`
- `write.safe`
- `sensitive`

Sensitive actions require explicit confirmation. Persistent approvals are allowed only for categories explicitly designed as safe.

## 2026-09-09 — Short follow-up conversation window

Decision: after wake and a response, keep a temporary follow-up listening mode so the user does not repeat the assistant name before every sentence.

Initial target: configurable ~15–30 seconds of inactivity before returning to wake-only mode.

## 2026-09-09 — Piper first, higher-quality TTS later

Decision: begin with Piper for fast local Polish TTS and keep a provider abstraction.

Evaluate XTTS-v2 or another high-quality Polish TTS later; do not block V1 foundation on premium voice quality.

## 2026-09-09 — SQLite structured memory first

Decision: begin with SQLite structured state and explicit retrieval.

Do not add a vector DB/embedding system until there is a demonstrated semantic-retrieval need.

## 2026-09-11 — Milestone 10 local persistence boundary

Decision: use a versioned SQLite database under per-user application data for
explicit preferences, memories, aliases, opt-in completed conversation history
and bounded typed routines. Open a short-lived connection per operation with
WAL, foreign keys and a fail-closed migration path. Retrieval remains
deterministic and bounded; durable extraction, embeddings and watchers remain
out of scope.

The existing validated permissions JSON remains separate from SQLite. Policy
writes have independent fail-closed semantics and should not be removed by a
user's memory reset. Routines call the central tool engine rather than adding
a second execution path, so existing validation, policy, confirmation and
cancellation guarantees remain authoritative.

## 2026-09-09 — Proactive behavior must be event-driven

Decision: background watchers use deterministic OS/process/file/resource events and invoke AI only when interpretation is needed.

Reason: lower CPU/GPU use, better privacy and more predictable behavior than constant LLM/vision monitoring.

## 2026-09-11 — Watchers share the local SQLite lifecycle

Decision: Milestone 11 stores watcher definitions, last observations and a
bounded event history in the existing versioned SQLite database. One
asyncio-owned scheduler restores only revalidated active watchers, caps
concurrency and publishes meaningful transitions as authenticated Protocol 1.3
notifications. Watcher events are notification-only and cannot authorize tools
or trigger routines. Explicit watcher management is exposed as confirmation-
gated typed ToolEngine definitions through a small event-loop bridge; watcher
operations are never executed from the observation callback itself.

Reason: a single lifecycle and migration path keeps restart, cancellation,
privacy deletion and fail-closed identity validation consistent with Milestone
10 without introducing a second service or database.

## 2026-09-09 — GitHub repository is source of truth

Decision: architecture, roadmap, decisions and changelog live in versioned repository docs.

The development website is a visual presentation layer. If the website and docs disagree, docs win and the website should be updated.

## 2026-09-10 — Local ONNX custom wake training

Decision: implement custom wake-name training locally with the installed openWakeWord feature extractor as a frozen embedding backbone and a small deterministic ONNX classifier, while keeping runtime activation behind the existing `OpenWakeWordProvider` interface.

The installed runtime package is inference-focused and does not expose the upstream training notebook/tooling. The local trainer therefore uses only user-provided recordings, bounded seeded augmentation and atomic model activation. The upstream documentation is English-oriented, so Polish quality must be demonstrated by the onboarding validation flow and may require retraining or an explicit user override. No recordings, datasets or personal models are committed.

## 2026-09-10 — Browser-owned onboarding capture

Decision: during first-run/retraining, the desktop webview temporarily owns microphone capture for guided recording while the core pauses its live audio source. The core remains responsible for validation, storage, training and runtime activation. If onboarding is cancelled, the previous capture/model configuration is restored; if activation succeeds, capture resumes with the new model.
