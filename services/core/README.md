# Assistant Core

Target: **Python 3.12 + FastAPI + WebSocket**.

This directory contains the long-running local assistant service. Milestone 2
provides a Python 3.12 FastAPI boundary at `127.0.0.1:8765` by default.

## Running locally

Run `uv sync --all-groups`, then `uv run moj-asystent-core` from this directory.
The service exposes `GET /health` and `/ws`. WebSocket clients must send a
protocol-v1 `client.hello` before receiving health and state synchronization.
Invalid origin, size, schema and protocol-version inputs are rejected.

Assistant state is owned by one asyncio event loop and published through
bounded per-client queues. New and reconnected clients receive a correlated,
authoritative snapshot. Slow clients are disconnected so they cannot block the
core. Shutdown cancels active WebSocket session work before lifecycle teardown.

No model, audio, microphone, screen, tool or persistence implementation is
included at this stage.

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
