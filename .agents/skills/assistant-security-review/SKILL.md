---
name: assistant-security-review
description: Defensive security review for Moj-Asystent tool execution, permissions, filesystem or Windows actions, local APIs, provider boundaries, and privacy-sensitive functionality. Do not use for ordinary CSS or visual UI work.
---

# Assistant Security Review

Review changes against the product boundary: **AI decides what; deterministic
code decides how; permission policy decides whether.** Treat model output,
provider payloads, UI events and local API messages as untrusted until validated.
This skill is for implementation or review of tool engines, permissions,
filesystem/system actions, local APIs and privacy-sensitive capabilities.

## Review workflow

1. **Map the boundary.** Identify inputs, typed data models, trust transitions,
   executors, persistence and externally observable effects. State what is not
   in scope rather than assuming a subsystem exists.
2. **Check secure defaults.** Invalid, absent, empty, zero and malformed values
   must reject safely. No configuration, provider failure or timeout may
   silently grant an action, expand a path, expose a listener or retain data.
3. **Review enforcement, not intent.** Confirm authorization occurs after model
   selection and before execution, and that the executor independently receives
   validated typed arguments plus a permission decision.
4. **Probe misuse resistance.** Prefer narrow types, allowlists and explicit
   operations over strings, booleans or escape hatches that make unsafe use
   convenient. Test the likely rushed or confused caller, not only the happy
   path.
5. **Validate findings.** For each reportable issue, trace input to effect,
   establish realistic impact, identify the missing guard and propose the
   smallest durable remediation. Do not treat documentation as a mitigation.

## Required review areas

### Tool execution and Windows actions

- Tools are typed and allowlisted; no normal path exposes arbitrary shell,
  PowerShell, command-line, executable or process targeting.
- Arguments are schema-validated, normalized and bounded before execution.
- Command, path, process and executable injection cannot cross the adapter.
- Failure, timeout, cancellation and unknown result states fail closed and are
  never presented as success.
- Elevation, services, restart/shutdown, protected paths and destructive actions
  require explicit capability-specific controls.

### Filesystem

- Normalize paths before policy checks; reject traversal, ambiguous roots and
  unsafe reparse-point or symlink resolution where relevant.
- Evaluate allowlists on the final resolved target, not the user spelling.
- Move, write and deletion operations have explicit confirmation and safe
  recovery semantics; persistent approval is never a blanket destructive grant.

### Permission policy

- `read`, `write.safe` and `sensitive` are closed enums with explicit defaults.
- A user confirmation is bound to the exact tool, normalized arguments and
  correlation ID; stale, replayed or substituted approvals cannot authorize a
  different action.
- Persistent approval is limited to explicitly safe categories and cannot
  become an escalation route.
- Missing policy, unknown tool, malformed payload and unavailable checker deny.

### Local core and protocol

- Bind local services to localhost by default; exposing a network listener is
  explicit, authenticated and justified.
- Validate WebSocket origins, connection state, message version and payloads.
- Correlation IDs never substitute for authorization. Provider input and local
  UI events receive the same schema and size validation as network input.

### Privacy

- Recordings, wake-training data, screenshots, UI Automation output,
  conversations, memory, telemetry and logs are local-first, minimized and not
  retained by default.
- Sensitive windows/apps are excluded before collection where feasible; redact
  secrets from diagnostic output and never log raw microphone audio by default.
- Collection, inspection and retention are visible to the user and independently
  configurable when the feature is introduced.

## Security test prompts

Use focused negative tests where an enforcement seam exists:

- unknown tool or invalid enum is denied;
- path traversal, normalized equivalent and reparse-point escape are denied;
- confirmation for one action cannot authorize another;
- executor failure cannot produce a success result;
- malformed or oversized local protocol message is rejected;
- unavailable policy/provider fails closed;
- zero, negative, null and empty security configuration values have safe,
  documented semantics.

## Reporting

Report severity, reachable input, affected guard, concrete impact, proof or
test, and remediation. State uncertainty plainly. Do not run destructive proof
of concepts against user data or systems.

## Design references

This workflow synthesizes defensive concepts from Trail of Bits' `semgrep`,
`codeql`, `insecure-defaults` and `sharp-edges` skills: evidence-led analysis,
fail-closed defaults and pit-of-success interfaces. It is deliberately scoped
to Moj-Asystent rather than a concatenation of those upstream skills.
