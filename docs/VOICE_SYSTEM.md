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

- faster-whisper;
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
