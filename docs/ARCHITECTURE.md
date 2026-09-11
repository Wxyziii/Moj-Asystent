# Architecture

## High-level system

```text
Microphone / keyboard / overlay
            |
            v
+-----------------------------+
| Desktop UI (Tauri + React)  |
| overlay, tray, confirmations|
+-------------+---------------+
              |
       WebSocket / local API
              |
              v
+-----------------------------+
| Assistant Core (Python)     |
| state, routing, permissions |
+---+------------+------------+
    |            |
    |            +-------------------------------+
    |                                            |
    v                                            v
Audio pipeline                             Context engine
wake -> VAD -> STT                         window/UI/screen/system
    |                                            |
    +-------------------+------------------------+
                        v
                  Orchestrator
                        |
          +-------------+-------------+
          |                           |
          v                           v
     deterministic                 local AI
     direct paths                 model router
          |                           |
          +-------------+-------------+
                        v
                   Tool engine
                        |
                 Permission gate
                        |
                        v
        Windows / files / apps / telemetry
                        |
                        v
                response builder
                        |
                 overlay + TTS
```

## Components

### 1. Desktop application

Responsibilities:

- system tray;
- overlay windows;
- global fallback shortcut;
- onboarding;
- settings;
- approval cards;
- progress/task UI;
- user-selected screen region;
- display of assistant state.

The desktop UI should not contain model-specific business logic.

### 2. Assistant core

Long-running local service responsible for:

- assistant state machine;
- audio pipeline;
- model routing;
- tool registry;
- permissions;
- context gathering;
- watchers;
- persistence;
- response construction.

Use typed internal events so the UI can be replaced without rewriting core behavior.

### 3. Audio pipeline

Idle path must remain cheap:

```text
microphone
   -> rolling in-memory buffer
   -> openWakeWord
   -> no wake: discard
   -> wake: LISTENING
   -> Silero VAD determines utterance end
   -> faster-whisper (language=pl)
   -> orchestrator
```

Raw recordings are not persisted by default.

The microphone callback crosses a bounded thread-safe queue and never mutates
assistant state. The asyncio-owned audio orchestrator uses generation and
operation IDs for cancellation ownership, runs wake/VAD/STT/TTS model work off
the event loop, suppresses wake input during playback and clears utterance
buffers after every terminal path.

### 4. Context engine

Build one normalized context snapshot from available sources:

```json
{
  "active_application": "Visual Studio Code",
  "window_title": "resolver.ts",
  "ui": {},
  "selection": null,
  "screenshot_available": true,
  "system": {
    "cpu_percent": 31,
    "gpu_percent": 62,
    "vram_used_mb": 5200
  }
}
```

Do not collect every field for every request. Context providers should be lazy and request-scoped.

### 5. Orchestrator

Responsibilities:

- understand user intent;
- decide whether deterministic routing is sufficient;
- choose model tier when AI is required;
- request relevant context providers;
- manage tool-call loops;
- construct concise spoken answer + richer visual answer;
- preserve follow-up context for a short conversation window.

The model is advisory until a typed tool passes validation and authorization.

### 6. Model providers

Interface example:

```python
class ModelProvider(Protocol):
    async def chat(self, request: ModelRequest) -> ModelResponse: ...
```

Initial providers:

- Ollama/Qwen3.5 4B;
- optional Ollama/Qwen3.5 9B;
- llama.cpp hybrid provider for larger models.

Model selection is not a UI concern.

### 7. Tool engine

Each tool declares:

- stable name;
- typed arguments;
- return schema;
- permission class;
- optional app/path allowlist requirement;
- timeout;
- audit metadata.

Example:

```python
@tool(name="set_application_volume", permission="write.safe")
async def set_application_volume(application: str, volume: int) -> ToolResult:
    ...
```

Do not make `run_any_shell_command` a normal tool.

The Milestone 6 implementation exposes exactly the plan's 18 stable tools through one registry. Pydantic schemas reject unknown fields before policy evaluation. The conversation orchestrator owns the bounded model → tool → result loop; provider adapters only transport Ollama's structured function calls, and React never executes tools. Tool status/result events are correlated by operation and call IDs under Protocol `1.3`.

`read_ui_tree` and `inspect_screen` currently expose typed provider seams but return an honest unavailable result. Their Windows context implementations remain Milestone 7.

The Milestone 6 implementation exposes exactly the plan's 18 stable tools through one registry. Pydantic schemas reject unknown fields before policy evaluation. The conversation orchestrator owns the bounded model → tool → result loop; provider adapters only transport Ollama's structured function calls, and React never executes tools. Tool status/result events are correlated by operation and call IDs under Protocol `1.3`.

`read_ui_tree` and `inspect_screen` currently expose typed provider seams but report unavailable. Their Windows context implementations remain Milestone 7.

### 8. Permission engine

Authorization occurs after intent/tool selection and before execution.

Permission classes:

- `read`
- `write.safe`
- `sensitive`

Policy can include per-tool, per-app, per-path and one-time approvals.

The initial persisted policy is deliberately smaller: validated per-tool preferences for `write.safe`, stored under the current user's local application-data directory. Corrupt policy fails closed. Sensitive tools ignore stored allow preferences and require an expiring, single-use ticket bound to the exact normalized request. A separate action credential retained in Tauri authorizes only confirmation resolution; the webview retains the ordinary session credential but cannot directly authorize actions.

The initial persisted policy is deliberately smaller: validated per-tool preferences for `write.safe`, stored under the current user's local application-data directory. Corrupt policy fails closed. Sensitive tools ignore stored allow preferences and require an expiring, single-use ticket bound to the exact normalized request. A separate action credential retained in Tauri authorizes only confirmation resolution; the webview retains the ordinary session credential but cannot directly authorize actions.

### 9. Watcher engine

Watchers use ordinary event/process/window/file APIs and only escalate to AI when interpretation is necessary.

```text
OS event -> cheap rule -> relevant? -> collect context -> AI if needed -> notification
```

This prevents continuous LLM/vision use.

### 10. Persistence

SQLite stores structured state:

- settings;
- permissions;
- conversation metadata;
- user-approved memories;
- aliases;
- routines;
- watchers;
- action/audit history.

Screenshots, microphone recordings and model prompts containing sensitive context should not be retained by default.

## Process model

Preferred initial process layout:

```text
Moj-Asystent.exe / Tauri desktop
        |
        +--> local core service process
                 |
                 +--> Ollama / llama.cpp runtime
```

The desktop app owns lifecycle/startup UX; the core owns assistant behavior.

## Communication protocol

Tauri creates a fresh memory-only credential and passes it to its owned core
child process for the lifetime of one application launch. The credential is
required before any health, audio-control or WebSocket traffic is accepted.
The HTTP health response is validated before the desktop opens a WebSocket. A
WebSocket session begins with `client.hello`; the core correlates its health
response and authoritative state snapshot to that hello event. The desktop
becomes connected only after both messages validate. It retries failed or
closed sessions with bounded exponential backoff and ignores callbacks from
superseded sessions. Every server event in the session carries the hello event
ID as its correlation ID, so an event from an older session cannot be accepted
by a replacement connection.

The core is the sole owner of assistant state. It mutates and publishes state
on one asyncio event loop. Per-client queues are bounded; a client that cannot
keep up is disconnected and must reconnect for a fresh snapshot. Shutdown
cancels active session tasks before lifecycle teardown completes.

Use a shared schema package for messages such as:

- `assistant.state.changed`
- `audio.transcript.final`
- `assistant.response.started`
- `assistant.response.delta`
- `assistant.response.completed`
- `model.status.changed`
- `system.health`

This is the implemented Protocol `1.2` set. Response streams carry one
operation ID and strictly increasing sequence numbers; reconnecting clients
cannot accept events correlated to an older WebSocket hello. The other planned event families
are added only with their owning milestones.

Messages should include IDs so long-running actions can be correlated.

## Screen-awareness strategy

Priority:

1. active application and window title;
2. Windows UI Automation;
3. known structured logs/integrations;
4. selected region or active-window screenshot;
5. full monitor screenshot only when explicitly necessary.

Never run continuous image inference simply to know whether something changed.

## Model routing strategy

```text
simple known command -> deterministic intent/tool
normal conversation/tool reasoning -> Qwen3.5 4B
harder local task -> Qwen3.5 9B if resources permit
explicit/deep complex task -> larger llama.cpp hybrid tier
```

Before loading a larger tier, check available VRAM/RAM and current gaming/high-load state.

Milestone 5 implements only the normal conversation branch through the
replaceable local Ollama provider. Tool selection, telemetry-aware routing and
larger tiers remain assigned to their later milestones.

## Failure model

Every external subsystem can fail independently:

- wake-word engine unavailable -> fallback hotkey/text works;
- STT unavailable -> typed chat works;
- TTS unavailable -> overlay text works;
- model unavailable -> deterministic tools still work where safe;
- UI Automation unavailable -> optional screenshot path;
- telemetry source unavailable -> report missing metric, never fabricate;
- tool failure -> surface actual tool result and recovery options.

## Privacy defaults

- wake audio processed locally;
- rolling buffer in RAM only;
- no screenshot storage by default;
- no cloud calls by default;
- obvious visual indicator during active listening/screen inspection;
- configurable excluded applications/windows;
- secrets/password fields should be redacted or excluded when detectable.
