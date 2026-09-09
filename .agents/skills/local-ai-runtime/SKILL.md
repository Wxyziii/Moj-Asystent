---
name: local-ai-runtime
description: Implement or review Moj-Asystent local LLM integration and routing: Ollama/Qwen provider interfaces, Polish prompting, streaming, conversation context, model health, privacy modes, and later llama.cpp quality/deep tiers. Use for Phase 5 and Phase 12 AI/model-runtime work.
---

# Local AI Runtime

Read `docs/AI_MODELS.md`, `docs/ARCHITECTURE.md`, and the relevant Phase 5/12 section of `docs/IMPLEMENTATION_PLAN.md` first.

## Core rules

- Keep model vendors/runtimes behind provider interfaces. Product logic must not depend directly on Ollama response shapes.
- V1 interaction language is Polish. Spoken responses should be concise; overlay text may be richer.
- Main target is Qwen3.5 4B on RTX 3070 8 GB-class hardware. Do not assume unlimited VRAM.
- Never let the model claim a system action succeeded without a confirmed tool result.
- Prefer deterministic tools/context retrieval over asking the LLM to guess machine state.
- Stream tokens/events through the typed core protocol while preserving cancellation and clean error states.
- Keep local-only privacy mode genuinely local. Cloud providers, if added later, must be optional and explicit.
- Bound conversation/context growth. Include only relevant screen/system/tool context and support cancellation/retry.
- Do not load larger models for simple commands. Phase 12 routing should consider task complexity, hardware availability, memory pressure, and explicit deeper-reasoning requests.
- Treat 9B/27B tiers as optional capabilities; the product must remain functional without them.

## Verification

Use provider fakes for deterministic tests. Test streaming, cancellation, provider failure, model unavailable/loading states, Polish prompt constraints, context trimming, and router fallback. Benchmark latency/VRAM only when the target runtime/hardware is actually available; label estimates as estimates.