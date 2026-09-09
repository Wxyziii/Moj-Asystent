---
name: custom-wakeword-onboarding
description: Build or refine Moj-Asystent's custom assistant-name onboarding and wake-word training flow, including suitability scoring, microphone calibration, guided recordings, Polish confusable negatives, training, validation, sensitivity, and retraining. Use for Phase 4 custom-name/wake-word work.
---

# Custom Wake-Word Onboarding

Read `docs/WAKE_WORD_TRAINING.md` and Phase 4 of `docs/IMPLEMENTATION_PLAN.md` before editing.

The assistant's name is user-configured. Never hardcode `Jarvis` or another product wake name into normal runtime behavior.

## UX contract

Every requested recording must tell the user:
- the exact phrase to say;
- target loudness;
- approximate microphone distance;
- intended tone/intonation;
- whether it is a positive or negative example.

Do not present a vague `record N samples` workflow.

## Training workflow

1. Validate/name-score the chosen phrase and warn about overly common or acoustically ambiguous choices without blocking reasonable user intent.
2. Calibrate microphone/background level first.
3. Collect varied positive examples: normal, quieter, louder, changed intonation, realistic command starts, and modest position/distance variation.
4. Generate Polish hard-negative/confusable phrases for the chosen name and collect/select negative examples.
5. Keep training data local. Generated datasets and raw recordings must remain ignored by Git.
6. Train through a replaceable wake-word provider/pipeline rather than coupling onboarding to one model implementation.
7. Validate with real held-out user samples and a false-activation test. Track recall and false accepts rather than training accuracy alone.
8. Expose sensitivity and rename/retrain flows. Replacing the wake model must not change unrelated assistant configuration.

## Verification

Test onboarding state progression, interrupted/retried recordings, invalid/quiet/clipped samples, arbitrary Unicode/Polish-friendly names, persistence of only intended metadata/model output, and failure recovery when training cannot complete.