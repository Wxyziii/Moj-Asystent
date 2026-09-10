# Desktop App

Target: **Tauri 2 + React + TypeScript**.

This directory will own presentation and native desktop-shell behavior only.

## Responsibilities

- overlay windows;
- compact/expanded assistant UI;
- system tray;
- fallback global shortcut;
- onboarding screens;
- settings;
- permission/confirmation cards;
- region-selection UI;
- connection to the local core service;
- rendering state/events returned by the core.

## Non-responsibilities

Do not put these here unless they are thin adapters:

- LLM orchestration;
- wake-word logic;
- STT/TTS engines;
- persistent memory logic;
- Windows tool authorization;
- business rules for model routing.

Those belong in `services/core/`.

## Implemented foundation

Implemented: frameless always-on-top overlay, compact/expanded/settings modes,
system tray controls, the `Ctrl` + `Shift` + `Spacja` fallback shortcut and Polish
simulated assistant states. The desktop now owns the development core process,
creates its memory-only session credential, reconnects with bounded backoff and
renders authenticated core state, final Polish transcripts and deterministic
Milestone 3 responses. Simulation remains clearly labeled for UI development.

Milestone 4 adds the first-run and settings-accessible Polish wake-name wizard.
It records temporary browser PCM only while the core capture is paused, then
hands validated samples to the authenticated core for local training and model
activation. The wizard exposes quality guidance, progress, validation and
sensitivity without moving wake-word or training logic into React.

Run locally with `npm run dev` from the repository root. Validate the desktop
shell with `npm --workspace @moj-asystent/desktop run tauri -- dev`.
