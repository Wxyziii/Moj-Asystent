# Changelog

All notable project changes are tracked here.

## [Unreleased]

### Added

- Milestone 4 custom assistant-name onboarding: Polish suitability scoring, generated confusables, guided recordings, calibration, quality feedback and wake-only validation.
- Local deterministic openWakeWord-compatible ONNX trainer with cancellable jobs, bounded seeded augmentation and atomic activation/retraining.
- Authenticated desktop/core onboarding APIs, shared TypeScript protocol parsers and a polished Polish setup wizard.
- First-run audio remains closed until an active wake model exists; retraining pauses and safely restores the live capture pipeline.

- Milestone 3 Polish voice pipeline: bounded in-memory microphone frames, openWakeWord development activation, Silero VAD speech boundaries, faster-whisper forced to `pl`, Piper `pl_PL` playback and timed follow-up listening.
- Cancellable audio orchestration with operation generations, stale-result suppression, playback feedback blocking, device retry and deterministic Polish placeholder responses without an LLM.
- Protocol `1.1` transcript and completed-response events, validated consistently by JSON Schema, Python and TypeScript.
- Per-launch Tauri/core credential protecting health, audio commands and WebSocket traffic; Tauri starts and owns the development core process and removes it on application exit.
- Desktop rendering of authenticated core transcripts/responses with a clear distinction between live and simulated state.

- Milestone 2 architecture hardening: bounded desktop reconnect backoff, correlated session snapshots, heartbeat liveness checks and stale-session suppression.
- Single-event-loop core runtime with ordered bounded subscriber queues, deterministic multi-client state publication and clean cancellation during shutdown.
- One shared JSON Schema acceptance corpus exercised by JSON Schema, Python and TypeScript validators, plus real Uvicorn restart/shutdown integration tests.

### Fixed

- Audio-provider work and microphone callbacks no longer mutate assistant state outside the runtime's owning asyncio loop.
- Cancellation and shutdown now release RAM audio buffers and close capture/playback resources without allowing late STT/TTS results to change state.

- Desktop now recovers when the core starts late or restarts while the overlay remains open.
- Health requests now allow only known desktop origins, while HTTP and WebSocket Host headers remain restricted to loopback names.
- Binary, oversized, malformed, duplicate, out-of-order and incorrectly correlated protocol messages now fail closed.
- Protocol `1.0` version handling remains exact; clients move to `1.1` explicitly for the Milestone 3 voice events.
- Updated the frontend test runner to a patched release after the dependency audit identified vulnerable development-server code.

- Milestone 2 local assistant core under `services/core`, using Python 3.12, FastAPI and a localhost-only HTTP/WebSocket boundary.
- Protocol v1 schemas and typed Python/TypeScript message contracts for health, handshake, assistant-state and error events.
- Desktop health/handshake client with clear Polish connected, connecting and disconnected states while retaining UI simulation controls.
- Deterministic assistant-state validation, fail-closed mock LLM/STT/TTS/wake-word provider interfaces and protocol/API test coverage.

- Milestone 1 desktop foundation under `apps/desktop`: Tauri 2, React and TypeScript application shell.
- Frameless always-on-top overlay with compact listening bubble, expanded Polish conversation view and settings shell.
- System tray menu, global `Ctrl` + `Shift` + `Spacja` fallback shortcut, hide/show behavior and responsive desktop window sizing.
- Typed simulated assistant UI state transitions for idle, wake detection, listening, transcription, thinking, speaking, follow-up and error states.
- Frontend linting, formatting, type checks and state-transition tests.

- Initial product definition for a Polish-only, local-first Windows AI assistant.
- Tauri + React desktop overlay architecture.
- Python FastAPI assistant-core architecture.
- Custom user-defined assistant name instead of a hardcoded wake word.
- Guided wake-word training plan with microphone calibration, pronunciation/position instructions, hard negatives and validation.
- Polish audio pipeline plan using openWakeWord, Silero VAD, faster-whisper and Piper.
- Qwen3.5 model-routing strategy for fast, quality and experimental deep/hybrid modes.
- Windows screen-awareness strategy prioritizing UI Automation before screenshots.
- Typed tool registry and three-level permission model.
- System telemetry and diagnostic feature plan.
- Persistent SQLite memory/routine architecture.
- Event-driven proactive watcher design.
- Overlay UX specification covering listening, chat, confirmations and proactive suggestions.
- Security/privacy specification.
- GitHub Pages development-status website source and deployment workflow.
- Codex-oriented `AGENTS.md` repository map and source-of-truth documentation structure.

### Decisions

- Protocol versions are exact capabilities; every unsupported version is rejected before state changes.

- V1 is Polish-only.
- Local processing is the default.
- Qwen3.5 4B is the initial main model candidate.
- Arbitrary shell execution is excluded from normal V1 behavior.
- Continuous full-screen vision inference is excluded.
- The repository documentation is the canonical project specification.
