# Security and Permissions

## Core principle

The assistant may reason freely, but execution is constrained by deterministic code and explicit authorization.

```text
User request
 -> intent/model
 -> typed tool proposal
 -> argument validation
 -> permission evaluation
 -> optional confirmation
 -> deterministic execution
 -> verified result
 -> user response
```

The model never bypasses this chain.

## Permission classes

### `read`

Examples:

- get system stats;
- inspect active window;
- list processes;
- read approved UI tree;
- read an approved file/path;
- inspect screenshot when screen awareness is enabled.

May run automatically subject to privacy/app/path policy.

### `write.safe`

Examples:

- set volume;
- open application;
- focus a window;
- open a folder.

May run automatically if the user has allowed the tool/category.

### `sensitive`

Examples:

- terminate/restart process;
- move/delete files;
- change security-sensitive settings;
- restart/shutdown PC;
- send data externally in a future cloud/integration feature.

Requires explicit confirmation unless a narrowly scoped persistent permission is intentionally designed and safe.

In the current implementation, all `sensitive` tools require a new confirmation every time and never permit persistent approval. `write.safe` preferences default to ask and may be set to allow/ask/deny in validated user-local storage.

## Tool design

Every tool must declare:

- name;
- typed input schema;
- output schema;
- permission class;
- timeout;
- audit metadata;
- optional path/app/service allowlists;
- whether persistent approval is permitted.

Never expose an unrestricted general-purpose command runner to the default assistant.

The central registry is the only model-facing execution surface. Unknown tools and extra arguments fail closed. Ollama uses structured function calls; natural-language command markers are not parsed. Conversation orchestration is capped at four tool iterations, and only a validated backend result may establish success.

## Shell access

Normal mode: **no arbitrary PowerShell/cmd execution**.

If developer mode later introduces shell execution:

- disabled by default;
- visible and clearly labeled;
- preview exact command;
- confirmation required;
- timeout/output size limits;
- working-directory restrictions;
- audit log;
- no privilege escalation without a separate explicit flow.

## Filesystem

- default to user-selected/approved roots;
- canonicalize paths before policy evaluation;
- defend against `..`, symlink/junction traversal and case/path normalization issues;
- distinguish read/write/delete permissions;
- destructive operations display exact target(s);
- avoid recursive delete in early versions.

Milestone 6 limits filesystem tools to canonical paths under configured user roots, rejects symlinks/reparse points, reads at most 256 KiB of UTF-8 non-binary content through a bounded read, and inspects at most `limit + 1` directory entries. Destructive confirmations bind canonical preflighted paths, execution revalidates them, moves use an atomic no-replace operation, and deletion is never recursive. These controls reduce traversal risk but do not provide a kernel-level handle-based defense against every same-user TOCTOU race or same-path file replacement.

The initial default root is the current user's home directory so the feature is usable before a path-selection settings flow exists. This remains broader than the intended mature allowlist and should be narrowed through explicit user-selected roots before adding external/cloud providers. Cancellation is guaranteed while waiting for approval and before dispatch; a blocking native call already past its point of effect may not be interruptible by Python's thread timeout.

## Confirmation boundary

- Tauri holds a separate per-launch action credential that is not exposed to React;
- the webview can invoke only a typed confirmation-resolution command, not a general privileged proxy;
- confirmation IDs use cryptographic randomness, expire after 90 seconds and are single-use;
- operation ID, call ID, tool name and canonical argument digest must all match;
- cancellation/replacement invalidates pending confirmations;
- losing the last authenticated WebSocket session invalidates pending confirmations, and no new ticket can be created without an authenticated event subscriber;
- sensitive tools cannot be permanently approved by local policy.

Loopback plus per-launch credentials protects against ordinary browser-origin requests, but does not claim isolation from malware running as the same Windows user.

## Screen privacy

- screen awareness can be disabled globally;
- user can exclude applications/windows;
- capture active window/region rather than all monitors where possible;
- do not save screenshots by default;
- detect/redact password fields where Windows accessibility metadata makes this possible;
- never claim perfect secret redaction;
- future cloud providers must never receive screenshots silently.

Milestone 7 collects structured Windows context only when a typed tool is
requested. UI Automation traversal is bounded by node count, depth, text length,
aggregate payload, queue capacity and time. Password controls never expose their
value, text or selection through the context model. Built-in password-manager
process exclusions and configurable exact-process/title-fragment exclusions are
evaluated before UI traversal; excluded window titles and executable paths are
not returned. Window identity is checked after traversal so content collected
from a window that lost focus is discarded.

Context is process-local and ephemeral. Normal logs contain only operational
metadata such as process name, node count, truncation, elapsed time and error
class—not window titles, selected text or UI content. Accessibility metadata is
not a perfect secret classifier, and same-user malware remains outside this
boundary; users should exclude any additional sensitive applications. Screenshot
capture and vision are demand-driven in Milestone 8. Screenshot capture checks
the exclusion policy before pixel allocation, rechecks exact foreground identity
after capture, never writes ordinary captures to disk, and clears bounded buffers
after use. There is no reliable pixel-level password redaction: exclusions and
explicit user control are the primary screenshot protections. Full-resolution
images are not exposed to the webview; only a bounded reduced preview is.

Image text is untrusted data, including text that resembles instructions. Vision
cannot bypass the typed tool registry, permission policy or confirmations.

## System telemetry privacy

Telemetry is collected only on an authorized `get_system_stats` request. The
core uses bounded psutil/NVML adapters, never returns command lines or
executable paths, sanitizes process names and reports unavailable metrics
explicitly. Two samples are required for a rate; resets are not converted into
negative activity. The twelve-entry, sixty-second history is memory-only and
cleared on shutdown. Process and GPU names are untrusted observations and
cannot grant permission or trigger a tool.

## Microphone privacy

- wake-word processing local;
- short rolling audio buffer in RAM;
- no continuous disk recording;
- obvious listening state;
- tray-level instant microphone disable;
- training recordings remain local and gitignored;
- optional deletion of training samples after model creation.

## Persistent memory

- only store structured user-approved data by default;
- settings UI must allow review/removal/reset;
- sensitive raw context such as screenshots/audio should not become memory automatically;
- separate session context from durable memory.
- history retention is off by default and stores only completed user/assistant turns;
- credential-shaped keys/values are rejected before persistence;
- SQLite schema versions, foreign keys, parameterized queries and bounded values fail closed on corruption or newer versions;
- retrieved records are bounded and marked as untrusted data in the model prompt;
- aliases resolve data only and cannot bypass canonical path/application policy;
- routines reuse every normal tool validation, permission, confirmation and cancellation check.

## Network

V1 local-first.

- no cloud AI required;
- all network calls should have explicit purpose;
- model downloads are user-visible setup actions;
- future integrations use scoped credentials and OS credential storage;
- secrets never committed to repository or SQLite in plaintext when avoidable.

The desktop/core voice boundary additionally requires a per-launch random credential. Tauri passes it directly to the owned core process, and the core validates it before health responses, audio-control requests or WebSocket acceptance. Credentials are memory-only, omitted from logs and invalid after application restart. HTTP and WebSocket remain restricted to loopback hosts and known desktop origins, with strict payload-size and schema validation. This limits accidental cross-origin/local access; it does not defend against a malicious process with access to the same user's process memory.

Voice v2 settings and diagnostics use that same authenticated loopback boundary.
Vocabulary entries are normalized, length/count bounded and treated only as
decoder hints. Diagnostic storage is capped in memory and excludes PCM and
transcript content. Model status exposes a public model basename rather than a
private filesystem path. Preferred and fallback STT models are local-only and
are never downloaded by normal startup or benchmark execution.

Whisper confidence signals are not authentication and are not treated as
calibrated truth. A low-confidence voice request can only make authorization
stricter: `write.safe` requires a single-use confirmation even when persistently
allowed, while `sensitive` retains its existing mandatory confirmation. It can
never make a denied or confirmation-required ToolEngine decision more permissive.

The core removes both credentials from its process environment after startup and explicitly scrubs them from tool-launched child environments. Fixed application aliases resolve to absolute Windows system executables rather than relying on the working directory or `PATH`.

Approved process restart binds PID, creation time, executable name and the resolved allowlisted image. Relaunch explicitly pins that verified executable even if the captured command line uses a different or relative first argument.

Wake-name onboarding uses the same boundary. Session, calibration, training, validation and activation endpoints require the credential and current exact Protocol 1.4. Session IDs are UUIDs, request models reject unknown fields, base64 PCM is bounded before decoding, and model activation accepts only a validated ONNX file in the service-owned model directory. Status responses redact filesystem paths. Temporary recordings and partial models are cleaned up on cancellation, failure and shutdown; the active model is replaced only through an atomic file operation.

Local chat requests use the authenticated core API. The core alone contacts
Ollama, accepts only an HTTP loopback origin, disables environment proxy use,
validates bounded provider frames and publishes only typed Protocol 1.4 events.
Prompts and response text are held in bounded process memory and are not logged
or persisted by Milestone 5.

Milestone 12 preserves local-only as the default model data policy. Local
provider origins are restricted to HTTP loopback endpoints. The optional
OpenRouter adapter accepts only the fixed official HTTPS origin, follows no
redirects, ignores environment proxy configuration and applies bounded connect,
read, write and pool timeouts plus prompt, catalog, SSE-frame and response-size
limits. A missing/rejected key, removed model, timeout or rate limit fails as a
provider error and enters the router's visible fallback path.

The OpenRouter key is read only by the core from
`MOJ_ASYSTENT_OPENROUTER_API_KEY`, removed from the environment immediately and
explicitly scrubbed from ToolEngine child environments. It is never returned to
React, stored in SQLite/localStorage/config files or logged. Native Windows
credential provisioning is not implemented, so cloud setup remains an
operator-controlled optional path.

Private mode, the default `local_only` policy, secret-like text in the current
or bounded recent conversation, persistent memory records, screenshots, tool
requests and destructive confirmation data cannot select the cloud candidate.
Provider output and tool proposals remain
untrusted. The existing typed ToolEngine, local policy, confirmation tickets
and deterministic executors remain authoritative regardless of the selected
model. Model routing itself has no permission-writing or OS-action surface.

## Logs

Do log:

- state transitions;
- tool names;
- validation failures;
- success/failure metadata;
- performance timing;
- watcher triggers.

Milestone 11 watchers require an explicit `explicit_intent` request, accept
only the six typed categories, canonicalize file targets through the existing
root/reparse-point policy and bind process/window identities to stable
metadata. The scheduler is capped at 32 active definitions and four
concurrent checks. It persists only bounded structured state and 256 event
records; screenshots, raw telemetry and full logs are excluded. An ambiguous
identity or provider failure stops the watcher, and a watcher event never
authorizes a tool or runs a routine. The typed watcher-management tools mark
creation and persistent changes as sensitive, so the normal exact-target
confirmation flow still applies even when a model proposes the definition.

Do not log by default:

- raw microphone audio;
- screenshot bytes;
- passwords/tokens;
- entire sensitive documents;
- full prompts containing unnecessary private context.

Provide a debug mode with explicit warnings if richer diagnostic logging is ever needed.

## Gaming/anti-cheat boundary

- no DLL injection;
- no protected game-process memory reads;
- no features designed to bypass anti-cheat;
- prefer OS-level telemetry and post-crash logs;
- overlay/proactive behavior can disable itself for configured competitive games.

## Confirmation UX

A sensitive confirmation must show:

- requested action;
- exact target;
- relevant consequences;
- Allow / Cancel;
- persistent approval only if policy marks it safe.

Never use ambiguous confirmation such as `Continue?` when exact action can be shown.

## Tool result integrity

The model must not report success until the executor returns a success result.

Tools return structured outcomes, for example:

```json
{
  "ok": false,
  "error_code": "PROCESS_ACCESS_DENIED",
  "message": "Access denied while stopping process"
}
```

The assistant should communicate the actual failure and possible next step.

## Emergency controls

- tray `Wycisz mikrofon`;
- stop current generation/action;
- disable proactive watchers;
- exit application;
- future optional emergency-stop shortcut for active tool sequences.

## Secrets and repository hygiene

Never commit:

- `.env` files;
- API keys/tokens;
- local SQLite DBs;
- model weights;
- user voice recordings;
- wake-word training datasets;
- generated personal wake models;
- screenshots/log dumps containing private data.

See root `.gitignore`.
