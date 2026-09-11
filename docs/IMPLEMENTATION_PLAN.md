# Implementation Plan

## Objective

Build a Windows-first, Polish-only, local-first desktop AI assistant with:

- custom user-defined assistant name and wake phrase training;
- hands-free wake-word activation;
- Polish speech recognition and speech output;
- a lightweight overlay chat UI;
- screen and active-window awareness;
- system telemetry and diagnostics;
- controlled Windows/app tools;
- explicit permissions for sensitive actions;
- persistent memory and routines;
- proactive watchers that invoke AI only when something meaningful changes.

## Target hardware profile

Initial optimization target:

- CPU: Intel Core i9-14900KF-class
- GPU: NVIDIA RTX 3070 8 GB-class
- RAM: 32 GB
- OS: Windows 11

The design must degrade gracefully to weaker systems and scale to stronger GPUs without architectural rewrites.

## Tech stack

### Desktop

- Tauri 2
- React
- TypeScript
- Tailwind CSS
- Rust for native window/tray/global-shortcut integration

### Core service

- Python 3.12
- FastAPI
- WebSocket for live state/events
- Pydantic for protocol/tool validation
- SQLite for local persistence

### AI

- Ollama for initial local-model lifecycle and API
- llama.cpp for advanced quantization/offload/hybrid inference
- Qwen3.5 4B as main local assistant/vision model
- Qwen3.5 9B as optional quality tier
- Qwen3.5 27B as experimental deep-reasoning CPU/GPU hybrid tier

### Audio

- openWakeWord for wake phrase detection
- Silero VAD for speech boundaries
- faster-whisper with `language="pl"`
- Piper `pl_PL` initially for TTS
- XTTS-v2 evaluated later for higher-quality Polish voice

### Windows integration

- Windows UI Automation
- pywinauto for early Python integration where useful
- pywin32 and/or Rust `windows` crate for native Windows capabilities
- `mss` for initial screenshot capture
- `psutil` for CPU/RAM/disk/network/process telemetry
- NVIDIA NVML for GPU/VRAM/temperature/power telemetry

## Core architecture rule

> AI decides **what** should happen. Deterministic code decides **how** it happens. The permission system decides **whether** it may happen.

No everyday feature should depend on free-form shell generation when a typed capability can exist instead.

---

# Phase 0 — Repository foundation

## Deliverables

- source-of-truth docs under `docs/`;
- short `AGENTS.md` repository map;
- development status site under `site/`;
- GitHub Pages workflow;
- `.gitignore` covering models, recordings, databases, build output, secrets;
- initial application/service package structure when implementation begins.

## Exit criteria

Codex can enter the repository, understand product constraints, locate the relevant spec, and know the next implementation milestone without relying on chat history.

---

# Phase 1 — Desktop shell and overlay

## Build

- Tauri app bootstrapping;
- React shell;
- frameless always-on-top overlay window;
- compact and expanded overlay modes;
- system tray icon/menu;
- global fallback shortcut;
- hide/show/resize/drag behavior;
- local settings storage;
- connection health indicator for the core service.

## Required overlay states

- idle/hidden;
- listening;
- transcribing;
- thinking;
- speaking;
- follow-up listening;
- confirmation required;
- error/offline.

## Exit criteria

Overlay launches reliably, can be summoned/dismissed, and reflects simulated assistant state transitions.

---

# Phase 2 — Core service and protocol

## Build

- FastAPI service;
- WebSocket event channel;
- Pydantic message/event schemas;
- desktop-to-core health/handshake protocol;
- central assistant state machine;
- structured logging with privacy-safe defaults;
- provider interfaces for STT, TTS, wake word, LLM and screen capture.

## Exit criteria

Desktop and core can exchange typed events and state updates without AI dependencies.

---

# Phase 3 — Polish audio pipeline

## Build

1. microphone selection and calibration;
2. openWakeWord integration;
3. Silero VAD;
4. faster-whisper with Polish forced;
5. Piper Polish TTS;
6. conversation/follow-up timeout;
7. temporary rolling audio buffer in RAM only;
8. wake-word-only low-cost idle mode.

## State flow

`SLEEPING -> WAKE_DETECTED -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> FOLLOW_UP -> SLEEPING`

## Initial barge-in policy

For V1, suppress normal wake processing while TTS is playing except for an explicit stop path if reliable. Full acoustic echo cancellation is V1.5.

## Exit criteria

The user can wake the assistant, speak a Polish command, receive a Polish spoken response, and continue with a short follow-up conversation without pressing a hotkey.

---

# Phase 4 — Custom assistant-name onboarding

## Build

- name entry screen;
- wake-name suitability score;
- microphone calibration;
- guided positive recordings;
- guided pronunciation/position/loudness prompts;
- natural command recordings;
- generated hard-negative/confusable Polish phrases;
- normal-speech negative sample;
- wake model training pipeline;
- validation loop for recall/false activations;
- sensitivity control;
- rename/retrain support.

## Important UX rule

Never ask the user to "record N samples" without guidance. Every recording step must specify:

- exact phrase to say;
- how loudly;
- distance from microphone;
- intended tone/intonation;
- whether it is a positive or negative training example.

See `WAKE_WORD_TRAINING.md`.

## Exit criteria

A first-time user can choose an arbitrary suitable Polish-friendly name, complete guided training, validate it, and use it as the activation phrase.

---

# Phase 5 — Main local AI conversation

## Build

- Ollama provider;
- Qwen3.5 4B model integration;
- Polish system prompt;
- streaming responses;
- conversation context manager;
- local-only privacy mode;
- model health/loading state;
- concise voice-response mode vs richer overlay-response mode.

## Prompt constraints

- always respond in Polish for V1;
- prefer tool calls over guessing facts that tools can retrieve;
- never claim an action succeeded until a tool returns success;
- keep spoken replies short and natural;
- allow richer text detail in the overlay.

## Exit criteria

Polish local chat is stable, streamed into the overlay, and spoken when appropriate.

---

# Phase 6 — Tool engine and permissions

## Build

Typed tool registry with permission metadata and validation.

### Initial read tools

- `get_system_stats`
- `get_running_processes`
- `get_active_window`
- `read_ui_tree`
- `inspect_screen`
- `list_directory`
- `read_file`
- `find_process_using_port`

### Initial safe-action tools

- `open_application`
- `focus_window`
- `open_folder`
- `set_volume`
- `set_application_volume`

### Confirmation-required tools

- `restart_approved_process`
- `move_file`
- `delete_file`
- `restart_pc`
- `shutdown_pc`

## Permission levels

- `read` — may run automatically;
- `write.safe` — may run automatically if user policy permits;
- `sensitive` — explicit confirmation required.

## Exit criteria

The model can select typed tools, the backend validates them, and the overlay provides a clear confirmation card where required.

---

# Phase 7 — Windows and screen context

## Build

- active process/window tracking;
- Windows UI Automation reader;
- selected/control text extraction where possible;
- unified context object;
- context minimization before prompting the model;
- typed unavailable seam for screenshot inspection, implemented in Phase 8.

## Priority order

1. structured app/OS metadata;
2. UI Automation tree;
3. selected text/known logs;
4. screenshot/vision only when needed.

## Exit criteria

Questions like `Co to za błąd?` or `Dlaczego to nie działa?` can use current-window context without requiring the user to paste text manually.

---

# Phase 8 — Vision

## Build

- active-window and optional user-selected region capture;
- screenshot normalization/resizing;
- Qwen3.5 image input;
- region-selection flow;
- explicit indicator when the assistant is visually inspecting the screen;
- exclusion rules for sensitive windows/apps.

## Exit criteria

The assistant can explain visible errors, settings pages, dialogs and selected screen areas without continuously monitoring every frame.

---

# Phase 9 — System telemetry and diagnostics

## Build

- CPU/RAM/disk/network telemetry;
- NVIDIA GPU/VRAM/temp/power telemetry;
- per-process correlation where available;
- lightweight history buffer;
- diagnostic summaries for the model.

## Exit criteria

Requests such as `Czemu gra laguje?` can be answered using real system measurements instead of generic advice.

---

# Phase 10 — Memory and routines

## Build

SQLite tables for:

- conversations;
- messages;
- preferences;
- memories;
- app aliases;
- project aliases;
- permissions;
- routines;
- watchers;
- event history.

Start with explicit structured memory. Add embeddings/vector search only when there is a proven retrieval need.

## Exit criteria

The assistant can remember user-approved aliases/preferences and execute named routines without stuffing full history into every prompt.

---

# Phase 11 — Proactive watchers

## Build

- WindowWatcher;
- ProcessWatcher;
- FileWatcher;
- ResourceWatcher;
- BuildWatcher;
- DownloadWatcher.

Watchers are deterministic event sources. They invoke the LLM only when a relevant change needs interpretation.

Example:

`Obserwuj to okno i powiedz mi, kiedy instalacja się skończy.`

## Exit criteria

The assistant can monitor an explicitly requested condition and notify the user when it completes/fails without running vision or the LLM continuously.

---

# Phase 12 — Quality/deep model routing

## Build

- hardware-aware router;
- optional Qwen3.5 9B quality mode;
- experimental Qwen3.5 27B llama.cpp CPU/GPU hybrid mode;
- VRAM/RAM availability checks;
- model residency/unload policy;
- manual Polish command such as `Przemyśl to dokładniej` to request a deeper tier.

## Exit criteria

Simple commands remain fast while difficult analysis can escalate without forcing a large hybrid model into every interaction.

---

# V1 completion definition

V1 is complete when this interaction is reliable:

1. user says their trained assistant name;
2. overlay enters listening state;
3. user asks in Polish why GPU usage is high;
4. assistant transcribes the request;
5. retrieves GPU/process telemetry;
6. identifies likely source of load;
7. answers concisely in Polish by voice;
8. shows richer detail in overlay;
9. user can say `Pokaż` as a follow-up without repeating the wake word.

The same release must also support screen-context questions and safe typed tool actions.

# Explicitly postponed beyond initial V1

- unrestricted autonomous mouse/keyboard control;
- arbitrary PowerShell execution;
- automatic deletion of files;
- email/message sending;
- voice biometrics as security authentication;
- always-on continuous-screen vision;
- multi-agent orchestration;
- cloud AI as a requirement;
- plugin marketplace;
- smart-home integration;
- deep game-process/anti-cheat integrations.
