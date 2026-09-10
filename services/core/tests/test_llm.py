import json

import httpx
import pytest

from moj_asystent_core.llm import (
    ConversationContext,
    LanguageModelRequest,
    ModelStatus,
    OllamaLanguageModelProvider,
    ProviderProtocolError,
)


def response(lines: list[dict[str, object]], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=b"\n".join(json.dumps(line).encode() for line in lines),
        headers={"content-type": "application/x-ndjson"},
    )


@pytest.mark.asyncio
async def test_ollama_status_distinguishes_ready_and_missing_model() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaLanguageModelProvider(client=client)
        assert await provider.status() == ModelStatus(
            provider="ollama", model="qwen3.5:4b", state="ready", detail=None
        )

    async def missing(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:8b"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(missing)) as client:
        provider = OllamaLanguageModelProvider(client=client)
        assert (await provider.status()).state == "missing"


@pytest.mark.asyncio
async def test_ollama_status_rejects_an_oversized_model_list() -> None:
    async def oversized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b" " * 1_048_577)

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized)) as client:
        provider = OllamaLanguageModelProvider(client=client)
        assert (await provider.status()).state == "unavailable"


@pytest.mark.asyncio
async def test_ollama_streams_validated_text_and_forces_local_polish_chat() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return response(
            [
                {
                    "model": "qwen3.5:4b",
                    "created_at": "2026-09-10T20:00:00Z",
                    "message": {"role": "assistant", "content": "Dzień ", "thinking": ""},
                    "done": False,
                },
                {"message": {"role": "assistant", "content": "dobry."}, "done": False},
                {
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "total_duration": 10,
                },
            ]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaLanguageModelProvider(client=client)
        request = LanguageModelRequest(
            messages=(
                {"role": "system", "content": "Odpowiadaj po polsku."},
                {"role": "user", "content": "Cześć"},
            )
        )
        chunks = [chunk async for chunk in provider.stream(request)]

    assert chunks == ["Dzień ", "dobry."]
    assert seen == {
        "model": "qwen3.5:4b",
        "messages": [
            {"role": "system", "content": "Odpowiadaj po polsku."},
            {"role": "user", "content": "Cześć"},
        ],
        "stream": True,
        "think": False,
        "options": {"temperature": 0.3},
    }


@pytest.mark.asyncio
async def test_ollama_rejects_remote_endpoints_and_malformed_streams() -> None:
    with pytest.raises(ValueError, match="loopback"):
        OllamaLanguageModelProvider(base_url="http://example.com:11434")

    async def malformed(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"message":{"content":7},"done":false}\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(malformed)) as client:
        provider = OllamaLanguageModelProvider(client=client)
        with pytest.raises(ProviderProtocolError):
            _ = [
                chunk
                async for chunk in provider.stream(
                    LanguageModelRequest(messages=({"role": "user", "content": "test"},))
                )
            ]


def test_context_is_polish_bounded_and_keeps_complete_recent_turns() -> None:
    context = ConversationContext(max_turns=2, max_characters=120)
    context.remember("pierwsze pytanie", "pierwsza odpowiedź")
    context.remember("drugie pytanie", "druga odpowiedź")
    context.remember("trzecie pytanie", "trzecia odpowiedź")

    messages = context.messages_for("co dalej?")

    assert messages[0].role == "system"
    assert "polsku" in messages[0].content
    assert [message.content for message in messages[1:]] == [
        "drugie pytanie",
        "druga odpowiedź",
        "trzecie pytanie",
        "trzecia odpowiedź",
        "co dalej?",
    ]
    assert sum(len(message.content) for message in messages[1:]) <= 120


def test_context_does_not_commit_an_unfinished_response() -> None:
    context = ConversationContext()
    before = context.messages_for("nowe pytanie")
    after = context.messages_for("inne pytanie")
    assert all(message.content != "nowe pytanie" for message in after)
    assert before[-1].content == "nowe pytanie"
