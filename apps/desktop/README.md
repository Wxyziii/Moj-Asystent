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

## First implementation task

Bootstrap Tauri + React and implement simulated overlay states from `docs/OVERLAY_UI.md` before integrating real audio/AI.
