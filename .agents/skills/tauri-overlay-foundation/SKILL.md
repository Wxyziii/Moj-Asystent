---
name: tauri-overlay-foundation
description: Build or modify the Moj-Asystent Tauri 2 + React/TypeScript desktop shell, assistant overlay, tray integration, global shortcut, simulated assistant states, and settings UI. Use for Phase 1 desktop/overlay work under apps/desktop.
---

# Tauri Overlay Foundation

Read `docs/OVERLAY_UI.md` and the Phase 1 section of `docs/IMPLEMENTATION_PLAN.md` before editing.

Work only under the desktop boundary unless a shared protocol type is genuinely required.

## Implementation rules

- Use Tauri 2 for native window, tray, shortcut, and desktop lifecycle behavior.
- Use React + TypeScript for UI. Keep native concerns out of React components.
- Model overlay behavior with explicit typed states rather than scattered booleans.
- Support compact and expanded/chat modes and simulated states: idle/hidden, listening, transcribing, thinking, speaking, follow-up, confirmation-required, error/offline.
- Keep all copy Polish for V1, but do not hardcode the user's eventual assistant name into product text or identifiers.
- Keep visual state transitions deterministic and testable; animations must not control business state.
- Use mocks for core-service events in Phase 1. Define clean event/service boundaries for later replacement.
- Avoid implementing microphone, AI, telemetry, screen capture, tool execution, or backend logic during this phase.
- Make always-on-top, show/hide, resize, drag, tray, and fallback shortcut behavior resilient to repeated invocation.

## Verification

Run the frontend formatter/linter/type checks and production build. Run Tauri/native checks available in the environment. Exercise the simulated state flow and compact/expanded transitions. Report any Windows behavior that could not be validated in the current environment.