---
name: memory-routines-watchers
description: Implement or review Moj-Asystent structured SQLite memory, aliases/preferences/routines, event history, and deterministic proactive watchers. Use for Phase 10 memory/routines and Phase 11 watcher/notification work.
---

# Memory, Routines, and Watchers

Read Phase 10-11 of `docs/IMPLEMENTATION_PLAN.md` plus `docs/SECURITY.md` before editing.

## Memory rules

- Start with explicit structured SQLite data. Do not introduce embeddings/vector search until a concrete retrieval problem justifies it.
- Separate conversations/messages from durable user-approved preferences, aliases, routines, permissions, and memories.
- Store provenance/timestamps where useful so stale or conflicting memory can be reasoned about.
- Make durable memory inspectable/editable and avoid persisting sensitive transient context unnecessarily.
- Do not persist screenshots or raw microphone audio as memory by default.
- Keep schema migrations explicit and tested.

## Routine rules

- A routine is a named, deterministic workflow composed of typed tools/capabilities.
- Reuse the normal permission model; a routine must not bypass confirmation requirements.
- Validate referenced apps/paths/tools at execution time and report partial failure explicitly.

## Watcher rules

- Watchers are deterministic event sources, not permanently-running LLM prompts.
- Implement narrow watchers such as window, process, file, resource, build, and download conditions.
- Keep sampling/event loops lightweight and bounded. Persist only the state required to resume/compare safely.
- Invoke AI only when an explicit watched condition changes and interpretation is actually useful.
- Prevent notification loops and duplicate alerts with event identity/debouncing/cooldown where appropriate.
- Make watchers visible and cancellable by the user.

## Verification

Test migrations, CRUD boundaries, memory conflict/staleness behavior, routine permission propagation, watcher lifecycle/restart, duplicate suppression, condition transitions, resource bounds, and cancellation. Use fake clocks/event sources for deterministic watcher tests where practical.