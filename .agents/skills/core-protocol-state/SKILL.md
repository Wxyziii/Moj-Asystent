---
name: core-protocol-state
description: Implement the Moj-Asystent Python/FastAPI core service, typed desktop-core protocol, WebSocket events, handshake/health flow, assistant state machine, privacy-safe logging, and provider interfaces. Use for Phase 2 core-service and protocol work.
---

# Core Protocol and State

Read `docs/ARCHITECTURE.md` and Phase 2 of `docs/IMPLEMENTATION_PLAN.md` first.

## Boundaries

- `services/core/`: Python 3.12, FastAPI, Pydantic, assistant orchestration primitives.
- `packages/protocol/`: shared message/event contracts only.
- `apps/desktop/`: protocol consumer; do not move core logic into it.

## Procedure

1. Define protocol messages/events before implementing transport behavior.
2. Version protocol envelopes when compatibility matters.
3. Validate all inbound payloads with typed schemas; reject malformed/unknown dangerous data explicitly.
4. Implement a central explicit assistant state machine. Invalid state transitions must fail predictably rather than being silently accepted.
5. Add WebSocket connection, handshake, health, reconnect-friendly semantics, and state synchronization without AI dependencies.
6. Define replaceable interfaces for wake word, STT, TTS, LLM, screen capture, and later tools; Phase 2 implementations may be mocks/null providers.
7. Keep logs structured and privacy-safe. Do not log raw audio, screenshots, secrets, or full sensitive payloads by default.
8. Make shutdown/restart behavior clean so native desktop development does not leave orphan services.

## Verification

Add tests for schema validation, handshake behavior, reconnect/state sync where practical, valid and invalid state transitions, and provider-interface substitution. Run Python formatting/linting/type checks/tests and any protocol/TypeScript checks that apply.