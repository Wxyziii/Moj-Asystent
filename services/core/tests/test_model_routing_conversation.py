from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest

from moj_asystent_core.conversation import LocalConversationService
from moj_asystent_core.llm import (
    LanguageModelRequest,
    ModelStatus,
    ModelStreamEvent,
    ModelTextDelta,
)
from moj_asystent_core.memory import SQLiteMemoryStore
from moj_asystent_core.model_routing import (
    HardwareSnapshot,
    ModelCandidate,
    ModelRouter,
    ProviderCapabilities,
)
from moj_asystent_core.runtime import CoreRuntime


class Provider:
    def __init__(self, model: str, provider: Literal["ollama", "llama_cpp", "openrouter"]) -> None:
        self.model = model
        self.provider = provider
        self.requests: list[LanguageModelRequest] = []

    async def status(self) -> ModelStatus:
        return ModelStatus(provider=self.provider, model=self.model, state="ready")

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        yield ModelTextDelta(text="Odpowiedź.")

    async def close(self) -> None:
        pass


class Hardware:
    async def capture(self) -> HardwareSnapshot:
        return HardwareSnapshot(
            free_ram_bytes=24 * 1024**3,
            total_ram_bytes=32 * 1024**3,
            free_vram_bytes=7 * 1024**3,
            total_vram_bytes=8 * 1024**3,
            gpu_utilization_percent=1,
            high_load_active=False,
        )


@pytest.mark.asyncio
async def test_cloud_route_does_not_receive_persistent_memory(tmp_path: Path) -> None:
    fast = Provider("qwen3.5:4b", "ollama")
    cloud = Provider("configured/cloud-model", "openrouter")
    local_capabilities = ProviderCapabilities(
        text=True,
        image=True,
        structured_output=True,
        tools=True,
        context_size=32_768,
    )
    router = ModelRouter(
        (
            ModelCandidate(
                tier="fast",
                provider_name="ollama",
                provider=fast,
                local=True,
                capabilities=local_capabilities,
            ),
            ModelCandidate(
                tier="deep",
                provider_name="openrouter",
                provider=cloud,
                local=False,
                capabilities=ProviderCapabilities(
                    text=True,
                    image=False,
                    structured_output=True,
                    tools=False,
                    context_size=32_768,
                ),
            ),
        ),
        Hardware(),
        mode="deep",
        data_policy="cloud_allowed",
    )
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.remember_preference("miasto", "Warszawa")
    runtime = CoreRuntime()
    service = LocalConversationService(runtime, router, memory_store=store)

    await service.respond(uuid4(), "Opowiedz żart", mode="text")

    assert cloud.requests
    assert all("ZAPISANA PAMIĘĆ" not in message.content for message in cloud.requests[0].messages)
    assert not fast.requests
    await service.close()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_secret_in_short_local_history_prevents_later_cloud_route() -> None:
    fast = Provider("qwen3.5:4b", "ollama")
    cloud = Provider("configured/cloud-model", "openrouter")
    router = ModelRouter(
        (
            ModelCandidate(
                tier="fast",
                provider_name="ollama",
                provider=fast,
                local=True,
                capabilities=ProviderCapabilities(
                    text=True,
                    image=True,
                    structured_output=True,
                    tools=True,
                    context_size=32_768,
                ),
            ),
            ModelCandidate(
                tier="deep",
                provider_name="openrouter",
                provider=cloud,
                local=False,
                capabilities=ProviderCapabilities(
                    text=True,
                    image=False,
                    structured_output=True,
                    tools=False,
                    context_size=32_768,
                ),
            ),
        ),
        Hardware(),
        mode="fast",
        data_policy="cloud_allowed",
    )
    runtime = CoreRuntime()
    service = LocalConversationService(runtime, router)

    await service.respond(uuid4(), "api_key: sekret-wylacznie-lokalny", mode="text")
    router.set_mode("deep")
    await service.respond(uuid4(), "Podsumuj poprzednią wiadomość", mode="text")

    assert len(fast.requests) == 2
    assert cloud.requests == []
    await service.close()
    await runtime.shutdown()
