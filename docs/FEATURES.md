# Feature Catalogue

This document defines the product surface. Implementation status belongs in `ROADMAP.md` and `site/data/project.json`.

## 1. Overlay and desktop experience

- Global summon shortcut as fallback even when voice is enabled.
- Frameless always-on-top overlay.
- Compact listening/thinking bubble.
- Expanded chat window.
- Draggable and resizable.
- Auto-hide when inactive.
- Pin/unpin behavior.
- Conversation history.
- Streaming model responses.
- Markdown/code/table rendering.
- Suggested action buttons.
- Confirmation cards for sensitive actions.
- Screenshot/region preview when vision is used.
- Drag-and-drop file attachments later.
- Clipboard-aware ask action later.
- System tray menu.
- Settings/dashboard view.
- Clear state indicator: idle, listening, transcribing, thinking, speaking, follow-up, error.

## 2. Voice activation

- User chooses assistant name during onboarding.
- No hardcoded `Jarvis` requirement.
- User trains the wake phrase with guided recordings.
- Wake detector runs while enabled without requiring a hotkey.
- Full STT starts only after wake.
- Follow-up conversation window so the wake name is not required before every sentence.
- Configurable timeout.
- Adjustable wake sensitivity.
- Microphone selection.
- Microphone calibration.
- Local-only wake processing.
- No raw recording persistence by default.
- Rename/retrain support.

## 3. Polish-only language support for V1

- Force STT language to Polish (`pl`).
- Polish system prompts.
- Polish overlay copy.
- Polish TTS voice.
- Polish technical vocabulary hints/corrections.
- Polish hard-negative generation for wake names.
- Spoken answers concise; visual answers may be more detailed.
- No language auto-detection in V1.

## 4. Screen awareness

- Detect active application.
- Detect active window title.
- Read Windows UI Automation tree.
- Read visible/accessibility text where exposed.
- Capture active-window screenshot only when needed.
- Full-screen capture when explicitly justified.
- User-selectable screen region.
- Explain visible errors/dialogs.
- `Co to jest?`
- `Co powinienem tutaj zrobić?`
- `Dlaczego to nie działa?`
- Compare visible choices.
- Detect meaningful UI change through deterministic watchers.
- Explicit screen-inspection indicator.
- Per-app exclusion list.

## 5. Proactive assistance

- Detect failed builds when watching a process/window.
- Detect crashes.
- Detect new error dialogs.
- Detect unusually high system-resource usage.
- Detect low disk space.
- Detect a watched operation completing/failing.
- Suggest troubleshooting only when relevant.
- Configurable proactive level: Off / Important / Normal / Proactive.
- Per-app proactive settings.
- `Obserwuj to okno.`
- `Powiedz mi, kiedy ten build się skończy.`
- `Daj znać, jeśli instalacja się wywali.`

## 6. Windows control

Planned typed capabilities:

- launch application;
- close/focus/minimize/maximize approved windows;
- open folder;
- set master volume;
- set per-application volume;
- mute microphone;
- change audio device later;
- inspect processes;
- restart approved process;
- inspect services;
- restart approved service;
- open Windows settings pages;
- inspect files/folders;
- copy/move/rename with permission;
- delete only with explicit confirmation;
- lock/sleep/restart/shutdown with appropriate confirmation.

Avoid free-form shell execution in normal mode.

## 7. System telemetry

- CPU utilization.
- CPU temperature where supported.
- GPU utilization.
- GPU temperature.
- GPU power.
- VRAM usage.
- RAM usage.
- disk capacity/activity.
- network throughput.
- latency/ping tools when requested.
- running processes.
- per-process resource correlation where feasible.
- recent lightweight telemetry history.
- bottleneck explanations based on real metrics.

## 8. Coding/development assistance

Longer-term integrations should allow:

- understand current project context;
- inspect terminal/build output;
- explain stack traces;
- search source files;
- understand project structure;
- propose patches;
- show diffs before applying changes;
- run tests/builds through approved project commands;
- inspect Git status;
- identify port conflicts;
- start/stop known development services;
- project-specific aliases/routines.

Destructive writes remain permission-controlled.

## 9. Browser awareness

Later phase:

- browser-extension bridge for active tab metadata;
- selected text/page context;
- page summarization/explanation;
- compare tabs/pages;
- explicit browser automation where safe and approved;
- do not depend on blind coordinate clicking when a structured integration exists.

## 10. Clipboard intelligence

Later phase:

- optional clipboard awareness;
- explain copied error/log/text;
- rewrite/translate/format copied text;
- detect URLs;
- `Zapytaj asystenta o schowek` action.

## 11. Files and documents

- list/read approved directories;
- semantic search later;
- summarize text/documents;
- compare files;
- inspect logs;
- organize suggested file moves with confirmation;
- duplicate detection later;
- no silent destructive file operations.

## 12. Memory

- short-term conversation state;
- user-approved persistent preferences;
- app aliases;
- project aliases;
- routines;
- tool permission preferences;
- watcher definitions;
- editable/deleteable stored memory through settings;
- no screenshot/audio storage by default.

Start structured; add embeddings only if needed.

## 13. Routines

Examples:

- `Tryb pracy`
- `Tryb grania`
- `Tryb programowania`

A routine may:

- launch approved apps;
- open folders/projects;
- set volume;
- change known system settings;
- arrange windows later;
- start approved services/processes.

## 14. AI model router

- Fast/default local tier.
- Optional quality tier.
- Optional deep CPU/GPU hybrid tier.
- Local-only privacy mode.
- Hardware/VRAM-aware loading.
- Unload large models when not needed.
- Manual `Przemyśl to dokładniej` escalation.
- Future coding-specialized provider possible.

## 15. Multi-step tasks

- break request into explicit steps;
- use several typed tools;
- validate each result;
- stop on unsafe/ambiguous state;
- show progress in overlay;
- allow cancel/stop;
- require confirmations at sensitive boundaries.

Example:

`Sprawdź, co używa portu 3000, zatrzymaj ten proces po mojej zgodzie, uruchom serwer i sprawdź, czy odpowiada.`

## 16. Notifications

- overlay bubble;
- Windows toast later;
- optional voice notification;
- priority levels;
- quiet mode;
- per-app rules;
- watcher completion/failure alerts.

## 17. Gaming-aware behavior

- detect game process;
- reduce proactive overlay interruptions;
- lightweight telemetry only;
- optional gaming profile;
- crash/log analysis after game exits;
- no code injection;
- no process-memory reading for anti-cheat-protected competitive titles;
- configurable automatic overlay disable for protected games.

## 18. Security/privacy controls

- read-only by default where possible;
- per-tool permissions;
- app/path allowlists;
- confirmation for sensitive actions;
- action audit log;
- emergency stop/mute;
- private session mode;
- excluded applications/windows;
- no unrestricted normal-mode shell;
- no hidden continuous screenshot recording.

## 19. Plugin/integration architecture

Long-term:

- internal plugin interface for tools/context providers;
- Python integrations;
- Rust/native integrations where appropriate;
- local HTTP integrations;
- MCP compatibility can be evaluated later;
- enable/disable plugins individually;
- permissions remain enforced centrally regardless of plugin source.

## 20. Development-status website

- project status;
- latest milestone;
- roadmap timeline;
- architecture overview;
- feature progress;
- changelog;
- links to source docs;
- static and privacy-safe;
- generated/presented from versioned repository data.
