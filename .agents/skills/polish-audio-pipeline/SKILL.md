---
name: polish-audio-pipeline
description: Implement or debug Moj-Asystent's Polish-only local voice pipeline using microphone capture, openWakeWord, Silero VAD, faster-whisper forced to pl, Piper/replaceable TTS, follow-up timing, and audio state transitions. Use for Phase 3 voice/audio work.
---

# Polish Audio Pipeline

Read `docs/VOICE_SYSTEM.md` and Phase 3 of `docs/IMPLEMENTATION_PLAN.md` before editing.

Optimize for low idle cost and Polish-only V1 behavior.

## Pipeline

`SLEEPING -> WAKE_DETECTED -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> FOLLOW_UP -> SLEEPING`

- Keep wake-word detection lightweight while idle; do not run Whisper or an LLM continuously.
- Force faster-whisper to Polish (`pl`) rather than spending work on language detection.
- Use Silero VAD for speech start/end boundaries; keep thresholds configurable and testable.
- Keep wake, VAD, STT, and TTS behind provider interfaces so implementations can be replaced.
- Keep only the minimum rolling audio required in RAM. Do not persist microphone recordings by default.
- Make microphone/device changes and stream failures recoverable without restarting the whole app where possible.
- Prevent TTS output from causing uncontrolled wake loops. Follow the V1 barge-in limitations in the project docs.
- Keep state/event transitions visible to the desktop overlay via the typed protocol.
- Favor CPU execution for wake/VAD/STT/TTS when practical so RTX VRAM remains available for the LLM.

## Verification

Test state transitions, VAD end-of-speech behavior, wake-to-record latency, STT error handling, follow-up timeout, TTS cancellation, device loss/recovery, and privacy defaults. Separate deterministic unit tests from hardware/microphone integration checks, and report which hardware checks were actually performed.