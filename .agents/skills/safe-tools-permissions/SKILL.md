---
name: safe-tools-permissions
description: Implement or audit Moj-Asystent's typed tool registry, argument validation, permission levels, confirmation flow, action results, and safe Windows/app capabilities. Use for Phase 6 tool execution, permissions, or any feature that can change system state.
---

# Safe Tools and Permissions

Read `docs/SECURITY.md`, `docs/ARCHITECTURE.md`, and Phase 6 of `docs/IMPLEMENTATION_PLAN.md` before editing.

The invariant is: AI proposes **what**; deterministic code implements **how**; policy decides **whether**.

## Tool contract

- Expose narrow typed capabilities instead of arbitrary command strings.
- Validate every argument before execution, including ranges, paths, process identities, app names, and resource ownership where relevant.
- Each tool declares permission metadata: `read`, `write.safe`, or `sensitive`.
- `read` tools may run automatically subject to privacy policy.
- `write.safe` tools follow user policy and must still return explicit success/failure.
- `sensitive` tools require an explicit confirmation flow before execution.
- The model must never manufacture a successful result. Tool execution returns structured outcomes and error details suitable for user-safe summarization.
- Keep authorization separate from execution so permission policy can be tested without invoking real system actions.
- Do not add unrestricted PowerShell/shell execution to the normal assistant. If a future developer-mode command runner exists, isolate it behind a separate explicit capability and stronger approval policy.
- Prefer recoverable operations where possible and never widen paths/targets from ambiguous input.
- Maintain an audit/event record without logging secrets or unnecessary sensitive content.

## Verification

Test schema rejection, permission classification, confirmation allow/deny/expiry, failed execution, spoofed success prevention, path/process edge cases, and policy overrides. Use fake executors for unit tests; separately label any real Windows integration tests.