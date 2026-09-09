# Decision Log

## 2026-09-09 — Versioned local protocol boundary

Decision: desktop and core exchange explicit protocol-v1 envelopes over a localhost-only HTTP/WebSocket boundary. Every event contains a protocol version, event ID, UTC timestamp and nullable correlation ID. Unknown event types, extra fields and incompatible versions are rejected before they reach assistant state handling.

Compatibility rule: additive, backward-compatible changes may increment the minor protocol version after both consumers support them. Any breaking schema or behavior change requires a new major version and is rejected by older clients.

Reason: it allows the Tauri UI and Python core to evolve independently without treating local payloads as trusted or silently changing assistant behavior.

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
