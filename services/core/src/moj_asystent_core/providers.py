"""Replaceable provider interfaces for local assistant engines."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .llm import (
    LanguageModelProvider,
    LanguageModelRequest,
    ModelStatus,
    ModelStreamEvent,
    ModelTextDelta,
    ProviderUnavailableError,
)

if TYPE_CHECKING:
    from .audio import PcmFrame

__all__ = [
    "LanguageModelProvider",
    "LanguageModelRequest",
    "ProviderUnavailableError",
]


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


TranscriptionConfidenceLevel = Literal["high", "medium", "low", "unknown"]


class SpeechToTextRequest(ProviderModel):
    pcm_s16le: bytes
    sample_rate: int = Field(ge=8_000, le=48_000)
    language: Literal["pl"]
    duration_ms: int = Field(gt=0, le=120_000)
    vad_speech_start_ms: int = Field(default=0, ge=0, le=120_000)
    vad_speech_end_ms: int | None = Field(default=None, ge=0, le=120_000)
    pre_roll_ms: int = Field(default=0, ge=0, le=2_000)
    post_roll_ms: int = Field(default=0, ge=0, le=1_500)


class TranscriptionSegment(ProviderModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=4_096)
    average_log_probability: float | None = None
    no_speech_probability: float | None = Field(default=None, ge=0, le=1)


class TranscriptionConfidence(ProviderModel):
    """A conservative quality signal, not a calibrated probability."""

    level: TranscriptionConfidenceLevel = "unknown"
    reasons: tuple[Annotated[str, Field(min_length=1, max_length=96)], ...] = Field(
        default=(), max_length=4
    )


class SpeechToTextResponse(ProviderModel):
    transcript: str = Field(min_length=1, max_length=8_192)
    language: Literal["pl"]
    duration_ms: int = Field(gt=0, le=120_000)
    segments: tuple[TranscriptionSegment, ...]
    confidence: TranscriptionConfidence = Field(default_factory=TranscriptionConfidence)


class TextToSpeechRequest(ProviderModel):
    text: str = Field(min_length=1, max_length=8_192)
    language: Literal["pl"]


class TextToSpeechResponse(ProviderModel):
    duration_ms: int = Field(ge=0, le=120_000)


class WakeWordRequest(ProviderModel):
    pcm_s16le: bytes
    sample_rate: int = Field(ge=8_000, le=48_000)


class WakeWordResponse(ProviderModel):
    score: float = Field(ge=0, le=1)


@runtime_checkable
class SpeechToTextProvider(Protocol):
    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse: ...

    async def cancel(self) -> None: ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    async def speak(self, request: TextToSpeechRequest) -> TextToSpeechResponse: ...

    async def cancel(self) -> None: ...


@runtime_checkable
class WakeWordProvider(Protocol):
    async def score(self, audio: PcmFrame) -> float: ...

    def reset(self) -> None: ...


@runtime_checkable
class VoiceActivityProvider(Protocol):
    async def probability(self, audio: PcmFrame) -> float: ...

    def reset(self) -> None: ...


class MockLanguageModelProvider:
    @property
    def model(self) -> str:
        return "mock"

    async def status(self) -> ModelStatus:
        return ModelStatus(model="mock", state="unavailable", detail="Mock provider")

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise ProviderUnavailableError("Mock LLM provider is intentionally unavailable")
        yield ModelTextDelta(text="unreachable")  # pragma: no cover

    async def close(self) -> None:
        pass


class MockSpeechToTextProvider:
    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        raise ProviderUnavailableError("Mock STT provider is intentionally unavailable")

    async def cancel(self) -> None:
        pass


class MockTextToSpeechProvider:
    async def speak(self, request: TextToSpeechRequest) -> TextToSpeechResponse:
        raise ProviderUnavailableError("Mock TTS provider is intentionally unavailable")

    async def cancel(self) -> None:
        pass


class MockWakeWordProvider:
    async def score(self, audio: PcmFrame) -> float:
        raise ProviderUnavailableError("Mock wake-word provider is intentionally unavailable")

    def reset(self) -> None:
        pass


class MockVoiceActivityProvider:
    async def probability(self, audio: PcmFrame) -> float:
        raise ProviderUnavailableError("VAD provider is not available")

    def reset(self) -> None:
        pass
