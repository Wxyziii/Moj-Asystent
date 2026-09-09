---
name: milestone-executor
description: Execute one Moj-Asystent implementation milestone from docs/IMPLEMENTATION_PLAN.md from initial repo inspection through validation and documentation closeout. Use when asked to begin, implement, finish, or continue a numbered project milestone/phase.
---

# Milestone Executor

Treat `AGENTS.md` and `docs/` as the source of truth. This skill defines execution procedure, not product scope.

1. Read `AGENTS.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, and only the feature docs relevant to the requested milestone.
2. Inspect the existing implementation before changing files. Preserve working code and established interfaces unless the milestone requires change.
3. Extract the milestone's build items and exit criteria into a short internal checklist. Do not implement later milestones opportunistically.
4. Identify the narrowest relevant project skill under `.agents/skills/` and follow it for subsystem-specific work.
5. Implement in small coherent units. Keep desktop, core service, protocol, site, and docs responsibilities in their documented boundaries.
6. Prefer mocks/stubs at future integration boundaries over prematurely implementing later subsystems.
7. Run the checks that actually apply: formatting, linting, type checks, tests, production build, and platform/native checks where available. Never claim a check ran if it did not.
8. Compare the result against the milestone exit criteria. Fix milestone-scoped failures before stopping.
9. Update `CHANGELOG.md`, `docs/ROADMAP.md`, and `site/data/project.json` when status changed. Update `docs/DECISIONS.md` only for a material new/change in architecture or product behavior.
10. Finish with: implemented scope, important files, verification results, unresolved issues, assumptions, and the next milestone. Stop at the requested milestone.

Do not commit secrets, model weights, microphone recordings, generated wake-word datasets, screenshots, or local databases.