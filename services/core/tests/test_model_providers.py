from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from moj_asystent_core.llm import (
    LanguageModelRequest,
    ModelTextDelta,
    ModelUsageEvent,
    ProviderProtocolError,
    ProviderUnavailableError,
)
from moj_asystent_core.model_providers import (
    LlamaCppLanguageModelProvider,
    OpenRouterAuthenticationError,
    OpenRouterLanguageModelProvider,
    OpenRouterRateLimitError,
)


def sse(*payloads: dict[str, object], status: int = 200) -> httpx.Response:
    content = b"".join(
        b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n" for payload in payloads
    )
    return httpx.Response(status, content=content, headers={"content-type": "text/event-stream"})


def request() -> LanguageModelRequest:
    return LanguageModelRequest(messages=({"role": "user", "content": "Cześć"},))


def test_provider_urls_are_fail_closed() -> None:
    with pytest.raises(ValueError, match="OpenRouter"):
        OpenRouterLanguageModelProvider(
            api_key="test-secret-key-0001",
            model="vendor/model",
            base_url="https://example.com/api/v1",
        )
    with pytest.raises(ValueError, match="loopback"):
        LlamaCppLanguageModelProvider(model="deep", base_url="http://192.168.1.5:11435")


@pytest.mark.asyncio
async def test_openrouter_requires_key_without_making_a_request() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterLanguageModelProvider(
            api_key=None, model="vendor/model", client=client
        )
        status = await provider.status()

    assert status.state == "unavailable"
    assert "klucza" in (status.detail or "").casefold()
    assert calls == 0


@pytest.mark.asyncio
async def test_openrouter_catalog_rejects_removed_configured_model() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models"
        return httpx.Response(200, json={"data": [{"id": "other/model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterLanguageModelProvider(
            api_key="test-secret-key-0001", model="vendor/removed", client=client
        )
        status = await provider.status()

    assert status.state == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [(401, OpenRouterAuthenticationError), (429, OpenRouterRateLimitError)],
)
async def test_openrouter_maps_auth_and_rate_limit_failures(
    status: int, error: type[Exception]
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "provider detail"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterLanguageModelProvider(
            api_key="test-secret-key-0001", model="vendor/model", client=client
        )
        with pytest.raises(error):
            _ = [event async for event in provider.stream_turn(request())]


@pytest.mark.asyncio
async def test_openrouter_stream_is_bounded_and_uses_no_environment_proxy() -> None:
    seen: dict[str, object] = {}

    async def handler(http_request: httpx.Request) -> httpx.Response:
        seen["authorization"] = http_request.headers.get("authorization")
        seen["body"] = json.loads(http_request.content)
        return sse(
            {"choices": [{"delta": {"content": "Dobra "}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "odpowiedź."}, "finish_reason": None}]},
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                    "cost": 0.0002,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterLanguageModelProvider(
            api_key="test-secret-key-0001", model="vendor/model", client=client
        )
        events = [event async for event in provider.stream_turn(request())]

    assert [event.text for event in events if isinstance(event, ModelTextDelta)] == [
        "Dobra ",
        "odpowiedź.",
    ]
    usage = next(event for event in events if isinstance(event, ModelUsageEvent))
    assert usage.input_tokens == 12
    assert usage.output_tokens == 4
    assert usage.provider_cost == 0.0002
    assert usage.cost_unit == "openrouter_credits"
    assert seen["authorization"] == "Bearer test-secret-key-0001"
    assert seen["body"] == {
        "model": "vendor/model",
        "messages": [{"role": "user", "content": "Cześć"}],
        "stream": True,
        "max_tokens": 2048,
        "temperature": 0.3,
    }


@pytest.mark.asyncio
async def test_openrouter_rejects_oversized_stream_chunk() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return sse(
            {"choices": [{"delta": {"content": "x" * 9_000}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterLanguageModelProvider(
            api_key="test-secret-key-0001", model="vendor/model", client=client
        )
        with pytest.raises(ProviderProtocolError):
            _ = [event async for event in provider.stream_turn(request())]


@pytest.mark.asyncio
async def test_openrouter_rejects_inconsistent_usage_metadata() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return sse(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 999,
                },
            }
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterLanguageModelProvider(
            api_key="test-secret-key-0001", model="vendor/model", client=client
        )
        with pytest.raises(ProviderProtocolError, match="malformed"):
            _ = [event async for event in provider.stream_turn(request())]


@pytest.mark.asyncio
async def test_openrouter_maps_timeout_without_exposing_provider_detail() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream detail", request=http_request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterLanguageModelProvider(
            api_key="test-secret-key-0001", model="vendor/model", client=client
        )
        with pytest.raises(ProviderUnavailableError, match="unavailable") as captured:
            _ = [event async for event in provider.stream_turn(request())]

    assert "upstream detail" not in str(captured.value)


@pytest.mark.asyncio
async def test_llama_cpp_reports_missing_configured_gguf_without_network(tmp_path: Path) -> None:
    provider = LlamaCppLanguageModelProvider(
        model="Qwen3.5 27B GGUF", model_path=tmp_path / "missing.gguf"
    )

    status = await provider.status()

    assert status.state == "missing"
    assert "GGUF" in (status.detail or "")
    await provider.close()


@pytest.mark.asyncio
async def test_llama_cpp_health_and_stream_use_bounded_openai_boundary(tmp_path: Path) -> None:
    model_path = tmp_path / "qwen3.5-27b-q4_k_m.gguf"
    model_path.write_bytes(b"GGUF-test")

    async def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        assert http_request.url.path == "/v1/chat/completions"
        return sse(
            {"choices": [{"delta": {"content": "Głęboka analiza."}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = LlamaCppLanguageModelProvider(
            model="Qwen3.5 27B GGUF", model_path=model_path, client=client
        )
        assert (await provider.status()).state == "ready"
        events = [event async for event in provider.stream_turn(request())]

    assert isinstance(events[0], ModelTextDelta)
    assert events[0].text == "Głęboka analiza."
