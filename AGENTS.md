# AGENTS.md

This file is the repository map for Codex and other coding agents. Keep it short. Detailed requirements live under `docs/` and are the source of truth.

## Before changing code

Read, in this order:

1. `README.md`
2. `docs/IMPLEMENTATION_PLAN.md`
3. `docs/ARCHITECTURE.md`
4. The feature-specific document relevant to the task.
5. `docs/DECISIONS.md` for constraints already decided.

Do not silently contradict an existing decision. If a task requires changing one, update `docs/DECISIONS.md` in the same change.

## Repository Codex skills

Project-specific reusable skills live under `.agents/skills/`. For a milestone task, use `milestone-executor` plus the narrow subsystem skill that matches the current phase. If repo-local skills are not shown in the current Codex skill list, read the relevant `SKILL.md` directly from the paths below and follow it as project guidance.

- all numbered milestone work: `.agents/skills/milestone-executor/SKILL.md`
- Phase 1 desktop/overlay: `.agents/skills/tauri-overlay-foundation/SKILL.md`
- Phase 2 core/protocol/state: `.agents/skills/core-protocol-state/SKILL.md`
- Phase 3 Polish voice pipeline: `.agents/skills/polish-audio-pipeline/SKILL.md`
- Phase 4 custom assistant name/wake training: `.agents/skills/custom-wakeword-onboarding/SKILL.md`
- Phase 5 and Phase 12 local AI/model routing: `.agents/skills/local-ai-runtime/SKILL.md`
- Phase 6 tools/permissions/system actions: `.agents/skills/safe-tools-permissions/SKILL.md`
- Phases 7-9 Windows context/vision/diagnostics: `.agents/skills/windows-context-diagnostics/SKILL.md`
- Phases 10-11 memory/routines/watchers: `.agents/skills/memory-routines-watchers/SKILL.md`

Use only the minimum relevant skill set. Product docs and explicit user instructions take precedence over generic workflow guidance in a skill.

## Product constraints

- V1 is Windows-first and Polish-only.
- The assistant's public name is never hardcoded. The user chooses and trains it during onboarding.
- Wake-word detection is lightweight and always available when enabled; full STT/LLM processing starts after wake.
- Prefer structured OS/app context over screenshots.
- Never continuously send the screen to the LLM.
- Local-first is the default. Cloud AI must remain optional and explicit.
- The main model must remain replaceable through provider interfaces.
- Everyday tool execution must use typed, allowlisted capabilities. Do not expose arbitrary shell execution to the normal assistant.
- Sensitive or destructive actions require explicit confirmation.
- Competitive-game integrations must not inject into protected processes or interact with anti-cheat-protected memory.

## Architecture boundaries

Target top-level areas:

- `apps/desktop/` — Tauri 2 + React/TypeScript overlay and settings UI.
- `services/core/` — Python assistant service: audio, AI routing, tools, context, memory, permissions, watchers.
- `packages/protocol/` — shared schemas/events between desktop and core.
- `site/` — static development-status website.
- `docs/` — source-of-truth specifications.

Keep model/STT/TTS/wake-word implementations behind interfaces so providers can be swapped.

## Engineering expectations

- Prefer typed interfaces and explicit state machines.
- Validate all tool arguments before execution.
- Separate observation, reasoning, authorization, and execution.
- Keep background listeners lightweight; invoke expensive models only on relevant events.
- Add tests for permission checks, tool validation, audio state transitions, and routing rules.
- Fail safely: if context/tool results are uncertain, report uncertainty rather than claiming an action succeeded.
- Do not log raw microphone audio or screenshots by default.

## Documentation discipline

When a user-visible capability changes:

- update the relevant file under `docs/`;
- update `CHANGELOG.md`;
- update `site/data/project.json` if roadmap/status/changelog data changed.

The GitHub Pages site is a presentation layer, not the canonical specification.

## Implementation order

Follow `docs/IMPLEMENTATION_PLAN.md`. Do not opportunistically implement later-phase autonomous features before the core overlay, audio, model, tool, permission, and context foundations are stable.

## Completion checks

Before finishing a task:

- run the relevant formatter/linter/tests;
- confirm no secrets, model weights, recordings, generated wake-word datasets, or local databases were committed;
- update docs when architecture or behavior changed;
- summarize what changed and any remaining limitations.
