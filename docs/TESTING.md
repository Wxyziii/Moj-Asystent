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
- silence timeout;
- follow-up timeout;
- cancel during STT/TTS;
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
