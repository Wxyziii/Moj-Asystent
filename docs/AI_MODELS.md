# AI Model Strategy

## Implemented model routing (Milestone 12)

The conversation orchestrator now requests a capability and tier from a
deterministic `ModelRouter`; it does not select an Ollama tag itself. The
configured tiers are:

- Fast/default: `qwen3.5:4b` through loopback Ollama;
- Quality: optional `qwen3.5:9b` through loopback Ollama;
- Deep/experimental: a user-supplied Qwen3.5 27B GGUF through a loopback
  llama.cpp server, with bounded context and configurable GPU offload;
- optional online Deep fallback: an explicitly configured OpenRouter model,
  disabled unless `cloud_allowed` is selected.

The 4B and 9B choices were rechecked against the
[official Ollama Qwen3.5 catalog](https://ollama.com/library/qwen3.5), and the
27B architecture against the
[official Qwen repository](https://huggingface.co/Qwen/Qwen3.5-27B), on
2026-09-12. The application never invents or downloads a Deep tag: the GGUF
file and llama.cpp executable/server are explicit operator configuration.

Modes are `private`, `fast`, `quality`, `deep` and `auto`. `private` is always
local-only. The default data policy is `local_only`; online routing requires a
separate explicit opt-in. Secret-like content in both the current request and
bounded recent conversation prevents an online route. Polish escalation phrases such as `Przemyśl to
dokładniej`, `Użyj lepszego modelu` and `Przeanalizuj to głębiej` affect one
operation only and do not overwrite the stored global preference.

Auto routing uses bounded, explainable signals: request/context size,
reasoning/coding structure, required image/tool capabilities and request-driven
Milestone 9 RAM/VRAM/GPU-load telemetry. It does not call a model to choose a
model. Under high GPU load it stays on Fast unless the user explicitly asks for
an escalation. Predictable local capacity guards require at least 6 GiB free
RAM for Quality, at least 6 GiB total and 2 GiB free VRAM when VRAM data is
available, and at least 24 GiB total/16 GiB free RAM for the hybrid Deep tier.
These are conservative admission guards, not benchmark claims.

Fallback order is `Deep -> Quality -> Fast` and `Quality -> Fast`. Local
candidates are considered before online candidates at the same tier. Every
downgrade is included in response metadata and shown in the overlay. Required
image and tool capabilities are hard filters, so text-only providers cannot
receive screenshots or participate in a tool-required operation.

Only one selected provider is active for an operation. Switching providers
unloads the previous one where supported, Ollama uses bounded `keep_alive`, and
an idle task unloads the active provider after five minutes and never unloads
during an active generation. Cancellation is preserved across provider streams
and performance samples are held only in a bounded 64-entry process-memory
buffer. Samples include first-output/total latency, output characters and exact
token/provider-cost metadata only when the provider returns it. OpenRouter cost
is labelled as `openrouter_credits`; the core never assumes a currency,
estimates pricing or persists cost.

## Initial local chat foundation (Milestone 5)

The initial chat path uses `qwen3.5:4b` through Ollama at the loopback-only
origin `http://127.0.0.1:11434`. The core validates provider responses and
streams display text through the current exact Protocol 1.4; the desktop never talks to Ollama
directly. `MOJ_ASYSTENT_OLLAMA_URL` and `MOJ_ASYSTENT_LLM_MODEL` may select a
different loopback Ollama origin or compatible local model without changing
conversation orchestration.

Conversation history is process-local and bounded to six completed turns and
12,000 characters of recent user/assistant content. Cancelled or failed turns
are not committed. This is short request context, not the persistent memory
planned for a later milestone.

The Polish system prompt explicitly limits the current assistant to
conversation and prevents claims that a system action was executed. Rich text
is streamed to the overlay. Voice uses a deterministic summary of at most two
sentences and 360 characters before Piper playback, preserving a concise spoken
experience without making a second model request.

The UI distinguishes unavailable Ollama, a missing model, active loading,
ready and failed generation states. Model installation remains explicit; the
application does not silently download multi-gigabyte weights.

## Goals

- Keep ordinary voice interactions fast.
- Run locally by default.
- Avoid requiring one huge model for every request.
- Reuse one multimodal model for text + screen understanding where practical.
- Scale from RTX 3070 8 GB / 32 GB RAM upward.
- Keep providers swappable.

## Initial model tiers

### Tier 1 — Main assistant

**Qwen3.5 4B**

Purpose:

- normal Polish conversation;
- tool selection;
- short reasoning;
- screen/image understanding;
- contextual troubleshooting.

Why:

- fits the initial 8 GB VRAM target much more comfortably than larger models;
- leaves room for context/runtime overhead;
- multilingual capability is useful even though V1 is Polish-only;
- multimodal support reduces the need for a separate vision model initially.

Runtime: Ollama first.

### Tier 2 — Quality mode

**Qwen3.5 9B**

Purpose:

- harder reasoning;
- richer analysis;
- tasks where 4B quality is insufficient.

Loading policy:

- optional;
- load only if VRAM/RAM conditions allow;
- not resident during gaming/high GPU pressure by default.

### Tier 3 — Experimental deep mode

**Qwen3.5 27B** or another future model selected after benchmarking.

Runtime: llama.cpp with CPU/GPU hybrid offload.

Purpose:

- difficult coding/debugging analysis;
- long planning/reasoning tasks;
- explicit user request for deeper analysis.

This tier is not expected to feel instant on the initial RTX 3070 system.

## Why not use a large hybrid model as the main assistant?

Assistant UX is dominated by latency. A wake-word voice assistant that takes a long time to answer feels worse even if the model is smarter.

Hybrid inference is useful as an escalation path, not as the default for commands such as:

- `Otwórz Spotify.`
- `Ścisz dźwięk.`
- `Ile mam użytego VRAM?`

Many of those should avoid an LLM entirely after intent is safely known.

## Deterministic fast paths

The router should support direct handling for high-confidence structured commands.

Example:

```text
"Ustaw głośność na 20 procent"
        -> deterministic intent
        -> validate volume=20
        -> set_volume(20)
        -> tool result
        -> concise response
```

Use an LLM when language is ambiguous, reasoning is required, or a tool sequence must be planned.

## Router inputs

Model routing should consider:

- request type;
- context size;
- whether an image is needed;
- tool complexity;
- available VRAM;
- available RAM;
- current GPU load;
- whether a game is active;
- user-selected mode: Fast / Balanced / Deep;
- privacy mode.

## Provider abstraction

Implemented provider-neutral main-chat interface:

```python
class ChatProvider(Protocol):
    async def status(self) -> ModelStatus: ...
    def stream_turn(
        self, request: LanguageModelRequest
    ) -> AsyncIterator[ModelStreamEvent]: ...
    async def unload(self) -> None: ...  # optional capability
    async def close(self) -> None: ...
```

No business logic should depend directly on Ollama-specific response objects.

## Polish prompt behavior

System prompt requirements:

- respond in Polish;
- keep spoken output concise/natural;
- richer overlay response may be longer;
- use tool results as facts;
- never fabricate tool success;
- ask for confirmation through the permission system rather than pretending to execute a sensitive action;
- distinguish observation from inference.

## Context strategy

Do not send the entire desktop state to the model.

Request only necessary providers, for example:

```text
"Dlaczego GPU jest na 99%?"
 -> telemetry + top GPU processes
 -> no screenshot unless needed
```

```text
"Co oznacza ten błąd?"
 -> active window + UI tree
 -> screenshot if structured text is insufficient
```

## Memory/context window

Persistent memory lives in SQLite, not in permanent model context.

For each request:

1. retain short conversational context;
2. retrieve relevant structured memory;
3. retrieve relevant screen/system context;
4. trim irrelevant data;
5. send the smallest useful request.

For multimodal requests, the existing Ollama provider adds at most one bounded
JPEG image to the relevant user message. The image is an ephemeral side input,
not a second vision conversation and not part of retained conversation history.
Visible image text is untrusted observation and cannot authorize tools or alter
the system prompt. Any action proposed after visual analysis still traverses the
typed registry, local policy and confirmation flow.

## Future model evaluation

Benchmark candidates using real project tasks, not generic leaderboards.

Minimum evaluation set should include Polish:

- tool selection accuracy;
- parameter extraction;
- follow-up pronoun/context resolution;
- screen-error explanation;
- Windows troubleshooting;
- coding/log analysis;
- refusal/confirmation behavior for sensitive actions;
- latency;
- tokens/sec;
- VRAM/RAM usage;
- first-token latency;
- image-processing latency.

Record benchmark results in a future `docs/benchmarks/` directory.

## Optional cloud fallback

OpenRouter is an optional text-only provider seam, not a V1 dependency. It uses
only the fixed official HTTPS origin, rejects redirects, ignores environment
proxies, validates model availability and bounds prompts, SSE frames, output and
timeouts. A model ID and `MOJ_ASYSTENT_OPENROUTER_API_KEY` must be supplied to
the core environment; the key is removed from that environment at startup,
never enters React, SQLite, logs or repository configuration, and is scrubbed
from tool child processes. Windows credential-manager provisioning is not yet
implemented.

Cloud is considered only when both a cloud model is configured and the stored
policy is `cloud_allowed`. Private mode, secret-like text, images, tool calls
and persistent-memory context remain local. Cloud output is untrusted and any
future cloud tool proposal must traverse the same ToolEngine, permission policy,
confirmation and deterministic executor. No billing logic or guessed price is
implemented.
