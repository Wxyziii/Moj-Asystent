import asyncio
import time
from pathlib import Path

import numpy as np
import pytest

from moj_asystent_core.audio import PcmFrame
from moj_asystent_core.audio_providers import OpenWakeWordProvider
from moj_asystent_core.wakeword import (
    SampleKind,
    WakeModelMetadata,
    WakeModelStore,
    analyze_name,
    analyze_sample,
    build_curriculum,
    calculate_validation,
    generate_confusable_phrases,
    normalize_assistant_name,
    select_sensitivity,
)
from moj_asystent_core.wakeword_training import WakeOnboardingService


class FakeTrainer:
    backend_version = "fake-1"

    def __init__(self, *, fail: bool = False, wait: bool = False) -> None:
        self.fail = fail
        self.wait = wait

    def train(self, directory, curriculum, output, cancellation, progress, seed):
        del curriculum, seed
        progress(40, "Trenuję")
        while self.wait:
            cancellation.raise_if_cancelled()
            time.sleep(0.001)
        if self.fail:
            (directory / "candidate.partial").write_bytes(b"partial")
            raise RuntimeError("failed")
        output.write_bytes(b"valid-onnx")


async def no_runtime_change(_metadata: WakeModelMetadata) -> None:
    return None


def pcm(*, amplitude: float, seconds: float = 1.2, sample_rate: int = 16_000) -> bytes:
    timeline = np.arange(round(seconds * sample_rate), dtype=np.float64) / sample_rate
    samples = np.sin(2 * np.pi * 180 * timeline) * amplitude * 32_767
    return samples.astype("<i2").tobytes()


def test_name_normalization_preserves_display_and_scores_risk() -> None:
    name = normalize_assistant_name("  Ż   Orion  ")
    assert name.display == "Ż Orion"
    assert name.normalized == "ż orion"
    assert analyze_name("Lena").rating in {"słaba", "dobra"}
    assert analyze_name("Żorina").rating == "bardzo dobra"
    assert analyze_name("A").trainable is False


def test_confusables_and_curriculum_are_name_specific_and_fully_guided() -> None:
    phrases = generate_confusable_phrases("Nora")
    assert 5 <= len(phrases) <= 10
    assert all("nora" not in phrase.casefold() for phrase in phrases)
    steps = build_curriculum("Nora")
    assert any(step.kind is SampleKind.NATURAL_COMMAND for step in steps)
    assert any("Nora, otwórz Spotify" in step.phrase for step in steps)
    assert any(step.kind is SampleKind.ORDINARY_SPEECH for step in steps)
    for step in steps:
        assert step.phrase and step.loudness and step.distance and step.intonation
        assert step.guidance and step.avoid


def test_sample_quality_accepts_voice_and_rejects_quiet_clipped_and_silent() -> None:
    accepted = analyze_sample(pcm(amplitude=0.18), 16_000, expected_seconds=(0.5, 2.5))
    assert accepted.accepted
    assert accepted.speech_detected and accepted.volume_ok and accepted.no_clipping

    assert (
        "Za cicho"
        in analyze_sample(pcm(amplitude=0.002), 16_000, expected_seconds=(0.5, 2.5)).reason
    )
    assert (
        "przesterowany"
        in analyze_sample(
            (np.ones(16_000, dtype="<i2") * 32_767).tobytes(),
            16_000,
            expected_seconds=(0.5, 2.5),
        ).reason
    )
    assert not analyze_sample(b"\0\0" * 16_000, 16_000, expected_seconds=(0.5, 2.5)).accepted


def test_sample_quality_ignores_recorder_padding_after_a_short_wake_name() -> None:
    speech = pcm(amplitude=0.18, seconds=0.55, sample_rate=16_000)
    padded = speech + b"\0\0" * round(1.85 * 16_000)

    quality = analyze_sample(padded, 16_000, expected_seconds=(0.45, 3.5))

    assert quality.accepted, quality.reason
    assert quality.silence_ratio < 0.2


def test_validation_metrics_and_sensitivity_use_held_out_runtime_scores() -> None:
    threshold = select_sensitivity([0.82, 0.75, 0.91, 0.69, 0.88], [0.1, 0.18, 0.22])
    result = calculate_validation(
        [0.82, 0.75, 0.91, 0.69, 0.88],
        [0.1, 0.18, 0.22],
        threshold=threshold,
        negative_duration_seconds=120,
    )
    assert result.successful_activations >= 4
    assert result.false_accepts == 0
    assert result.passed
    assert result.false_accepts_per_hour == 0


def test_store_atomically_activates_only_a_validated_candidate(tmp_path: Path) -> None:
    store = WakeModelStore(tmp_path)
    previous = store.models / "previous.onnx"
    previous.write_bytes(b"old")
    store.write_active_for_test(
        WakeModelMetadata.for_test("Mira", previous, validated=True, sensitivity=0.55)
    )
    candidate = tmp_path / "candidate.onnx"
    candidate.write_bytes(b"new")

    with pytest.raises(ValueError):
        store.activate(WakeModelMetadata.for_test("Nora", candidate, validated=False))
    assert store.load_active().assistant_name == "Mira"
    assert previous.read_bytes() == b"old"

    activated = store.activate(
        WakeModelMetadata.for_test("Nora", candidate, validated=True, sensitivity=0.61)
    )
    assert store.load_active() == activated
    assert Path(activated.model_path).read_bytes() == b"new"
    assert previous.read_bytes() == b"old"


@pytest.mark.asyncio
async def test_cancelled_training_cleans_partial_output(tmp_path: Path) -> None:
    from moj_asystent_core.wakeword import CancellationToken

    token = CancellationToken()
    token.cancel()
    partial = tmp_path / "job" / "partial.onnx"
    partial.parent.mkdir()
    partial.write_bytes(b"partial")

    with pytest.raises(asyncio.CancelledError):
        token.raise_if_cancelled()
    partial.unlink(missing_ok=True)
    assert not partial.exists()


def complete_samples(service: WakeOnboardingService, session_id) -> None:
    session = service.get(session_id)
    service.calibrate(session_id, PcmFrame(pcm(amplitude=0.08), 16_000))
    for step in session.curriculum:
        seconds = max(step.expected_seconds[0] + 0.1, 1.2)
        result = service.add_sample(
            session_id,
            step.id,
            PcmFrame(pcm(amplitude=0.18, seconds=seconds), 16_000),
        )
        assert result.accepted, (step.id, result.reason)


@pytest.mark.asyncio
async def test_failed_training_cleans_output_and_preserves_active_model(tmp_path: Path) -> None:
    store = WakeModelStore(tmp_path)
    old = store.models / "old.onnx"
    old.write_bytes(b"old")
    store.write_active_for_test(WakeModelMetadata.for_test("Mira", old, validated=True))
    service = WakeOnboardingService(store, FakeTrainer(fail=True), no_runtime_change)
    session = service.begin("Nora", None, False)
    complete_samples(service, session.session_id)

    job = service.start_training(session.session_id)
    while service.job(job.job_id).status == "running":
        await asyncio.sleep(0.001)

    assert service.job(job.job_id).status == "failed"
    assert not list((store.sessions / str(session.session_id)).glob("*.partial"))
    assert store.load_active().assistant_name == "Mira"


@pytest.mark.asyncio
async def test_validation_normalizes_browser_sample_rate_before_wake_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WakeModelStore(tmp_path)
    service = WakeOnboardingService(store, FakeTrainer(), no_runtime_change)
    session = service.begin("Nora", None, False)
    internal = service._sessions[session.session_id]  # noqa: SLF001
    internal.candidate_path.write_bytes(b"candidate")
    internal.view = internal.view.model_copy(update={"candidate_ready": True})
    seen_rates: list[int] = []

    async def score_clip(_provider: object, frame: PcmFrame) -> float:
        seen_rates.append(frame.sample_rate)
        return 0.8

    monkeypatch.setattr(OpenWakeWordProvider, "score_clip", score_clip)
    await service.validate_sample(
        session.session_id,
        PcmFrame(pcm(amplitude=0.18, seconds=1.0, sample_rate=48_000), 48_000),
        positive=True,
    )

    assert seen_rates == [16_000]


def test_training_reconciles_existing_samples_and_reports_only_missing_steps(
    tmp_path: Path,
) -> None:
    store = WakeModelStore(tmp_path)
    service = WakeOnboardingService(store, FakeTrainer(), no_runtime_change)
    session = service.begin("Nora", None, False)
    service.calibrate(session.session_id, PcmFrame(pcm(amplitude=0.08), 16_000))
    service.add_sample(
        session.session_id,
        "wake-1",
        PcmFrame(pcm(amplitude=0.18, seconds=1.2), 16_000),
    )
    # Simulate a UI/core view that lost its in-memory accepted list while the
    # durable sample remains on disk.
    internal = service._sessions[session.session_id]  # noqa: SLF001
    internal.view = internal.view.model_copy(update={"accepted_step_ids": ()})

    with pytest.raises(ValueError, match="wake-2"):
        service.start_training(session.session_id)

    assert "wake-1" in service.get(session.session_id).accepted_step_ids


@pytest.mark.asyncio
async def test_training_is_cancellable_and_late_result_is_suppressed(tmp_path: Path) -> None:
    service = WakeOnboardingService(
        WakeModelStore(tmp_path), FakeTrainer(wait=True), no_runtime_change
    )
    session = service.begin("Żorina", "mikrofon-1", False)
    complete_samples(service, session.session_id)
    job = service.start_training(session.session_id)

    cancelled = await service.cancel_training(job.job_id)

    assert cancelled.status == "cancelled"
    assert not service.get(session.session_id).candidate_ready
    assert not (service.store.sessions / str(session.session_id) / "candidate.onnx").exists()
