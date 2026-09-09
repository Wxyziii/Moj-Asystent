# Changelog

All notable project changes are tracked here.

## [Unreleased]

### Added

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

- V1 is Polish-only.
- Local processing is the default.
- Qwen3.5 4B is the initial main model candidate.
- Arbitrary shell execution is excluded from normal V1 behavior.
- Continuous full-screen vision inference is excluded.
- The repository documentation is the canonical project specification.
