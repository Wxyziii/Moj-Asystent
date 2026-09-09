---
name: windows-context-diagnostics
description: Implement or refine Moj-Asystent Windows context, UI Automation, screen inspection, vision handoff, and system telemetry/diagnostics. Use for Phases 7-9 involving active windows, UI trees, screenshots, GPU/CPU/process data, or contextual troubleshooting.
---

# Windows Context and Diagnostics

Read the relevant Phase 7-9 sections of `docs/IMPLEMENTATION_PLAN.md`, plus `docs/ARCHITECTURE.md` and `docs/SECURITY.md`.

## Context priority

Use the least expensive and least invasive source that answers the question:

1. active process/window metadata;
2. Windows UI Automation / structured control text;
3. selected text, known logs, and tool results;
4. screenshot/vision only when structured context is insufficient.

Do not continuously stream frames to the model.

## Implementation rules

- Build a unified context object with explicit provenance and timestamps.
- Keep UIA, screenshot capture, telemetry, and vision behind separate interfaces so one can fail without collapsing the others.
- Minimize context before sending it to the model. Do not attach unrelated process lists, UI trees, screenshots, or telemetry by default.
- Respect sensitive-window/app exclusions and make visual inspection visible to the user.
- Normalize/resize screenshots before model input; capture the active window/selected region rather than the entire desktop when possible.
- For diagnostics, prefer measured data from `psutil`, NVML, Windows APIs, and process correlation over generic model inference.
- Treat telemetry sampling as lightweight background work. Keep bounded history and invoke the LLM only when interpretation is requested or an explicit watcher condition fires.
- Competitive-game support must remain read-only and must not inject into protected processes or access anti-cheat-protected memory.

## Verification

Test unavailable/inaccessible UIA nodes, window changes during capture, redaction/exclusion rules, stale-context handling, screenshot fallback decisions, missing NVML/GPU support, telemetry bounds, and context minimization. Distinguish deterministic integration tests from visual/LLM evaluations.