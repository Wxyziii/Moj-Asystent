# Decision Log

## 2026-09-09 — Versioned local protocol boundary

Decision: desktop and core exchange explicit protocol-v1 envelopes over a localhost-only HTTP/WebSocket boundary. Every event contains a protocol version, event ID, UTC timestamp and nullable correlation ID. Unknown event types, extra fields and incompatible versions are rejected before they reach assistant state handling.

Compatibility rule: the value is negotiated as an exact supported version, not as an implicit semantic-version range. A `1.2` client therefore rejects `1.3`. Protocol `1.3` is the current exact capability set and adds correlated tool status, confirmation and result events to the `1.2` local-model events. During a migration the core may support multiple exact versions, but this implementation intentionally supports only `1.3`. Any breaking schema or behavior change requires a new major version.

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

## 2026-09-10 — Per-launch local core credential

Decision: Tauri creates two distinct high-entropy credentials for each desktop launch and passes both only to the child core process environment. The ordinary session credential is exposed to the trusted application webview for health, chat, audio and WebSocket traffic. The action credential remains in Rust and can be used only by a narrow, typed confirmation command; it is never exposed to React. Credentials are never persisted or logged, and the owned core process is terminated with the desktop session.

Reason: loopback binding prevents remote access but does not by itself distinguish the desktop from unrelated local web pages or processes. This is a narrow application-session boundary, not an account system or a claim of isolation from malicious software already running as the same OS user.

## 2026-09-11 — Deterministic tool authorization boundary

Decision: model tool calls are untrusted proposals. Only names registered in the central tool registry can reach deterministic implementations, and only after strict argument validation and permission evaluation. `read` tools run automatically, `write.safe` follows a validated user-local policy that defaults to asking, and `sensitive` always requires a fresh confirmation. Persistent approval is never available to sensitive tools.

Confirmation tickets are unpredictable, expire after 90 seconds, are single-use and bind the operation ID, call ID, tool name and canonical argument digest. Replacing or cancelling an operation invalidates its pending tickets. Tool results—not model text—are the sole authoritative record of execution success.

Reason: this preserves the rule that AI chooses what to propose, deterministic code controls how it is performed, and policy/user confirmation decides whether it may execute. It also prevents a compromised or malformed model response from manufacturing authorization or success.

This file records product/architecture decisions that should not be silently changed.

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

## 2026-09-09 — Structured context before screenshots

Decision: screen awareness prioritizes structured context.

Order:

1. active app/window;
2. Windows UI Automation;
3. known structured app/log integrations;
4. active-window/selected-region screenshot;
5. full monitor capture only if justified.

No continuous full-screen vision inference.

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

## 2026-09-09 — Proactive behavior must be event-driven

Decision: background watchers use deterministic OS/process/file/resource events and invoke AI only when interpretation is needed.

Reason: lower CPU/GPU use, better privacy and more predictable behavior than constant LLM/vision monitoring.

## 2026-09-09 — GitHub repository is source of truth

Decision: architecture, roadmap, decisions and changelog live in versioned repository docs.

The development website is a visual presentation layer. If the website and docs disagree, docs win and the website should be updated.

## 2026-09-10 — Local ONNX custom wake training

Decision: implement custom wake-name training locally with the installed openWakeWord feature extractor as a frozen embedding backbone and a small deterministic ONNX classifier, while keeping runtime activation behind the existing `OpenWakeWordProvider` interface.

The installed runtime package is inference-focused and does not expose the upstream training notebook/tooling. The local trainer therefore uses only user-provided recordings, bounded seeded augmentation and atomic model activation. The upstream documentation is English-oriented, so Polish quality must be demonstrated by the onboarding validation flow and may require retraining or an explicit user override. No recordings, datasets or personal models are committed.

## 2026-09-10 — Browser-owned onboarding capture

Decision: during first-run/retraining, the desktop webview temporarily owns microphone capture for guided recording while the core pauses its live audio source. The core remains responsible for validation, storage, training and runtime activation. If onboarding is cancelled, the previous capture/model configuration is restored; if activation succeeds, capture resumes with the new model.
