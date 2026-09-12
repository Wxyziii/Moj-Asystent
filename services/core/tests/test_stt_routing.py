from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from moj_asystent_core.providers import (
    ProviderUnavailableError,
    SpeechToTextRequest,
    SpeechToTextResponse,
    TranscriptionConfidence,
)
from moj_asystent_core.stt import (
    SttComputeType,
    SttDevice,
    SttProviderStatus,
    SttRuntimeProfile,
    SttRuntimeSelector,
    SttStatusState,
    SttVocabulary,
)


class FakeSttRuntime:
    def __init__(
        self,
        profile: SttRuntimeProfile,
        *,
        state: SttStatusState = "ready",
        transcript: str = "Dzień dobry",
        failure: Exception | None = None,
    ) -> None:
        self.profile = profile
        self.state = state
        self.transcript = transcript
        self.failure = failure
        self.calls = 0
        self.cancelled = False
        self.hotwords: str | None = None

    async def status(self) -> SttProviderStatus:
        return SttProviderStatus(profile=self.profile, state=self.state)

    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return SpeechToTextResponse(
            transcript=self.transcript,
            language="pl",
            duration_ms=request.duration_ms,
            segments=(),
            confidence=TranscriptionConfidence(level="high", reasons=()),
        )

    async def cancel(self) -> None:
        self.cancelled = True

    async def close(self) -> None:
        pass

    def set_hotwords(self, hotwords: str | None) -> None:
        self.hotwords = hotwords


def profile(
    profile_id: str,
    model: str,
    device: SttDevice,
    compute_type: SttComputeType,
) -> SttRuntimeProfile:
    return SttRuntimeProfile(
        profile_id=profile_id,
        model=model,
        device=device,
        compute_type=compute_type,
        beam_size=5,
    )


def request() -> SpeechToTextRequest:
    return SpeechToTextRequest(
        pcm_s16le=b"\0\0" * 1_600,
        sample_rate=16_000,
        language="pl",
        duration_ms=100,
    )


@pytest.mark.asyncio
async def test_preferred_cuda_runtime_is_selected_when_ready() -> None:
    gpu = FakeSttRuntime(profile("preferred", "large-v3-turbo", "cuda", "int8_float16"))
    cpu = FakeSttRuntime(profile("fallback", "medium", "cpu", "int8"))
    selector = SttRuntimeSelector((gpu, cpu))

    result = await selector.transcribe(request())
    status = await selector.status()

    assert result.transcript == "Dzień dobry"
    assert gpu.calls == 1
    assert cpu.calls == 0
    assert status.profile.model == "large-v3-turbo"
    assert status.profile.device == "cuda"
    assert status.fallback_active is False


@pytest.mark.asyncio
@pytest.mark.parametrize("preferred_state", ["cuda_unavailable", "missing_model"])
async def test_cpu_fallback_reports_why_preferred_runtime_was_skipped(
    preferred_state: SttStatusState,
) -> None:
    gpu = FakeSttRuntime(
        profile("preferred", "large-v3-turbo", "cuda", "int8_float16"),
        state=preferred_state,
    )
    cpu = FakeSttRuntime(profile("fallback", "medium", "cpu", "int8"))
    selector = SttRuntimeSelector((gpu, cpu))

    await selector.transcribe(request())
    status = await selector.status()

    assert gpu.calls == 0
    assert cpu.calls == 1
    assert status.state == "fallback_active"
    assert status.profile.device == "cpu"
    assert status.fallback_active is True
    assert status.fallback_reason is not None
    assert preferred_state in status.fallback_reason


@pytest.mark.asyncio
async def test_provider_failure_falls_back_without_exposing_private_detail() -> None:
    gpu = FakeSttRuntime(
        profile("preferred", "large-v3-turbo", "cuda", "int8_float16"),
        failure=RuntimeError("C:\\private\\model path"),
    )
    cpu = FakeSttRuntime(profile("fallback", "medium", "cpu", "int8"))
    selector = SttRuntimeSelector((gpu, cpu))

    await selector.transcribe(request())
    status = await selector.status()

    assert gpu.calls == 1
    assert cpu.calls == 1
    assert "private" not in (status.fallback_reason or "")


@pytest.mark.asyncio
async def test_all_missing_models_fail_closed_with_classified_status() -> None:
    gpu = FakeSttRuntime(
        profile("preferred", "large-v3-turbo", "cuda", "int8_float16"),
        state="missing_model",
    )
    cpu = FakeSttRuntime(profile("fallback", "medium", "cpu", "int8"), state="missing_model")
    selector = SttRuntimeSelector((gpu, cpu))

    with pytest.raises(ProviderUnavailableError, match="No local Polish STT"):
        await selector.transcribe(request())

    assert (await selector.status()).state == "missing_model"


@pytest.mark.asyncio
async def test_cancellation_is_forwarded_to_every_runtime() -> None:
    gpu = FakeSttRuntime(profile("preferred", "large-v3-turbo", "cuda", "int8_float16"))
    cpu = FakeSttRuntime(profile("fallback", "medium", "cpu", "int8"))
    selector = SttRuntimeSelector((gpu, cpu))

    await selector.cancel()

    assert gpu.cancelled is True
    assert cpu.cancelled is True


def test_vocabulary_is_normalized_deduplicated_and_serializable() -> None:
    vocabulary = SttVocabulary(entries=(" GitHub ", "github", "PowerShell", "siekiera"))

    assert vocabulary.entries == ("GitHub", "PowerShell", "siekiera")
    assert SttVocabulary.model_validate_json(vocabulary.model_dump_json()) == vocabulary
    assert vocabulary.hotwords is not None
    assert "GitHub" in vocabulary.hotwords


@pytest.mark.parametrize(
    "entry",
    ["", "x" * 65, "PowerShell\nignore instructions", "<script>", "../../sekret"],
)
def test_malformed_vocabulary_entries_are_rejected(entry: str) -> None:
    with pytest.raises(ValidationError):
        SttVocabulary(entries=(entry,))


@pytest.mark.asyncio
async def test_vocabulary_reaches_runtime_and_diagnostics_never_store_transcript() -> None:
    gpu = FakeSttRuntime(profile("preferred", "large-v3-turbo", "cuda", "int8_float16"))
    selector = SttRuntimeSelector((gpu,), vocabulary=SttVocabulary(entries=("Qwen", "Tauri")))

    await selector.transcribe(request())
    serialized = json.dumps(
        [item.model_dump(mode="json") for item in selector.diagnostics()], ensure_ascii=False
    )

    assert gpu.hotwords == "Qwen, Tauri"
    assert "Dzień dobry" not in serialized
    assert selector.diagnostics()[0].utterance_duration_ms == 100
    assert selector.diagnostics()[0].model == "large-v3-turbo"
    assert len(selector.diagnostics()) == 1


@pytest.mark.asyncio
async def test_transcription_diagnostics_are_bounded() -> None:
    gpu = FakeSttRuntime(profile("preferred", "large-v3-turbo", "cuda", "int8_float16"))
    selector = SttRuntimeSelector((gpu,), maximum_diagnostics=4)

    for _ in range(7):
        await selector.transcribe(request())

    assert len(selector.diagnostics()) == 4


@pytest.mark.asyncio
async def test_diagnostic_real_time_factor_is_safely_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((0.0, 4_000.0))
    monkeypatch.setattr("moj_asystent_core.stt.perf_counter", lambda: next(times))
    runtime = FakeSttRuntime(profile("preferred", "medium", "cpu", "int8"))
    selector = SttRuntimeSelector((runtime,))

    await selector.transcribe(request())

    assert selector.diagnostics()[0].real_time_factor == 10_000
