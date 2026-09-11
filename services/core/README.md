# Assistant Core

Target: **Python 3.12 + FastAPI + WebSocket**.

This directory contains the long-running local assistant service. Milestones 2–6
provide a Python 3.12 FastAPI boundary, Polish audio pipeline and local chat at
`127.0.0.1:8765` by default.

## Running locally

Run `uv sync --all-groups`, set distinct fresh `MOJ_ASYSTENT_SESSION_CREDENTIAL`
and `MOJ_ASYSTENT_ACTION_CREDENTIAL` values, then
run `uv run moj-asystent-core` from this directory. Normal desktop development
does this automatically through the Tauri-owned process lifecycle.
The service exposes authenticated `GET /health`, `GET /model/status`,
`POST /chat`, `POST /chat/cancel`, `POST /audio/listen`, `POST /audio/cancel`,
`POST /shutdown` and `/ws`. WebSocket clients must send a
protocol-v1 `client.hello` before receiving health and state synchronization.
Invalid origin, size, schema and protocol-version inputs are rejected.
Only the Tauri-held action credential can resolve a pending tool confirmation.

Assistant state is owned by one asyncio event loop and published through
bounded per-client queues. New and reconnected clients receive a correlated,
authoritative snapshot. Slow clients are disconnected so they cannot block the
core. Shutdown cancels active WebSocket session work before lifecycle teardown.

Milestone 3 includes real sounddevice, openWakeWord, Silero VAD, faster-whisper
and Piper adapters. Model weights stay in user caches/outside Git. Set
`MOJ_ASYSTENT_TTS_VOICE_PATH` to a Polish Piper `.onnx` voice when it is not in
the default local application model directory. Screen, tool and persistence
implementations remain out of scope.

Milestone 4 adds authenticated `/onboarding/*` endpoints for name scoring,
microphone calibration, bounded PCM samples, cancellable local ONNX training,
validation, sensitivity and atomic activation. Sessions and model artifacts are
kept under the user's local application-data directory. The service does not
start live microphone capture until an active model exists.

Milestone 5 adds a loopback-only Ollama provider with `qwen3.5:4b` as the
default. Install Ollama and run `ollama pull qwen3.5:4b`; the desktop then shows
provider readiness and streams Polish replies. Override only with a local
origin through `MOJ_ASYSTENT_OLLAMA_URL` and select a compatible model with
`MOJ_ASYSTENT_LLM_MODEL`. Conversation context is bounded and memory-only.

Milestone 6 adds the central typed tool registry, local permission policy,
structured Ollama tool calling and Protocol 1.3 tool lifecycle events. Read
tools run automatically, `write.safe` defaults to asking, and sensitive tools
always require an exact short-lived confirmation. File access defaults to the
user's home directory. `read_ui_tree` and `inspect_screen` intentionally report
unavailable until their providers are implemented in Milestone 7.

## Responsibilities

- assistant state machine;
- microphone/audio pipeline;
- wake-word provider;
- VAD provider;
- STT provider;
- TTS provider;
- model provider/router;
- tool registry and execution;
- permissions;
- context providers;
- Windows UI/system telemetry adapters;
- memory/persistence;
- watcher engine;
- response construction;
- audit events and privacy-safe logging.

## Expected package layout

```text
services/core/
  src/
    moj_asystent_core/
      api.py
      local_boundary.py
      main.py
      protocol.py
      providers.py
      runtime.py
      state.py
```

See `docs/ARCHITECTURE.md` and `docs/IMPLEMENTATION_PLAN.md`.
