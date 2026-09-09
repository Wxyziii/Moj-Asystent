# Custom Wake-Word Training

## Goal

The assistant must not have a hardcoded public name. During onboarding, the user chooses a name and trains the app to recognize that exact wake phrase reliably in their own environment.

## Design principles

- Guided, not technical.
- Every recording prompt tells the user exactly what to say and how to say it.
- Use real user recordings plus augmentation/synthetic variations.
- Collect positive and negative/confusable examples.
- Validate the finished wake model before onboarding completes.
- Allow rename/retrain later.
- Keep all recordings/models local unless the user explicitly exports them.

## Onboarding flow

```text
Welcome
 -> choose assistant name
 -> name suitability check
 -> select microphone
 -> microphone calibration
 -> guided positive recordings
 -> natural-command recordings
 -> hard-negative recordings
 -> ordinary-speech negative sample
 -> train/build wake model
 -> validation test
 -> sensitivity adjustment if needed
 -> complete
```

## 1. Name entry

Screen copy example:

```text
Jak chcesz nazwać swojego asystenta?

[Kairo]
```

Store the chosen display name separately from internal product names.

## 2. Suitability scoring

Before training, evaluate the name for wake-word quality.

Consider:

- length;
- phonetic distinctiveness in Polish;
- similarity to common Polish words;
- similarity to common commands/app names;
- likely false-positive rate;
- whether STT/TTS can represent it reliably.

UI should show a simple score such as:

```text
Rozpoznawalność:       bardzo dobra
Ryzyko fałszywych aktywacji: niskie
```

Warn, but allow override for poor names.

## 3. Microphone calibration

Ask the user to speak normally for a few seconds.

Measure:

- ambient noise floor;
- speech RMS/peak;
- clipping;
- signal-to-noise estimate;
- device sample-rate/channel compatibility.

Use this to tune recording guidance and initial detection threshold.

## 4. Guided positive samples

Target roughly 16–20 useful recordings initially. The exact count can change after testing.

Do not show only `Nagranie 7/16`. Show the required manner too.

### A. Normal voice

3 samples.

Instruction:

```text
POWIEDZ: "Kairo"
JAK: normalnym głosem, tak jak zwykle przy komputerze.
ODLEGŁOŚĆ: około 50–80 cm od mikrofonu.
```

### B. Slightly quieter

2 samples.

Instruction:

```text
Powiedz nazwę trochę ciszej niż zwykle.
Nie szepcz i nie przeciągaj słowa.
```

### C. Slightly louder

2 samples.

Instruction:

```text
Powiedz nazwę trochę głośniej, jakby w tle grała muzyka.
Nie krzycz.
```

### D. Intonation variation

3 samples:

- neutral;
- question-like;
- urgent/attention-getting.

Instruction explicitly explains the tone instead of expecting the user to invent it.

### E. Natural command starts

Examples generated with the chosen name:

- `{name}, otwórz Spotify.`
- `{name}, sprawdź temperaturę.`
- `{name}, co się tutaj dzieje?`

The model should focus on wake phrase recognition while receiving realistic following speech.

### F. Position variation

2–3 samples.

Ask user to:

- lean back slightly;
- turn head slightly;
- remain in a realistic computer-use posture.

Do not ask for extreme positions that will not occur naturally.

### G. Real background conditions

2 samples with the user's normal fan/keyboard/game/music environment if available.

## 5. Sample validation

After each recording, automatically check:

- speech present;
- expected phrase recognized strongly enough;
- not too quiet;
- no clipping;
- duration plausible;
- signal quality acceptable.

UI:

```text
✓ Głos wykryty
✓ Odpowiednia głośność
✓ Brak przesterowania
✓ Fraza rozpoznana
```

Reject poor samples and explain exactly what to change.

## 6. Hard negatives / confusable Polish phrases

Generate phrases that sound similar to the selected name but must not trigger.

Example for a hypothetical name `Nova` might include Polish words/phrases around `nowa`, `od nowa`, etc.

The exact negative set must be generated from the chosen name, not from a fixed list.

Prompt example:

```text
Teraz nauczymy asystenta, czego NIE traktować jako swojego imienia.

Powiedz normalnie:
"To jest nowa wersja programu."
```

Label these as negative/non-wake training data.

## 7. Ordinary-speech negative sample

Ask user to read ~20–30 seconds of mundane Polish text that does not contain the assistant name.

Purpose:

- estimate false activation against the user's normal voice;
- capture realistic speech acoustics;
- optionally support future speaker-filter calibration.

Do not treat speaker recognition as authorization/security.

## 8. Training data composition

Conceptual dataset:

```text
real positive samples from user
        +
synthetic Polish pronunciation/intonation variations
        +
room/noise/reverb augmentation
        +
real and generated hard negatives
        +
ordinary Polish speech negatives
        ->
custom wake model
```

Prefer openWakeWord-compatible training/export where practical.

## 9. Validation

After training, enter actual wake-only mode.

### Positive test

Ask the user to activate naturally 5+ times without pressing anything.

Measure:

- successful detections;
- confidence distribution;
- latency.

### Negative test

Ask user to speak normally for a short period without saying the assistant name.

Measure accidental activations.

If results are poor:

- adjust threshold;
- ask for targeted additional samples;
- identify which pronunciation/noise condition failed;
- retrain.

Do not simply expose a mysterious confidence slider as the only fix.

## 10. Final configuration

Example persisted metadata:

```json
{
  "assistant_name": "Kairo",
  "language": "pl-PL",
  "wake_model_path": "models/wakewords/kairo.onnx",
  "wake_threshold": 0.72,
  "follow_up_timeout_seconds": 20
}
```

Wake model filenames must be sanitized and should use internal IDs rather than assuming display names are valid filesystem names.

## 11. Rename / retrain

Settings must offer:

- change name;
- retrain current name;
- recalibrate microphone;
- adjust sensitivity;
- re-run validation;
- switch microphone.

Changing the assistant name swaps the wake model; it does not require rebuilding the rest of the application.

## 12. Privacy/storage

Gitignore and never commit:

- raw user recordings;
- generated synthetic training audio;
- local wake datasets;
- personally trained wake models unless deliberately exported;
- calibration data that may contain voice recordings.

By default, delete intermediate audio after the local model artifact and evaluation metrics are produced, subject to an optional local `keep training samples` setting.
