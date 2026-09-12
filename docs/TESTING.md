# Testing Strategy

## Goals

The assistant interacts with microphone input, local AI, Windows APIs, user files and sensitive system actions. Tests must cover not only correctness but also safe failure behavior.

## Unit tests

Prioritize:

- assistant state-machine transitions;
- tool argument validation;
- permission evaluation;
- path normalization/allowlists;
- deterministic intent routing;
- model-router decisions;
- context minimization;
- watcher condition logic;
- Polish vocabulary normalization;
- configuration loading/migrations.

## Contract tests

For the shared protocol:

- Python -> JSON -> TypeScript schema compatibility;
- TypeScript -> JSON -> Python compatibility;
- unknown event/version behavior;
- required correlation IDs;
- malformed payload rejection.

## Provider tests

Every provider interface should have fake implementations for CI.

Real-hardware/integration suites can test:

- openWakeWord;
- Silero VAD;
- faster-whisper;
- Piper;
- Ollama/Qwen;
- UI Automation;
- NVML.

CI should not require large model downloads unless a dedicated workflow explicitly opts in.

## Permission/security tests

Required cases:

- `read` tool allowed under normal policy;
- `write.safe` policy behavior;
- `sensitive` action cannot execute before confirmation;
- model cannot bypass permission with crafted arguments;
- path traversal rejected;
- disallowed app/path rejected;
- failed executor result cannot be reported as success;
- timeout/cancellation leaves system in safe state.

## Audio tests

Automated/state:

- sleep -> wake -> listen;
- VAD end-of-utterance;
- exact bounded pre-roll/post-roll and maximum utterance buffer;
- silence timeout;
- follow-up timeout;
- cancel during STT/TTS;
- deterministic CUDA selection, CPU fallback and missing CUDA/model handling;
- vocabulary validation/serialization and transcript-free diagnostic bounds;
- low-confidence voice requests cannot reuse approval for state-changing tools;
- microphone unavailable/reconnect.

Manual/evaluation dataset:

- custom wake phrase at normal/quiet/louder volumes;
- different head positions;
- keyboard/fan/music noise;
- Polish technical vocabulary;
- false-positive speech without wake name.

## Wake-word metrics

Track at least:

- wake recall on user validation samples;
- false activations during negative test;
- detection latency;
- confidence distribution by recording condition.

Do not rely on training accuracy alone.

## Completed target-hardware validation

Manual testing completed on 2026-09-10 confirms that the Milestone 3 microphone path detects real human speech on the target Windows hardware and that the Milestone 4 user-trained wake call activates the assistant successfully. The trained name was recognized hands-free, the overlay entered listening mode, and the following utterance reached transcription.

This validates the basic real-device voice and wake path. Polish STT accuracy remains model-, microphone- and utterance-dependent and is tracked separately from wake-call success.

Milestone 5 was also validated against a real local Ollama 0.34.0 runtime with
`qwen3.5:4b` on the target machine. The production provider reported `ready`,
accepted the Polish system prompt, streamed a complete response through its
validated adapter and answered the Polish language check in Polish. Automated
tests separately cover desktop stream assembly, cancellation, missing/runtime
failure states and the authenticated core boundary.

Milestone 6 safe-tool smoke testing on 2026-09-11 exercised real Windows system
stats, process listing, a temporary directory and 25-byte UTF-8 temporary file,
allowlisted Calculator launch, and volume set/restore (37% back to the original
60%). No power action, real-user file deletion, process restart or other
destructive operation was used. The first run exposed Windows PID 0 and empty
executable metadata; both are now normalized behind a regression test.

Milestone 12 target inspection on 2026-09-12 found an RTX 3070 with 8 GiB VRAM
and the official `qwen3.5:4b` Ollama model installed. A harmless Polish CLI
prompt loaded that Fast model on GPU and returned the correct Polish answer.
`qwen3.5:9b`, llama.cpp/Deep and OpenRouter were not installed or configured,
so Quality, Deep, cloud, high-load behavior and restart preference persistence
have automated fake-provider coverage but still require the manual model-tier
matrix before release. The complete canonical V1 flow below was not rerun as
part of this implementation pass.

Milestone 13 adds automated coverage for Turbo CUDA/CPU selection, provider
failure fallback, provider-generation cancellation, VAD edge padding and buffer
limits, local vocabulary persistence, authenticated settings, privacy-safe
diagnostics, no normal PCM persistence, benchmark WER/path validation and
low-confidence ToolEngine hardening. The implementation did not download a
model or record microphone audio. Real `large-v3-turbo` CUDA accuracy, latency,
short-word recognition, mixed technical speech, follow-up transcription and
forced fallback still require the documented target-hardware pass; no accuracy
improvement is claimed from automated tests alone. Read-only target inspection
confirmed an RTX 3070 (8 GiB), cached Medium and `large-v3` models, and no cached
`large-v3-turbo`. A one-second in-memory silence probe reached the real
CTranslate2 CUDA path and found `cublas64_12.dll` unavailable; after the status
regression fix, the provider reports `cuda_unavailable` and the selector reports
Medium CPU fallback instead of claiming GPU readiness. No microphone was opened
and no audio file was written during this probe.

## Model evaluation

Maintain a small Polish benchmark set covering:

- tool choice;
- argument extraction;
- follow-up context;
- screen-error explanation;
- system diagnostics;
- coding/log explanation;
- sensitive-action confirmation behavior.

Record:

- answer quality;
- tool accuracy;
- first-token latency;
- total latency;
- VRAM/RAM use.

## UI tests

- overlay state rendering;
- keyboard navigation;
- confirmation workflow;
- multi-monitor placement;
- reconnect/error state;
- reduced motion;
- responsive settings layout.

## End-to-end release scenario

V1 release testing must include the canonical flow:

1. start app fresh;
2. choose/train custom name;
3. wake hands-free;
4. ask a Polish system-telemetry question;
5. receive spoken + overlay answer;
6. issue follow-up without wake name;
7. ask a screen-context question;
8. execute a safe tool;
9. attempt a sensitive tool and verify confirmation;
10. disable microphone listening and verify wake no longer activates.
