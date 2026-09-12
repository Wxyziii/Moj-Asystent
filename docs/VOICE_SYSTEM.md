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

## Implemented audio architecture

The core owns a cancellable asyncio audio pipeline. `sounddevice` supplies bounded 16-bit mono PCM frames through a thread-safe ingress; openWakeWord runs only in idle, Silero VAD bounds utterances, faster-whisper is forced to `language="pl"`, and Piper plays a configured `pl_PL` ONNX voice outside the event loop. Milestone 5 routes the final Polish transcript through the local conversation provider, streams the full answer to the overlay and gives Piper a bounded two-sentence spoken variant.

Audio bytes and VAD buffers remain in RAM and are cleared after completion, cancellation, failure or shutdown. Provider work is tagged with a generation and operation UUID, so results finishing after cancellation cannot publish events or mutate the authoritative state. Milestone 13 also serializes inference inside each faster-whisper runtime and invalidates the provider generation on cancellation, preventing a new transcription from reactivating stale work. Wake processing is suppressed during TTS. Microphone failures retry after a bounded delay, while the shortcut and text overlay remain available.

Configuration uses `MOJ_ASYSTENT_MICROPHONE_DEVICE`, `MOJ_ASYSTENT_SAMPLE_RATE`, `MOJ_ASYSTENT_WAKE_SENSITIVITY`, `MOJ_ASYSTENT_VAD_START_THRESHOLD`, `MOJ_ASYSTENT_VAD_END_THRESHOLD`, `MOJ_ASYSTENT_VAD_PRE_ROLL_MS`, `MOJ_ASYSTENT_VAD_POST_ROLL_MS`, `MOJ_ASYSTENT_VAD_TRAILING_SILENCE_MS`, `MOJ_ASYSTENT_MAX_UTTERANCE_SECONDS`, `MOJ_ASYSTENT_STT_MODEL`, `MOJ_ASYSTENT_STT_FALLBACK_DEVICE`, `MOJ_ASYSTENT_STT_FALLBACK_COMPUTE_TYPE`, `MOJ_ASYSTENT_STT_PREFERRED_ENABLED`, `MOJ_ASYSTENT_STT_PREFERRED_MODEL`, `MOJ_ASYSTENT_STT_PREFERRED_DEVICE`, `MOJ_ASYSTENT_STT_PREFERRED_COMPUTE_TYPE`, the bounded `MOJ_ASYSTENT_STT_*` decoding parameters, `MOJ_ASYSTENT_TTS_VOICE_PATH`, `MOJ_ASYSTENT_WAKE_MODEL_PATH`, `MOJ_ASYSTENT_FOLLOW_UP_SECONDS` and `MOJ_ASYSTENT_VOICE_RESPONSES`. If installed, the default Polish Piper voice is discovered under `%LOCALAPPDATA%/Moj-Asystent/models/piper/pl_PL-gosia-medium.onnx`; model downloads are explicit setup actions and remain outside the repository.

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

Milestone 13 uses a deterministic ordered selector behind the existing STT
provider abstraction:

1. preferred `large-v3-turbo`, CUDA, `int8_float16`;
2. configured local fallback (`medium`, CPU, `int8` by default);
3. an explicit `medium` CPU `int8` compatibility profile when the configured
   fallback differs.

Every profile forces `language="pl"`, uses bounded decoding parameters and
disables Whisper's internal VAD because Silero owns the speech boundary. The
selector checks device/compute compatibility, required Windows CUDA 12/cuDNN 9
libraries and local model availability, then reports the actual active profile
and explains fallback. Lazy CUDA failures during segment iteration are also
classified and retained instead of reverting to a false `ready` state. Model
construction uses `local_files_only`; missing large models are never downloaded
implicitly.

The preferred CUDA profile is allowed to share the GPU with the model router,
but runtime selection is independent and deterministic. Resource contention and
quality must be measured on the target machine using the local benchmark rather
than inferred from model size.

## Polish vocabulary hints

The core starts with a reviewable technical vocabulary such as:

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

The authenticated Settings → Voice view can add or remove at most 32 normalized,
64-character entries. The list is persisted locally in SQLite and passed to
faster-whisper through its `hotwords` input. It is a decoder hint, not a
post-transcription replacement rule: ordinary Polish words are never rewritten
solely because a similar vocabulary entry exists. No LLM correction pass is run.

## VAD

Silero VAD determines speech boundaries. The pipeline retains a bounded 240 ms
pre-roll by default and keeps up to 240 ms of detected trailing audio after the
speech endpoint. These values are validated, memory-only and included in the
maximum utterance buffer calculation, protecting short beginnings/endings
without allowing unbounded rolling capture.

Parameters should be configurable and calibrated for:

- microphone noise floor;
- typical speaking distance;
- room noise;
- keyboard/fan noise.

## Confidence and diagnostics

Whisper segment signals are not treated as calibrated probabilities. The core
derives only a conservative `high` / `medium` / `low` / `unknown` indicator from
average log probability and no-speech probability. A clearly low-confidence
voice request cannot use an existing persistent approval to run `write.safe`;
it must receive a one-time confirmation. Sensitive tools still require their
normal exact confirmation, read-only tools are unchanged, and ToolEngine remains
the sole authorization authority.

The selector keeps only the latest 64 diagnostic records in RAM. Records contain
duration, VAD boundaries and padding, latency/RTF, public model name,
device/compute type, aggregate segment signals, confidence, outcome and a
classified fallback/failure reason. They contain neither PCM nor transcript text
and are exposed only through the authenticated loopback API.

## Benchmark

`benchmarks/stt/` documents the opt-in local corpus format and the
`moj-asystent-stt-benchmark` command. It compares Medium CPU `int8` with Turbo
CUDA `int8_float16` and `float16`, producing per-sample transcription/WER plus
aggregate latency, RTF, failure and memory measurements. Audio, local manifests
and result files are gitignored because reports can contain private speech text;
the harness validates relative PCM16 mono WAV paths and never downloads models.

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
