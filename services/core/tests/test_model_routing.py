from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from moj_asystent_core.llm import (
    LanguageModelRequest,
    ModelStatus,
    ModelStreamEvent,
    ModelTextDelta,
    ModelUsageEvent,
)
from moj_asystent_core.model_routing import (
    HardwareSnapshot,
    ModelCandidate,
    ModelMode,
    ModelRouter,
    ProviderCapabilities,
    ProviderName,
    RoutingRequest,
    contains_sensitive_text,
    explicit_tier_override,
)

GIB = 1024**3


class FakeProvider:
    def __init__(
        self,
        model: str,
        *,
        provider: ProviderName = "ollama",
        state: Literal["unavailable", "missing", "loading", "ready", "error"] = "ready",
        chunks: tuple[str, ...] = ("Dobrze.",),
    ) -> None:
        self._model = model
        self._provider = provider
        self._state = state
        self._chunks = chunks
        self.unload_count = 0
        self.closed = False

    @property
    def model(self) -> str:
        return self._model

    async def status(self) -> ModelStatus:
        return ModelStatus(
            provider=self._provider,
            model=self._model,
            state=self._state,
            detail=None,
        )

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelStreamEvent]:
        for chunk in self._chunks:
            await asyncio.sleep(0)
            yield ModelTextDelta(text=chunk)

    async def unload(self) -> None:
        self.unload_count += 1

    async def close(self) -> None:
        self.closed = True


class FakeHardware:
    def __init__(self, snapshot: HardwareSnapshot) -> None:
        self.snapshot = snapshot

    async def capture(self) -> HardwareSnapshot:
        return self.snapshot


class BlockingProvider(FakeProvider):
    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelStreamEvent]:
        try:
            self.started.set()
            yield ModelTextDelta(text="część")
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


class UsageProvider(FakeProvider):
    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield ModelTextDelta(text="Gotowe")
        yield ModelUsageEvent(
            input_tokens=20,
            output_tokens=5,
            total_tokens=25,
            provider_cost=0.001,
            cost_unit="openrouter_credits",
        )


class FailingStatusProvider(FakeProvider):
    async def status(self) -> ModelStatus:
        raise RuntimeError("provider detail must not escape")


def hardware(
    *,
    free_ram: int = 24,
    total_ram: int = 32,
    free_vram: int = 7,
    total_vram: int = 8,
    gpu_load: float = 10,
    high_load: bool = False,
) -> HardwareSnapshot:
    return HardwareSnapshot(
        free_ram_bytes=free_ram * GIB,
        total_ram_bytes=total_ram * GIB,
        free_vram_bytes=free_vram * GIB,
        total_vram_bytes=total_vram * GIB,
        gpu_utilization_percent=gpu_load,
        high_load_active=high_load,
    )


def candidates(
    *,
    quality_state: Literal["unavailable", "missing", "loading", "ready", "error"] = "ready",
    deep_state: Literal["unavailable", "missing", "loading", "ready", "error"] = "ready",
    cloud_state: Literal["unavailable", "missing", "loading", "ready", "error"] = "ready",
) -> tuple[ModelCandidate, ...]:
    common = ProviderCapabilities(
        text=True,
        image=True,
        structured_output=True,
        tools=True,
        context_size=32_768,
    )
    return (
        ModelCandidate(
            tier="fast",
            provider_name="ollama",
            provider=FakeProvider("qwen3.5:4b"),
            local=True,
            capabilities=common,
            approximate_size_bytes=int(3.4 * GIB),
        ),
        ModelCandidate(
            tier="quality",
            provider_name="ollama",
            provider=FakeProvider("qwen3.5:9b", state=quality_state),
            local=True,
            capabilities=common,
            approximate_size_bytes=int(6.6 * GIB),
        ),
        ModelCandidate(
            tier="deep",
            provider_name="llama_cpp",
            provider=FakeProvider("Qwen3.5 27B GGUF", provider="llama_cpp", state=deep_state),
            local=True,
            capabilities=ProviderCapabilities(
                text=True,
                image=False,
                structured_output=True,
                tools=False,
                context_size=16_384,
            ),
            approximate_size_bytes=17 * GIB,
        ),
        ModelCandidate(
            tier="deep",
            provider_name="openrouter",
            provider=FakeProvider(
                "configured/cloud-model", provider="openrouter", state=cloud_state
            ),
            local=False,
            capabilities=ProviderCapabilities(
                text=True,
                image=False,
                structured_output=True,
                tools=False,
                context_size=32_768,
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"),
    [("fast", "fast"), ("quality", "quality"), ("deep", "deep")],
)
async def test_explicit_modes_select_requested_local_tier(
    mode: ModelMode, expected: Literal["fast", "quality", "deep"]
) -> None:
    router = ModelRouter(candidates(), FakeHardware(hardware()), mode=mode)

    route = await router.select(RoutingRequest(user_text="Wyjaśnij problem"))

    assert route.selected_tier == expected
    assert route.local is True
    assert route.fallback_reason is None


@pytest.mark.asyncio
async def test_auto_keeps_simple_request_on_fast_tier() -> None:
    router = ModelRouter(candidates(), FakeHardware(hardware()), mode="auto")

    route = await router.select(RoutingRequest(user_text="Otwórz Spotify"))

    assert route.selected_tier == "fast"


@pytest.mark.asyncio
async def test_auto_routes_complex_coding_analysis_to_quality() -> None:
    router = ModelRouter(candidates(), FakeHardware(hardware()), mode="auto")

    route = await router.select(
        RoutingRequest(
            user_text=(
                "Przeanalizuj ten kod i znajdź problem z concurrency. "
                "Porównaj możliwe rozwiązania i ich kompromisy."
            ),
            context_characters=4_000,
        )
    )

    assert route.selected_tier == "quality"


@pytest.mark.asyncio
async def test_polish_escalation_is_one_request_deep_override() -> None:
    router = ModelRouter(candidates(), FakeHardware(hardware()), mode="fast")

    route = await router.select(
        RoutingRequest(user_text="Przemyśl to dokładniej.", explicit_tier="deep")
    )
    next_route = await router.select(RoutingRequest(user_text="Dzięki"))

    assert explicit_tier_override("Użyj lepszego modelu.") == "quality"
    assert explicit_tier_override("Przeanalizuj to głębiej.") == "deep"
    assert route.selected_tier == "deep"
    assert route.explicit_override is True
    assert next_route.selected_tier == "fast"


@pytest.mark.asyncio
async def test_private_mode_never_selects_cloud() -> None:
    local_candidates = candidates(deep_state="missing")
    router = ModelRouter(
        local_candidates,
        FakeHardware(hardware()),
        mode="private",
        data_policy="cloud_allowed",
    )

    route = await router.select(
        RoutingRequest(user_text="Przeanalizuj bardzo dokładnie", explicit_tier="deep")
    )

    assert route.local is True
    assert route.provider_name != "openrouter"
    assert route.fallback_reason is not None


@pytest.mark.asyncio
async def test_cloud_is_considered_only_when_policy_allows_it() -> None:
    configured = candidates(deep_state="missing")
    local_only = ModelRouter(
        configured,
        FakeHardware(hardware()),
        mode="deep",
        data_policy="local_only",
    )
    cloud_allowed = ModelRouter(
        configured,
        FakeHardware(hardware()),
        mode="deep",
        data_policy="cloud_allowed",
    )

    local_route = await local_only.select(RoutingRequest(user_text="Analiza"))
    cloud_route = await cloud_allowed.select(RoutingRequest(user_text="Analiza"))

    assert local_route.local is True
    assert cloud_route.provider_name == "openrouter"
    assert cloud_route.local is False


@pytest.mark.asyncio
async def test_sensitive_text_never_crosses_to_cloud_candidate() -> None:
    configured = candidates(deep_state="missing")
    router = ModelRouter(
        configured,
        FakeHardware(hardware()),
        mode="deep",
        data_policy="cloud_allowed",
    )
    text = "api_key: bardzo-tajna-wartość"

    route = await router.select(
        RoutingRequest(
            user_text=text,
            sensitive_context=contains_sensitive_text(text),
        )
    )

    assert route.local is True
    assert route.provider_name != "openrouter"


@pytest.mark.asyncio
async def test_high_gpu_load_keeps_automatic_routing_on_fast() -> None:
    router = ModelRouter(
        candidates(),
        FakeHardware(hardware(gpu_load=95, high_load=True)),
        mode="auto",
    )

    route = await router.select(
        RoutingRequest(
            user_text="Przeanalizuj architekturę i dokładnie porównaj kompromisy",
            context_characters=8_000,
        )
    )

    assert route.selected_tier == "fast"
    assert "obciąż" in (route.fallback_reason or "").casefold()


@pytest.mark.asyncio
async def test_explicit_override_can_escalate_during_high_load() -> None:
    router = ModelRouter(
        candidates(),
        FakeHardware(hardware(gpu_load=95, high_load=True)),
        mode="auto",
    )

    route = await router.select(
        RoutingRequest(user_text="Użyj lepszego modelu", explicit_tier="quality")
    )

    assert route.selected_tier == "quality"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [hardware(free_ram=5), hardware(free_vram=1, total_vram=4)],
)
async def test_quality_falls_back_when_memory_is_predictably_insufficient(
    snapshot: HardwareSnapshot,
) -> None:
    router = ModelRouter(candidates(), FakeHardware(snapshot), mode="quality")

    route = await router.select(RoutingRequest(user_text="Trudne pytanie"))

    assert route.selected_tier == "fast"
    assert route.fallback_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("quality_state", ["missing", "unavailable", "error"])
async def test_missing_or_failed_model_has_visible_predictable_fallback(
    quality_state: Literal["missing", "unavailable", "error"],
) -> None:
    router = ModelRouter(
        candidates(quality_state=quality_state), FakeHardware(hardware()), mode="quality"
    )

    route = await router.select(RoutingRequest(user_text="Trudne pytanie"))

    assert route.selected_tier == "fast"
    assert route.fallback_reason
    if quality_state == "missing":
        assert "qwen3.5:9b" in route.fallback_reason
    else:
        assert "ollama" in route.fallback_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("required", ["image", "tools"])
async def test_router_never_selects_candidate_without_required_capability(
    required: str,
) -> None:
    router = ModelRouter(candidates(), FakeHardware(hardware()), mode="deep")

    route = await router.select(
        RoutingRequest(
            user_text="Sprawdź to",
            requires_image=required == "image",
            requires_tools=required == "tools",
        )
    )

    assert route.provider_name == "ollama"
    assert route.capabilities.image is (required == "image") or route.capabilities.tools
    assert route.fallback_reason


@pytest.mark.asyncio
async def test_switch_unloads_previous_model_but_reuse_does_not_thrash() -> None:
    configured = candidates()
    fast = configured[0].provider
    router = ModelRouter(configured, FakeHardware(hardware()), mode="fast")
    first = await router.select(RoutingRequest(user_text="Cześć"))
    await router.activate(first)
    second = await router.select(RoutingRequest(user_text="Jeszcze raz"))
    await router.activate(second)

    assert isinstance(fast, FakeProvider)
    assert fast.unload_count == 0

    router.set_mode("quality")
    quality = await router.select(RoutingRequest(user_text="Analiza"))
    await router.activate(quality)

    assert fast.unload_count == 1


@pytest.mark.asyncio
async def test_cancellation_during_stream_prevents_late_result_and_allows_switch() -> None:
    fast = BlockingProvider("qwen3.5:4b")
    quality = FakeProvider("qwen3.5:9b")
    capabilities = ProviderCapabilities(
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
                capabilities=capabilities,
            ),
            ModelCandidate(
                tier="quality",
                provider_name="ollama",
                provider=quality,
                local=True,
                capabilities=capabilities,
            ),
        ),
        FakeHardware(hardware()),
        mode="fast",
    )
    route = await router.select(RoutingRequest(user_text="Cześć"))
    received: list[str] = []

    async def consume() -> None:
        async for event in router.stream_turn(
            route,
            LanguageModelRequest(messages=({"role": "user", "content": "Cześć"},)),
        ):
            if isinstance(event, ModelTextDelta):
                received.append(event.text)

    task = asyncio.create_task(consume())
    await fast.started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    router.set_mode("quality")
    next_route = await router.select(RoutingRequest(user_text="Analiza"))
    await router.activate(next_route)

    assert fast.cancelled.is_set()
    assert received == ["część"]
    assert router.metrics()[-1].completed is False
    assert fast.unload_count == 1
    assert next_route.selected_tier == "quality"
    await router.close()


@pytest.mark.asyncio
async def test_idle_policy_never_unloads_during_active_generation() -> None:
    fast = BlockingProvider("qwen3.5:4b")
    capabilities = ProviderCapabilities(
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
                capabilities=capabilities,
            ),
        ),
        FakeHardware(hardware()),
        mode="fast",
        idle_unload_seconds=30,
    )
    route = await router.select(RoutingRequest(user_text="Długa analiza"))

    async def consume() -> None:
        _ = [
            event
            async for event in router.stream_turn(
                route,
                LanguageModelRequest(messages=({"role": "user", "content": "Analizuj"},)),
            )
        ]

    task = asyncio.create_task(consume())
    await fast.started.wait()
    router._last_used = time.monotonic() - 31
    await router._unload_if_idle()

    assert fast.unload_count == 0
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await router.close()


@pytest.mark.asyncio
async def test_provider_usage_metadata_stays_in_bounded_router_metrics() -> None:
    fast = UsageProvider("qwen3.5:4b")
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
        ),
        FakeHardware(hardware()),
        mode="fast",
    )
    route = await router.select(RoutingRequest(user_text="Cześć"))

    _ = [
        event
        async for event in router.stream_turn(
            route,
            LanguageModelRequest(messages=({"role": "user", "content": "Cześć"},)),
        )
    ]

    metric = router.metrics()[-1]
    assert metric.input_tokens == 20
    assert metric.output_tokens == 5
    assert metric.provider_cost == 0.001
    assert metric.cost_unit == "openrouter_credits"
    await router.close()


@pytest.mark.asyncio
async def test_unexpected_provider_status_failure_falls_back_and_catalog_stays_available() -> None:
    broken = FailingStatusProvider("qwen3.5:9b")
    fast = FakeProvider("qwen3.5:4b")
    capabilities = ProviderCapabilities(
        text=True,
        image=True,
        structured_output=True,
        tools=True,
        context_size=32_768,
    )
    router = ModelRouter(
        (
            ModelCandidate(
                tier="quality",
                provider_name="ollama",
                provider=broken,
                local=True,
                capabilities=capabilities,
            ),
            ModelCandidate(
                tier="fast",
                provider_name="ollama",
                provider=fast,
                local=True,
                capabilities=capabilities,
            ),
        ),
        FakeHardware(hardware()),
        mode="quality",
    )

    route = await router.select(RoutingRequest(user_text="Trudne pytanie"))
    catalog = await router.catalog()

    assert route.selected_tier == "fast"
    assert "poprawnego statusu" in (route.fallback_reason or "")
    assert catalog[0][1].state == "error"
    assert "provider detail" not in (catalog[0][1].detail or "")
    await router.close()
