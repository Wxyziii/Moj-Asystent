import pytest

from moj_asystent_core.audio import PcmFrame
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
        _ = [
            chunk
            async for chunk in llm.stream_turn(
                LanguageModelRequest(messages=({"role": "user", "content": "test"},))
            )
        ]
    with pytest.raises(ProviderUnavailableError):
        await stt.transcribe(
            SpeechToTextRequest(
                pcm_s16le=b"\0\0" * 160,
                sample_rate=16_000,
                language="pl",
                duration_ms=10,
            )
        )
    with pytest.raises(ProviderUnavailableError):
        await tts.speak(TextToSpeechRequest(text="test", language="pl"))
    with pytest.raises(ProviderUnavailableError):
        await wake.score(PcmFrame(pcm_s16le=b"\0\0" * 160, sample_rate=16_000))
