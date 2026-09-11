# Windows Context

## Scope

Milestone 7 supplies structured, request-scoped Windows context. Milestone 8
adds a bounded screenshot fallback for the active window or an explicitly
selected region. It never continuously monitors the desktop.

The two implemented read paths are:

- `get_active_window` — inexpensive best-effort foreground-window metadata;
- `read_ui_tree` — the same metadata plus a bounded UI Automation snapshot and
  focused/selected text when the control exposes it.

The model is instructed to request these tools only when a question depends on
the current window, visible interface or selection. UI text is untrusted data:
it cannot grant consent, change permission policy or become an instruction to
the assistant.

## Snapshot contract

Each full snapshot contains:

- a unique `context_id`, UTC capture time and user-provenance request reason;
- source/provenance metadata on every observation;
- active window identity and best-effort process, executable, title, class,
  monitor and bounds fields;
- a flattened UI tree whose `path` values preserve hierarchy;
- focused/selected text availability with an explicit unavailable reason;
- explicit truncation flags and skipped-node counts;
- per-stage and total elapsed timings;
- screenshot provenance when the visual fallback is requested.

## Visual fallback

`inspect_screen` captures the current active window only for an explicit visual
question or after structured context proves insufficient. A Tauri selector can
capture one rectangle constrained to the monitor containing the overlay and
fully contained in the active window. Each
capture carries context, operation and capture IDs, UTC time, exact foreground
window identity, physical bounds, monitor identity and capture/normalization
timings.

The core checks exclusions before allocating image pixels and verifies the
foreground identity again after capture. A focus change or newly excluded
target discards the image. Raw BGRA and normalized JPEG buffers remain in RAM,
are limited to one image per request, and are cleared after completion,
cancellation, failure, replacement, expiry or shutdown. React receives only a
small bounded JPEG preview.

Defaults cap capture at 4096 × 4096 and 12 million pixels, normalize the longest
edge to 1280 px, cap encoded model input at 1.5 MB, and time capture out after
2 seconds. Region coordinates are physical pixels and support non-primary
monitors with negative origins and 0.5–4.0 DPI scale factors.

The active-window identity includes the native window handle, process ID and
process creation time when available. The provider reads it again after UI
Automation traversal. If focus moved to another identity, collected UI content
and selection are discarded rather than attached to the new window.

## Bounds and lifecycle

UI Automation runs on one lazy, dedicated MTA worker. The worker initializes and
owns the native backend, all COM objects remain on that thread, and shutdown
cancels active work before closing the backend. Its queue is bounded so callers
fail quickly instead of creating unlimited inspection work. One-time backend
initialization does not consume the traversal timeout.

Default per-request limits are:

| Limit                |        Default |   Hard maximum |
| -------------------- | -------------: | -------------: |
| UI nodes             |            120 |            512 |
| Tree depth           |              6 |             16 |
| Text per property    | 320 characters | 512 characters |
| Aggregate UI payload |    4,800 bytes |    6,000 bytes |
| Traversal time       |       1,500 ms |       5,000 ms |

Before the tool result reaches the model, the complete serialized context is
also reduced to fit a 7,000-byte budget. Truncation is explicit; missing data is
never invented. Timeout and cancellation are cooperative between nodes and
property reads. A native accessibility provider that blocks inside one COM call
cannot always be interrupted immediately, but it cannot block the asyncio event
loop.

## Privacy and exclusions

Context is kept in memory for the current request and is not persisted. Normal
logs exclude window titles, selected text and UI strings. Password controls are
recognized through the native password property and their value, text and
selection are removed.

The default process exclusions are `1Password.exe`, `Bitwarden.exe`,
`KeePass.exe` and `KeePassXC.exe`. Exclusions are case-insensitive. Exact process
names and title fragments can be extended through environment configuration.
The exclusion check happens before UI Automation; excluded responses redact the
window title, executable path and bounds.

Accessibility metadata is best effort and cannot guarantee detection of every
secret or custom-rendered control. Screenshot pixels do not receive automatic
password-field redaction. Add sensitive applications to the exclusion list
rather than relying on password detection.

## Configuration

| Variable                                           | Meaning                                      |
| -------------------------------------------------- | -------------------------------------------- |
| `MOJ_ASYSTENT_CONTEXT_ENABLED`                     | Set false/off/0 to disable Windows context.  |
| `MOJ_ASYSTENT_CONTEXT_MAX_NODES`                   | Maximum UI nodes per snapshot.               |
| `MOJ_ASYSTENT_CONTEXT_MAX_DEPTH`                   | Maximum UI hierarchy depth.                  |
| `MOJ_ASYSTENT_CONTEXT_MAX_TEXT_LENGTH`             | Maximum characters retained per UI property. |
| `MOJ_ASYSTENT_CONTEXT_MAX_PAYLOAD_BYTES`           | Aggregate serialized UI-node budget.         |
| `MOJ_ASYSTENT_CONTEXT_TIMEOUT_MS`                  | UI traversal timeout.                        |
| `MOJ_ASYSTENT_CONTEXT_EXCLUDED_APPS`               | Comma-separated exact executable names.      |
| `MOJ_ASYSTENT_CONTEXT_EXCLUDED_TITLES`             | Pipe-separated window-title fragments.       |
| `MOJ_ASYSTENT_VISION_MAX_CAPTURE_WIDTH` / `HEIGHT` | Maximum source dimensions.                   |
| `MOJ_ASYSTENT_VISION_MAX_CAPTURE_PIXELS`           | Maximum source pixel count.                  |
| `MOJ_ASYSTENT_VISION_MAX_DIMENSION`                | Longest normalized edge.                     |
| `MOJ_ASYSTENT_VISION_MAX_IMAGE_BYTES`              | Maximum encoded JPEG bytes.                  |
| `MOJ_ASYSTENT_VISION_CAPTURE_TIMEOUT_MS`           | Screenshot worker timeout.                   |

Invalid or out-of-range configuration fails during settings validation rather
than silently weakening bounds.

## Manual validation status

On the target Windows machine, Explorer returned active-window metadata and a
bounded UI tree after a cold worker start. A newly opened blank Notepad window
also returned a bounded tree and focused-control metadata, and was then closed
without saving. Selected-text availability and unavailable fallbacks are covered
by deterministic adapter tests; manual selection behavior still varies by the
accessibility support of the target application.

Milestone 8 validation used newly opened safe/blank windows only. Active-window
capture succeeded for blank Notepad, File Explorer at `C:\Windows`, an empty
VS Code window and an InPrivate `about:blank` Edge window; physical capture
bounds matched the foreground metadata at the tested 100% DPI setting. A
programmatically bounded region inside blank Notepad also captured successfully.
The native drag selector is covered by TypeScript coordinate tests and Rust
window checks, but still needs a human drag test on a multi-monitor/high-DPI
setup. Calculator window discovery did not complete in the non-interactive
validation harness. A real Notepad process placed on the exclusion list was
rejected before an image was attached.

On this machine, a 1440 × 753 Notepad capture normalized to 1280 × 669 in about
72 ms wall time (approximately 16 ms capture and 31 ms normalization) and
produced a 22.6 KB JPEG. Qwen3.5 4B returned its first visual token in about
13.5 s and completed in 16.4 s; the comparable warmed text-only request returned
its first token in 0.30 s and completed in 0.76 s. The visual answer recognized
a blank text editor but was uncertain and misidentified the exact Notepad
variant, so visual descriptions remain best-effort rather than authoritative.
