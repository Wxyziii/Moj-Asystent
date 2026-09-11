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

## Network

V1 local-first.

- no cloud AI required;
- all network calls should have explicit purpose;
- model downloads are user-visible setup actions;
- future integrations use scoped credentials and OS credential storage;
- secrets never committed to repository or SQLite in plaintext when avoidable.

The desktop/core voice boundary additionally requires a per-launch random credential. Tauri passes it directly to the owned core process, and the core validates it before health responses, audio-control requests or WebSocket acceptance. Credentials are memory-only, omitted from logs and invalid after application restart. HTTP and WebSocket remain restricted to loopback hosts and known desktop origins, with strict payload-size and schema validation. This limits accidental cross-origin/local access; it does not defend against a malicious process with access to the same user's process memory.

The core removes both credentials from its process environment after startup and explicitly scrubs them from tool-launched child environments. Fixed application aliases resolve to absolute Windows system executables rather than relying on the working directory or `PATH`.

Approved process restart binds PID, creation time, executable name and the resolved allowlisted image. Relaunch explicitly pins that verified executable even if the captured command line uses a different or relative first argument.

Wake-name onboarding uses the same boundary. Session, calibration, training, validation and activation endpoints require the credential and current exact Protocol 1.3. Session IDs are UUIDs, request models reject unknown fields, base64 PCM is bounded before decoding, and model activation accepts only a validated ONNX file in the service-owned model directory. Status responses redact filesystem paths. Temporary recordings and partial models are cleaned up on cancellation, failure and shutdown; the active model is replaced only through an atomic file operation.

Local chat requests use the authenticated core API. The core alone contacts
Ollama, accepts only an HTTP loopback origin, disables environment proxy use,
validates bounded provider frames and publishes only typed Protocol 1.3 events.
Prompts and response text are held in bounded process memory and are not logged
or persisted by Milestone 5.

## Logs

Do log:

- state transitions;
- tool names;
- validation failures;
- success/failure metadata;
- performance timing;
- watcher triggers.

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
