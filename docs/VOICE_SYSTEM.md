# Voice System

## V1 scope

Polish-only voice interaction with hands-free activation through a user-trained assistant name.

## Pipeline

```text
Microphone
   -> input normalization
   -> rolling RAM buffer
   -> openWakeWord
   -> wake detected
   -> Silero VAD
   -> utterance capture
   -> faster-whisper (`language="pl"`)
   -> orchestrator
   -> response text
   -> Piper pl_PL
   -> speakers
```

## Milestone 3 implementation

The core now owns a cancellable asyncio audio pipeline. `sounddevice` supplies bounded 16-bit mono PCM frames through a thread-safe ingress; openWakeWord runs only in idle, Silero VAD bounds utterances, faster-whisper is forced to `language="pl"`, and Piper plays a configured `pl_PL` ONNX voice outside the event loop. Until Milestone 5, processing emits the deterministic response `Usłyszałem: … To testowa odpowiedź bez modelu AI.`

Audio bytes and VAD buffers remain in RAM and are cleared after completion, cancellation, failure or shutdown. Provider work is tagged with a generation and operation UUID, so results finishing after cancellation cannot publish events or mutate the authoritative state. Wake processing is suppressed during TTS. Microphone failures retry after a bounded delay, while the shortcut and text overlay remain available.

Configuration uses `MOJ_ASYSTENT_MICROPHONE_DEVICE`, `MOJ_ASYSTENT_SAMPLE_RATE`, `MOJ_ASYSTENT_WAKE_SENSITIVITY`, `MOJ_ASYSTENT_VAD_START_THRESHOLD`, `MOJ_ASYSTENT_VAD_END_THRESHOLD`, `MOJ_ASYSTENT_MAX_UTTERANCE_SECONDS`, `MOJ_ASYSTENT_STT_MODEL`, `MOJ_ASYSTENT_TTS_VOICE_PATH`, `MOJ_ASYSTENT_WAKE_MODEL_PATH`, `MOJ_ASYSTENT_FOLLOW_UP_SECONDS` and `MOJ_ASYSTENT_VOICE_RESPONSES`. If installed, the default Polish Piper voice is discovered under `%LOCALAPPDATA%/Moj-Asystent/models/piper/pl_PL-gosia-medium.onnx`; model downloads are explicit setup actions and remain outside the repository.

## Milestone 4 custom wake name

The first-run desktop wizard now owns name suitability, microphone calibration, guided Polish curriculum, sample quality feedback, local training progress, wake-only validation and sensitivity selection. During browser recording the core pauses its live capture so the webview owns the microphone; it resumes only after cancellation or atomic activation of a validated model. On first launch, no live microphone capture starts until an active model exists.

Target-hardware validation completed on 2026-09-10: real human microphone input was detected, the trained wake call activated the assistant, and the wake → listening → transcription path completed successfully.

## Assistant state machine

```text
SLEEPING
  -> WAKE_DETECTED
  -> LISTENING
  -> TRANSCRIBING
  -> THINKING
  -> SPEAKING
  -> FOLLOW_UP
  -> SLEEPING
```

### SLEEPING

- Wake model is active if microphone listening is enabled.
- Full STT and LLM are inactive.
- Keep only a short rolling audio window in RAM.
- Discard old audio immediately.

### WAKE_DETECTED

- Emit UI/audio acknowledgment.
- Open compact overlay.
- Start utterance capture.

### LISTENING

- Silero VAD detects speech and trailing silence.
- Overlay shows waveform/state indicator.
- User can cancel.

### TRANSCRIBING

- faster-whisper processes the completed utterance.
- Force Polish instead of language auto-detection.
- Emit partial transcript only if implementation is stable enough.

### THINKING

- Orchestrator gathers required context/tools/model output.
- Overlay shows progress without exposing hidden chain-of-thought.

### SPEAKING

- TTS speaks concise answer.
- Overlay can display a richer answer.
- V1 may suppress general wake handling while TTS is active to prevent self-triggering.

### FOLLOW_UP

- Keep short conversational context.
- Listen for the user's next sentence without requiring the wake name again.
- Configurable timeout, initial target around 15–30 seconds.
- Return to SLEEPING on timeout/cancel.

## STT

Initial implementation:

- faster-whisper Medium by default (override with `MOJ_ASYSTENT_STT_MODEL=small` on lower-memory machines);
- Polish forced;
- CPU INT8 first;
- benchmark Whisper Small and Medium;
- keep GPU available primarily for the LLM.

## Polish vocabulary hints

Maintain a configurable technical vocabulary list such as:

- GitHub
- Git
- Visual Studio Code
- PowerShell
- Proxmox
- NVIDIA
- Steam
- Spotify
- Ollama
- Qwen
- Docker
- localhost

Corrections must be conservative: do not replace uncertain ordinary Polish words simply because a technical term exists in the dictionary.

## VAD

Silero VAD determines speech boundaries.

Parameters should be configurable and calibrated for:

- microphone noise floor;
- typical speaking distance;
- room noise;
- keyboard/fan noise.

## TTS

### V1

Piper Polish voice.

Requirements:

- fast startup;
- cancelable playback;
- sentence/chunk streaming if practical;
- provider abstraction.

### Later

Evaluate XTTS-v2 or another high-quality Polish-capable local TTS engine for a more distinctive assistant voice.

## Barge-in

### V1

- user can stop/cancel through overlay/hotkey;
- evaluate a reliable voice stop path;
- avoid full acoustic echo cancellation as a V1 blocker.

### V1.5+

- acoustic echo cancellation;
- detect user speech while assistant is talking;
- pause/stop TTS and return to LISTENING.

## Microphone privacy

- wake detection local only;
- no microphone recordings written to disk by default;
- explicit UI indicator for active capture;
- user can disable wake listening instantly from tray/settings;
- generated training recordings/datasets remain local and gitignored.

## Audio failure fallback

- wake engine fails -> fallback shortcut/text overlay remains usable;
- STT fails -> show error and allow text input;
- TTS fails -> return text response in overlay;
- microphone unavailable -> display clear device-selection path.

## Testing

Add automated/state tests and manual hardware tests for:

- wake -> listen transition;
- utterance end detection;
- Polish transcription accuracy;
- short/long utterances;
- silence timeout;
- follow-up mode;
- TTS cancellation;
- self-trigger behavior;
- device unplug/reconnect;
- noisy-room behavior;
- app restart while audio device is busy.
