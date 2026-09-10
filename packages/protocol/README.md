# Shared Protocol

This package defines the stable messages exchanged between the Tauri desktop app and the Python core service.

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

## Protocol v1

`schema/protocol-v1.json` is the versioned wire-contract source. The
TypeScript runtime parser and the core's Pydantic models both accept only the
current v1 events: `client.hello`, `system.health`,
`assistant.state.changed` and `system.error`.

Every envelope includes `protocol_version`, `event_id`, `occurred_at` and a
nullable `correlation_id`. Unknown fields, event types and unsupported versions
are rejected by the shared JSON Schema, Python models and TypeScript validator.

Versions are exact capabilities. A `1.0` client rejects `1.1`; it accepts a new
minor version only after that version is explicitly implemented and validated.
During a migration, the core may advertise support for multiple exact versions.
Breaking changes require a new major version.
