# Persistent memory and routines

Milestone 10 introduces a small, local SQLite store under the user's application
data directory. The default path is `%LOCALAPPDATA%/MojAsystent/memory.sqlite3`
(with the standard per-user AppData fallback). The database is runtime data: it
is never created in the repository and is not synchronized or uploaded.

## Storage boundary

`SQLiteMemoryStore` opens a short-lived connection for each operation, enables
foreign keys, WAL and a bounded busy timeout, and runs all SQL through
parameterized statements. The `PRAGMA user_version` value is the schema
version. Startup refuses a newer version and fails closed on malformed or
corrupt data; it never deletes a database to recover. Schema migrations are
explicit transactions.

The v1 schema contains settings, completed conversation messages, preferences,
approved memories, app/project aliases and routine definitions/steps. The
Milestone 11 migration to schema v2 adds watcher definitions and bounded
structured watcher event history without creating another database. The
existing tool-permission policy remains a separate validated JSON file: its
security semantics and fail-closed recovery are already established, and
mixing policy writes with user memory deletion would make privacy controls less
predictable.

## Explicit memory

Preferences and memories are written only after an explicit user request (or
from the settings API). The assistant does not extract durable memories from
ordinary conversation. Secret-like keys and credential-shaped values are
rejected before SQL. Every stored value is bounded, reviewable and removable.
Preferences use a stable normalized key and are updated explicitly; durable
memories have a UUID, source and optional expiry.

When the local model is called, retrieval is deterministic: exact/substring
matches over normalized keys and values, recent preferences for preference
questions, approved non-expired memories and matching aliases. At most eight
items and 4 KiB are inserted as a clearly labelled, untrusted system context.
There is no vector database, embedding index or automatic semantic extraction.

## Aliases

Aliases are short names for an application or project target. They are
case/space-normalized, unique per kind and require explicit overwrite to
change. An alias only resolves to data; it never bypasses the existing
application registry, canonical path policy or confirmation engine.

## Routines

A routine is a named, bounded sequence of already registered tool names and
validated JSON arguments (one to sixteen steps). The API supports create, list,
inspect, rename, run and delete. Runs reuse the normal `ToolEngine`, including
per-tool policy, confirmation, timeout and cancellation rules; they do not
interpret shell commands, loops, conditions or arbitrary scripts. A running
routine cannot be deleted and shutdown cancels active runs.

An explicit Polish request such as `Utwórz rutynę „Praca”...` can be turned by
the local model into the typed `create_routine` proposal. Saving it is a
sensitive action that still requires the existing confirmation UI; the backend
revalidates every referenced tool and argument before writing.

## Privacy controls

Conversation history retention is off by default. When enabled, only completed
user/assistant turns are stored; deltas, audio, screenshots, prompts and tool
arguments are not persisted. The settings API and desktop settings panel can
toggle history, clear history, review/delete individual durable memories, or
clear all memories and aliases. Clearing memories intentionally leaves
conversation history and routines untouched; those have separate controls.

Milestone 11 adds watcher definitions and bounded structured watcher event
history to this same database. Watchers are explicit, cancellable and
deterministic; they never become an automatic memory-extraction path. See
[`WATCHERS.md`](WATCHERS.md) for supported categories, lifecycle and privacy
limits.
