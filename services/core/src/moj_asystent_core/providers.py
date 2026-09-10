"""Replaceable provider interfaces for local assistant engines."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .audio import PcmFrame


class ProviderUnavailableError(RuntimeError):
    """A safe failure used until a real local engine is configured."""


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LanguageModelRequest(ProviderModel):
    prompt: str = Field(min_length=1, max_length=32_768)


class LanguageModelResponse(ProviderModel):
    text: str


class SpeechToTextRequest(ProviderModel):
    pcm_s16le: bytes
    sample_rate: int = Field(ge=8_000, le=48_000)
    language: Literal["pl"]
    duration_ms: int = Field(gt=0, le=120_000)


class TranscriptionSegment(ProviderModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=4_096)
    average_log_probability: float | None = None


class SpeechToTextResponse(ProviderModel):
    transcript: str = Field(min_length=1, max_length=8_192)
    language: Literal["pl"]
    duration_ms: int = Field(gt=0, le=120_000)
    segments: tuple[TranscriptionSegment, ...]


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
class LanguageModelProvider(Protocol):
    async def generate(self, request: LanguageModelRequest) -> LanguageModelResponse: ...


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
    async def generate(self, request: LanguageModelRequest) -> LanguageModelResponse:
        raise ProviderUnavailableError("LLM provider is not available before Milestone 5")


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
