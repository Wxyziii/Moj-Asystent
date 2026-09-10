# Roadmap

## Status legend

- `planned`
- `in_progress`
- `blocked`
- `done`

## Milestone 0 — Foundation

Status: `done`

Goals:

- repository documentation;
- Codex instructions;
- architecture and product constraints;
- GitHub Pages development site;
- project status/changelog data.

Exit criteria:

- Codex can start implementation from repository context alone.

## Milestone 1 — Desktop shell

Status: `done`

Deliverables:

- Tauri 2 app;
- React overlay;
- tray integration;
- global fallback shortcut;
- compact/expanded states;
- settings shell;
- simulated assistant-state UI.

Completed scope:

- frameless, always-on-top Tauri overlay with compact, expanded and settings views;
- tray menu and `Ctrl` + `Shift` + `Spacja` fallback shortcut;
- Polish simulated states: idle, wake detection, listening, transcription, thinking, speaking, follow-up and error;
- responsive React component structure and transition validation tests.

## Milestone 2 — Core service/protocol

Status: `done`

Deliverables:

- Python FastAPI core;
- WebSocket event stream;
- shared schemas;
- assistant state machine;
- health/lifecycle handling;
- provider interfaces.

Completed scope:

- local-only FastAPI core on `127.0.0.1` with lifecycle, health endpoint and validated WebSocket handshake;
- protocol-v1 schema, Python Pydantic models and TypeScript runtime parser;
- deterministic assistant state machine, privacy-safe lifecycle logs and fail-closed mock LLM/STT/TTS/wake-word providers;
- desktop health/WebSocket client with a visible graceful disconnected state.

## Milestone 3 — Polish voice pipeline

Status: `done`

Deliverables:

- microphone selection/calibration;
- openWakeWord;
- Silero VAD;
- faster-whisper forced Polish;
- Piper Polish TTS;
- follow-up conversation mode;
- privacy-safe rolling audio buffer.

Completed scope:

- configurable local microphone ingress and non-persistent level calibration metrics;
- temporary openWakeWord development phrase behind the replaceable wake provider (custom naming remains Milestone 4);
- Silero VAD with speech/trailing-silence thresholds and a maximum utterance duration;
- faster-whisper Small/replaceable model forced to Polish and structured segment output;
- configurable Piper `pl_PL` voice playback with cancellation and self-trigger suppression;
- deterministic no-LLM response, follow-up listening window and full state/event synchronization;
- per-launch authenticated Tauri/core boundary for audio-derived content.

## Milestone 4 — Custom wake-name onboarding

Status: `planned`

Deliverables:

- assistant name entry;
- suitability score;
- guided recordings;
- hard negatives;
- local model training;
- validation;
- rename/retrain.

## Milestone 5 — Local AI chat

Status: `planned`

Deliverables:

- Ollama integration;
- Qwen3.5 4B;
- Polish prompt;
- streaming responses;
- local-only mode;
- separate voice/display response formatting.

## Milestone 6 — Tool/permission engine

Status: `planned`

Deliverables:

- typed tool registry;
- validation;
- read/write.safe/sensitive permissions;
- confirmation UI;
- action results/audit events.

## Milestone 7 — Screen/Windows context

Status: `planned`

Deliverables:

- active-window metadata;
- UI Automation;
- screenshot provider;
- region capture;
- context minimization;
- sensitive-app exclusions.

## Milestone 8 — Vision and diagnostics

Status: `planned`

Deliverables:

- multimodal Qwen screen input;
- visible error explanation;
- psutil telemetry;
- NVML GPU telemetry;
- bottleneck analysis.

## Milestone 9 — Memory/routines

Status: `planned`

Deliverables:

- SQLite schema;
- structured memories;
- aliases;
- routines;
- permission persistence;
- history controls.

## Milestone 10 — Watchers/proactive assistance

Status: `planned`

Deliverables:

- window/process/file/resource/build/download watchers;
- explicit watch commands;
- event-driven AI escalation;
- non-disruptive notifications.

## Milestone 11 — Quality/deep routing

Status: `planned`

Deliverables:

- hardware-aware model router;
- optional Qwen3.5 9B tier;
- llama.cpp hybrid larger-model experimentation;
- VRAM/RAM/load-aware residency policy.

## V1 release gate

Do not call the project V1 until all of these work reliably:

- custom wake name;
- hands-free Polish activation;
- follow-up conversation;
- overlay chat;
- local AI response;
- screen-context question;
- real system telemetry;
- safe typed Windows actions;
- confirmation for sensitive actions;
- privacy toggles/exclusions;
- stable fallback text/hotkey mode.

## V1.5 candidates

- acoustic echo cancellation / proper barge-in;
- higher-quality Polish TTS;
- richer watcher set;
- advanced region-selection UX;
- better app-specific context providers;
- packaging/update pipeline.

## V2 candidates

- browser extension integration;
- advanced coding/project integrations;
- semantic memory/embeddings;
- optional cloud provider;
- plugin/MCP compatibility;
- more languages;
- smart-home integrations;
- broader automation workflows.
