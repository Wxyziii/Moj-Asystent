from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from moj_asystent_core.providers import (
    SpeechToTextRequest,
    SpeechToTextResponse,
    TranscriptionConfidence,
)
from moj_asystent_core.stt import SttProviderStatus, SttRuntimeProfile
from moj_asystent_core.stt_benchmark import (
    BenchmarkManifestError,
    normalize_polish_transcript,
    run_benchmark,
    word_error_rate,
)


class FakeRuntime:
    def __init__(self, profile: SttRuntimeProfile, transcript: str) -> None:
        self.profile = profile
        self.transcript = transcript

    async def status(self) -> SttProviderStatus:
        return SttProviderStatus(profile=self.profile, state="ready")

    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        return SpeechToTextResponse(
            transcript=self.transcript,
            language="pl",
            duration_ms=request.duration_ms,
            segments=(),
            confidence=TranscriptionConfidence(level="high"),
        )

    async def cancel(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def set_hotwords(self, hotwords: str | None) -> None:
        del hotwords


def write_wav(path: Path, *, duration_ms: int = 100) -> None:
    with wave.open(str(path), "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16_000)
        recording.writeframes(b"\0\0" * (duration_ms * 16))


def profile() -> SttRuntimeProfile:
    return SttRuntimeProfile(
        profile_id="medium_cpu_int8",
        model="medium",
        device="cpu",
        compute_type="int8",
    )


def test_polish_wer_normalization_is_deterministic() -> None:
    assert normalize_polish_transcript("  Żółć, GitHub! ") == ("żółć", "github")
    assert word_error_rate("Włącz Steam", "włącz  steam!") == 0
    assert word_error_rate("otwórz github", "otwórz git hub") == pytest.approx(2 / 2)


@pytest.mark.asyncio
async def test_benchmark_reports_aggregate_and_reviewable_transcription(
    tmp_path: Path,
) -> None:
    write_wav(tmp_path / "sample.wav", duration_ms=100)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "samples": [
                    {"audio": "sample.wav", "reference": "włącz Steam"},
                ],
            }
        ),
        encoding="utf-8",
    )

    report = await run_benchmark(
        manifest,
        profiles=(profile(),),
        runtime_factory=lambda configured: FakeRuntime(configured, "Włącz Steam"),
    )

    assert report.sample_count == 1
    assert report.profiles[0].status == "completed"
    assert report.profiles[0].word_error_rate == 0
    assert report.profiles[0].audio_duration_ms == 100
    assert report.profiles[0].samples[0].reference == "włącz Steam"
    assert report.profiles[0].samples[0].transcription == "Włącz Steam"
    encoded = report.model_dump_json()
    assert "sample.wav" not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize("audio", ["../outside.wav", "C:/private/sample.wav"])
async def test_manifest_rejects_audio_outside_its_directory(tmp_path: Path, audio: str) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"version": 1, "samples": [{"audio": audio, "reference": "test"}]}),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkManifestError, match="względ"):
        await run_benchmark(
            manifest,
            profiles=(profile(),),
            runtime_factory=lambda configured: FakeRuntime(configured, "test"),
        )


@pytest.mark.asyncio
async def test_manifest_rejects_non_pcm_or_stereo_wav(tmp_path: Path) -> None:
    audio = tmp_path / "stereo.wav"
    with wave.open(str(audio), "wb") as recording:
        recording.setnchannels(2)
        recording.setsampwidth(2)
        recording.setframerate(16_000)
        recording.writeframes(b"\0\0\0\0" * 1_600)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"version": 1, "samples": [{"audio": "stereo.wav", "reference": "test"}]}),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkManifestError, match="mono"):
        await run_benchmark(
            manifest,
            profiles=(profile(),),
            runtime_factory=lambda configured: FakeRuntime(configured, "test"),
        )
