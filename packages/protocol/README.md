# Shared Protocol

This package defines the stable messages exchanged between the Tauri desktop app and the Python core service.

## Goals

- explicit versioned schemas;
- request/action correlation IDs;
- clear assistant state events;
- no leaking provider-specific model objects into the UI;
- generated/shared TypeScript and Python representations where practical.

## Current implemented events (1.3)

- `system.health`
- `assistant.state.changed`
- `audio.transcript.final`
- `assistant.response.started`
- `assistant.response.delta`
- `assistant.response.completed`
- `model.status.changed`
- `tool.execution.status`
- `tool.confirmation.requested`
- `tool.confirmation.resolved`
- `tool.result`

## Protocol v1

`schema/protocol-v1.json` is the versioned wire-contract source. The
TypeScript runtime parser and the core's Pydantic models both accept only the
current v1 events: `client.hello`, `system.health`,
`assistant.state.changed`, `audio.transcript.final`,
`assistant.response.started`, `assistant.response.delta`,
`assistant.response.completed`, `model.status.changed`, the four correlated
tool lifecycle events and `system.error`.

Every envelope includes `protocol_version`, `event_id`, `occurred_at` and a
nullable `correlation_id`. Unknown fields, event types and unsupported versions
are rejected by the shared JSON Schema, Python models and TypeScript validator.

The package also exports typed parsers for the authenticated Milestone 4
onboarding payloads (name assessment, curriculum steps, sample quality,
training jobs and validation metadata). These REST payloads use the same exact
Protocol 1.3 capability requirement as the event stream and never expose local
model paths.

Versions are exact capabilities. The current client and core both require
`1.3`; a `1.2` client rejects it. A new minor version is accepted only after it
is explicitly implemented and validated. Breaking changes require a new major
version.
