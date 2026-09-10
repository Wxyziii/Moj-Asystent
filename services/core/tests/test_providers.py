import pytest

from moj_asystent_core.providers import (
    LanguageModelProvider,
    LanguageModelRequest,
    MockLanguageModelProvider,
    MockSpeechToTextProvider,
    MockTextToSpeechProvider,
    MockWakeWordProvider,
    ProviderUnavailableError,
    SpeechToTextProvider,
    SpeechToTextRequest,
    TextToSpeechProvider,
    TextToSpeechRequest,
    WakeWordProvider,
    WakeWordRequest,
)


@pytest.mark.asyncio
async def test_stubs_substitute_at_the_real_provider_interfaces() -> None:
    llm: LanguageModelProvider = MockLanguageModelProvider()
    stt: SpeechToTextProvider = MockSpeechToTextProvider()
    tts: TextToSpeechProvider = MockTextToSpeechProvider()
    wake: WakeWordProvider = MockWakeWordProvider()
    assert isinstance(llm, LanguageModelProvider)
    assert isinstance(stt, SpeechToTextProvider)
    assert isinstance(tts, TextToSpeechProvider)
    assert isinstance(wake, WakeWordProvider)
    with pytest.raises(ProviderUnavailableError):
        await llm.generate(LanguageModelRequest(prompt="test"))
    with pytest.raises(ProviderUnavailableError):
        await stt.transcribe(SpeechToTextRequest(audio_reference="in-memory"))
    with pytest.raises(ProviderUnavailableError):
        await tts.synthesize(TextToSpeechRequest(text="test"))
    with pytest.raises(ProviderUnavailableError):
        await wake.detect(WakeWordRequest(audio_reference="in-memory"))
