"""Replaceable provider interfaces; concrete engines belong to later milestones."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ProviderUnavailableError(RuntimeError):
    """A safe failure used until a real local engine is configured."""


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LanguageModelRequest(ProviderModel):
    prompt: str = Field(min_length=1, max_length=32_768)


class LanguageModelResponse(ProviderModel):
    text: str


class SpeechToTextRequest(ProviderModel):
    audio_reference: str = Field(min_length=1, max_length=256)


class SpeechToTextResponse(ProviderModel):
    transcript: str


class TextToSpeechRequest(ProviderModel):
    text: str = Field(min_length=1, max_length=8_192)


class TextToSpeechResponse(ProviderModel):
    audio_reference: str


class WakeWordRequest(ProviderModel):
    audio_reference: str = Field(min_length=1, max_length=256)


class WakeWordResponse(ProviderModel):
    detected: bool


@runtime_checkable
class LanguageModelProvider(Protocol):
    async def generate(self, request: LanguageModelRequest) -> LanguageModelResponse: ...


@runtime_checkable
class SpeechToTextProvider(Protocol):
    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse: ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    async def synthesize(self, request: TextToSpeechRequest) -> TextToSpeechResponse: ...


@runtime_checkable
class WakeWordProvider(Protocol):
    async def detect(self, request: WakeWordRequest) -> WakeWordResponse: ...


class MockLanguageModelProvider:
    async def generate(self, request: LanguageModelRequest) -> LanguageModelResponse:
        raise ProviderUnavailableError("LLM provider is not available in Milestone 2")


class MockSpeechToTextProvider:
    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        raise ProviderUnavailableError("STT provider is not available in Milestone 2")


class MockTextToSpeechProvider:
    async def synthesize(self, request: TextToSpeechRequest) -> TextToSpeechResponse:
        raise ProviderUnavailableError("TTS provider is not available in Milestone 2")


class MockWakeWordProvider:
    async def detect(self, request: WakeWordRequest) -> WakeWordResponse:
        raise ProviderUnavailableError("Wake-word provider is not available in Milestone 2")
