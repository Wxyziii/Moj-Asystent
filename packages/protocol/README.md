# Shared Protocol

This package will define the stable messages exchanged between the Tauri desktop app and the Python core service.

## Goals

- explicit versioned schemas;
- request/action correlation IDs;
- clear assistant state events;
- no leaking provider-specific model objects into the UI;
- generated/shared TypeScript and Python representations where practical.

## Initial events

- `system.health`
- `assistant.state.changed`
- `audio.transcript.partial`
- `audio.transcript.final`
- `assistant.response.delta`
- `assistant.response.completed`
- `tool.requested`
- `tool.confirmation.required`
- `tool.completed`
- `context.screen.inspecting`
- `watcher.triggered`

## First implementation task

Choose one schema source of truth (JSON Schema, TypeSpec, or another maintainable option), generate/validate both Python and TypeScript representations, and add protocol round-trip tests.
