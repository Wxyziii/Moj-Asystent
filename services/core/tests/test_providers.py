import pytest

from moj_asystent_core.providers import (
    MockLanguageModelProvider,
    MockSpeechToTextProvider,
    MockTextToSpeechProvider,
    MockWakeWordProvider,
    ProviderUnavailableError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [
        MockLanguageModelProvider(),
        MockSpeechToTextProvider(),
        MockTextToSpeechProvider(),
        MockWakeWordProvider(),
    ],
)
async def test_mock_providers_fail_closed_without_real_engines(provider: object) -> None:
    with pytest.raises(ProviderUnavailableError):
        await provider.execute_for_test()
