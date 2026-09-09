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
