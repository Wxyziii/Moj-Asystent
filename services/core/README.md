# Assistant Core

Target: **Python 3.12 + FastAPI + WebSocket**.

This directory will contain the long-running local assistant service.

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
    assistant_core/
      api/
      audio/
        wakeword/
        vad/
        stt/
        tts/
      ai/
        providers/
        router/
        prompts/
      context/
        windows/
        screen/
        telemetry/
      tools/
      permissions/
      memory/
      watchers/
      state/
```

## First implementation tasks

1. Create the Python package and dependency management.
2. Implement FastAPI health endpoint + WebSocket.
3. Implement typed assistant-state events.
4. Add provider protocols with fake/test implementations.
5. Connect desktop shell to simulated core events before adding model/audio dependencies.

See `docs/ARCHITECTURE.md` and `docs/IMPLEMENTATION_PLAN.md`.
