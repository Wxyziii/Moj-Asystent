# AI Model Strategy

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

Suggested interfaces:

```python
class ChatProvider(Protocol):
    async def generate(self, request: ChatRequest) -> ChatResponse: ...

class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
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

## Cloud fallback

Not required for V1.

If introduced later:

- disabled by default;
- clearly labeled;
- never silently upload screen/audio/private files;
- per-request consent/policy for sensitive context;
- local tool permissions remain authoritative.
