import asyncio
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
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
from moj_asystent_core.stt import SttRuntimeProfile


class RecordingWhisperModel:
    def __init__(self) -> None:
        self.options: dict[str, Any] = {}

    def transcribe(self, _samples: object, **options: Any) -> tuple[Iterable[object], object]:
        self.options = options
        return (
            [
                SimpleNamespace(
                    start=0.0,
                    end=0.4,
                    text=" Dzień dobry ",
                    avg_logprob=-0.2,
                    no_speech_prob=0.1,
                )
            ],
            object(),
        )


class RecordingWhisperFactory:
    def __init__(self, model: RecordingWhisperModel) -> None:
        self.model = model
        self.model_name = ""
        self.options: dict[str, Any] = {}

    def __call__(self, model_name: str, **options: Any) -> RecordingWhisperModel:
        self.model_name = model_name
        self.options = options
        return self.model


class MissingCudaLibraryFactory:
    def __call__(self, model_name: str, **options: Any) -> RecordingWhisperModel:
        del model_name, options
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")


class LazyMissingCudaLibraryModel(RecordingWhisperModel):
    def transcribe(self, _samples: object, **options: Any) -> tuple[Iterable[object], object]:
        del _samples, options

        def segments() -> Iterator[object]:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            yield object()

        return segments(), object()


class BlockingWhisperModel(RecordingWhisperModel):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def transcribe(self, _samples: object, **options: Any) -> tuple[Iterator[object], object]:
        self.options = options

        def segments() -> Iterator[object]:
            self.started.set()
            self.release.wait(timeout=2)
            yield SimpleNamespace(
                start=0.0,
                end=0.4,
                text=" Anulowane ",
                avg_logprob=-0.2,
                no_speech_prob=0.1,
            )

        return segments(), object()


@pytest.mark.asyncio
async def test_faster_whisper_is_forced_to_polish_and_returns_segments() -> None:
    model = RecordingWhisperModel()
    factory = RecordingWhisperFactory(model)
    provider = FasterWhisperPolishProvider(
        SttRuntimeProfile(
            profile_id="preferred",
            model="large-v3-turbo",
            device="cuda",
            compute_type="int8_float16",
            beam_size=4,
            best_of=3,
        ),
        model_factory=factory,
    )
    provider.set_hotwords("GitHub, Qwen, PowerShell")
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
    assert model.options["beam_size"] == 4
    assert model.options["best_of"] == 3
    assert model.options["hotwords"] == "GitHub, Qwen, PowerShell"
    assert factory.model_name == "large-v3-turbo"
    assert factory.options == {
        "device": "cuda",
        "compute_type": "int8_float16",
        "local_files_only": True,
    }
    assert result.transcript == "Dzień dobry"
    assert result.segments[0].start_seconds == 0
    assert result.segments[0].no_speech_probability == 0.1
    assert result.confidence.level == "high"


@pytest.mark.asyncio
async def test_faster_whisper_cancellation_invalidates_in_flight_generation() -> None:
    model = BlockingWhisperModel()
    provider = FasterWhisperPolishProvider(
        SttRuntimeProfile(
            profile_id="fallback",
            model="medium",
            device="cpu",
            compute_type="int8",
        ),
        model_factory=RecordingWhisperFactory(model),
    )
    task = asyncio.create_task(
        provider.transcribe(
            SpeechToTextRequest(
                pcm_s16le=b"\0\0" * 1_600,
                sample_rate=16_000,
                language="pl",
                duration_ms=100,
            )
        )
    )
    await asyncio.to_thread(model.started.wait, 1)

    await provider.cancel()
    model.release.set()

    with pytest.raises(ProviderUnavailableError, match="cancelled"):
        await task


@pytest.mark.asyncio
async def test_cuda_load_failure_remains_visible_after_shallow_readiness_check() -> None:
    provider = FasterWhisperPolishProvider(
        SttRuntimeProfile(
            profile_id="preferred",
            model="large-v3",
            device="cuda",
            compute_type="int8_float16",
        ),
        model_factory=MissingCudaLibraryFactory(),
    )
    request = SpeechToTextRequest(
        pcm_s16le=b"\0\0" * 1_600,
        sample_rate=16_000,
        language="pl",
        duration_ms=100,
    )

    assert (await provider.status()).state == "ready"
    with pytest.raises(ProviderUnavailableError, match="could not be loaded"):
        await provider.transcribe(request)

    assert (await provider.status()).state == "cuda_unavailable"


@pytest.mark.asyncio
async def test_windows_cuda_preflight_reports_missing_runtime_libraries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "moj_asystent_core.audio_providers._windows_cuda_runtime_available",
        lambda: False,
    )
    monkeypatch.setattr("moj_asystent_core.audio_providers.sys.platform", "win32")
    provider = FasterWhisperPolishProvider(
        SttRuntimeProfile(
            profile_id="preferred",
            model="large-v3-turbo",
            device="cuda",
            compute_type="int8_float16",
        )
    )

    assert (await provider.status()).state == "cuda_unavailable"


@pytest.mark.asyncio
async def test_lazy_cuda_inference_failure_is_classified_and_remains_visible() -> None:
    provider = FasterWhisperPolishProvider(
        SttRuntimeProfile(
            profile_id="preferred",
            model="large-v3",
            device="cuda",
            compute_type="int8_float16",
        ),
        model_factory=RecordingWhisperFactory(LazyMissingCudaLibraryModel()),
    )
    request = SpeechToTextRequest(
        pcm_s16le=b"\0\0" * 1_600,
        sample_rate=16_000,
        language="pl",
        duration_ms=100,
    )

    with pytest.raises(ProviderUnavailableError, match="failed"):
        await provider.transcribe(request)

    assert (await provider.status()).state == "cuda_unavailable"


@pytest.mark.asyncio
async def test_transcription_does_not_persist_microphone_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    model = RecordingWhisperModel()
    provider = FasterWhisperPolishProvider(
        SttRuntimeProfile(
            profile_id="fallback",
            model="medium",
            device="cpu",
            compute_type="int8",
        ),
        model_factory=RecordingWhisperFactory(model),
    )

    await provider.transcribe(
        SpeechToTextRequest(
            pcm_s16le=b"\1\0" * 1_600,
            sample_rate=16_000,
            language="pl",
            duration_ms=100,
        )
    )

    assert list(tmp_path.iterdir()) == []


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
