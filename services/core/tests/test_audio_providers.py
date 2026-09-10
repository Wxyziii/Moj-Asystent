from types import SimpleNamespace
from typing import Any

import pytest

from moj_asystent_core.audio import PcmFrame
from moj_asystent_core.audio_providers import (
    FasterWhisperPolishProvider,
    OpenWakeWordProvider,
    PiperPolishProvider,
    calibrate_pcm,
)
from moj_asystent_core.providers import (
    ProviderUnavailableError,
    SpeechToTextRequest,
    TextToSpeechRequest,
)


class RecordingWhisperModel:
    def __init__(self) -> None:
        self.options: dict[str, Any] = {}

    def transcribe(self, _samples: object, **options: Any) -> tuple[list[object], object]:
        self.options = options
        return (
            [SimpleNamespace(start=0.0, end=0.4, text=" Dzień dobry ", avg_logprob=-0.2)],
            object(),
        )


@pytest.mark.asyncio
async def test_faster_whisper_is_forced_to_polish_and_returns_segments() -> None:
    model = RecordingWhisperModel()
    provider = FasterWhisperPolishProvider("small", model_factory=lambda *_args, **_kwargs: model)
    result = await provider.transcribe(
        SpeechToTextRequest(
            pcm_s16le=b"\0\0" * 1_600,
            sample_rate=16_000,
            language="pl",
            duration_ms=100,
        )
    )
    assert model.options["language"] == "pl"
    assert model.options["vad_filter"] is False
    assert result.transcript == "Dzień dobry"
    assert result.segments[0].start_seconds == 0


@pytest.mark.asyncio
async def test_real_audio_adapters_fail_closed_on_incompatible_or_missing_models() -> None:
    with pytest.raises(ProviderUnavailableError, match="16 kHz"):
        await OpenWakeWordProvider().score(PcmFrame(pcm_s16le=b"\0\0" * 800, sample_rate=8_000))
    with pytest.raises(ProviderUnavailableError, match="voice model"):
        await PiperPolishProvider(None).speak(
            TextToSpeechRequest(text="Dzień dobry", language="pl")
        )


def test_microphone_calibration_is_metadata_only() -> None:
    result = calibrate_pcm([PcmFrame(pcm_s16le=b"\0\0" * 160, sample_rate=16_000)])
    assert result == {"rms": 0.0, "peak": 0.0, "clipping_ratio": 0.0}
