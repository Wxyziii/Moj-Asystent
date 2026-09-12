# Mój Asystent

A local-first, Polish-only desktop AI assistant for Windows with voice activation, an overlay chat interface, screen awareness, system telemetry, memory, and controlled tool execution.

> Project status: **Milestone 13 complete — Voice v2 implementation; manual V1 validation pending**

## Product goal

Build an assistant that feels native to the PC rather than like a chatbot in a separate window. The assistant should be available by voice through a user-trained wake name, appear as a lightweight desktop overlay, understand the current screen and system state, and carry out approved actions through deterministic tools.

Example target interaction:

1. User says the custom assistant name.
2. The assistant wakes without a hotkey and opens a compact overlay.
3. User asks in Polish: `Dlaczego ten program nie działa?`
4. The assistant reads the active-window context and UI Automation tree, captures a screenshot only if needed, and checks relevant telemetry.
5. The local model explains the problem in Polish.
6. The overlay offers explicit actions such as `Wyjaśnij`, `Napraw`, or `Pokaż szczegóły`.
7. Any sensitive action requires confirmation.

## V1 principles

- Polish-only speech and responses.
- Local-first processing.
- Custom assistant name trained during onboarding.
- Wake-word detection runs continuously; full speech recognition starts only after wake.
- Overlay is the primary visual interface.
- Structured Windows information is preferred over screenshots.
- The LLM decides **what** should happen; deterministic code decides **how** it happens; permissions decide **whether** it may happen.
- No unrestricted shell access for the everyday assistant.
- No continuous full-screen vision processing.

## Stack

- **Desktop:** Tauri 2 + React + TypeScript
- **Native desktop integration:** Rust
- **Assistant service:** Python 3.12 + FastAPI + WebSocket
- **Main local model:** Qwen3.5 4B
- **Quality model:** Qwen3.5 9B
- **Experimental deep model:** Qwen3.5 27B using CPU/GPU hybrid inference
- **Model runtime:** Ollama first, llama.cpp for advanced offload/tuning
- **Wake word:** openWakeWord
- **VAD:** Silero VAD
- **STT:** faster-whisper, Polish forced (`pl`), preferred local
  `large-v3-turbo` CUDA with explicit CPU fallback
- **TTS:** Piper initially; XTTS-v2 evaluated later
- **Memory:** SQLite
- **Windows context:** UI Automation + active-window metadata + screenshots when required
- **Telemetry:** psutil + NVIDIA NVML + Windows APIs

## Documentation

Start here:

- [`AGENTS.md`](AGENTS.md) — Codex repository map and engineering rules
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — phased implementation plan
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system architecture and data flow
- [`docs/FEATURES.md`](docs/FEATURES.md) — product feature catalogue
- [`docs/AI_MODELS.md`](docs/AI_MODELS.md) — model strategy and hardware routing
- [`docs/VOICE_SYSTEM.md`](docs/VOICE_SYSTEM.md) — audio pipeline and conversation state machine
- [`docs/WAKE_WORD_TRAINING.md`](docs/WAKE_WORD_TRAINING.md) — custom-name onboarding and training
- [`docs/OVERLAY_UI.md`](docs/OVERLAY_UI.md) — overlay UX specification
- [`docs/SECURITY.md`](docs/SECURITY.md) — permissions and safety boundaries
- [`docs/SYSTEM_DIAGNOSTICS.md`](docs/SYSTEM_DIAGNOSTICS.md) — bounded local telemetry and diagnostic facts
- [`docs/MEMORY_AND_ROUTINES.md`](docs/MEMORY_AND_ROUTINES.md) — local persistence, privacy controls and typed routines
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — milestones and release scope
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — architectural decision log
- [`CHANGELOG.md`](CHANGELOG.md) — project changes

## Development site

A static development site lives in [`site/`](site/) and is intended for GitHub Pages. It presents the current development status, roadmap, architecture, and changelog in a more visual form than Markdown documents alone.

The deployment workflow is in `.github/workflows/pages.yml`.

## Status

Milestones 1–13 are implemented: the Windows overlay, authenticated local core,
Polish audio pipeline, trained custom wake name, local streamed Qwen chat, the
typed tool/permission boundary and request-scoped active-window/UI Automation
context, selective local vision, request-driven system diagnostics, explicit
local SQLite memory/routines and deterministic proactive watchers with bounded
notifications, deterministic hardware-aware Fast/Quality/Deep model routing,
plus Voice v2 CUDA/CPU STT routing, speech-edge buffering, local vocabulary,
diagnostics and benchmark tooling. The final target-hardware voice/model matrix
and documented end-to-end V1 interaction still need manual release-gate
validation before the project is called reliable V1. See `docs/ROADMAP.md` for
the exact scope.
