import json
from base64 import b64encode
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from moj_asystent_core.llm import (
    SYSTEM_PROMPT,
    ConversationContext,
    LanguageModelRequest,
    ModelStatus,
    ModelTextDelta,
    ModelToolCallDelta,
    OllamaLanguageModelProvider,
    ProviderProtocolError,
    ProviderUnavailableError,
)
from moj_asystent_core.tools.models import ModelToolDefinition
from moj_asystent_core.vision import VisionCaptureInvalid, VisionImage


def response(lines: list[dict[str, object]], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=b"\n".join(json.dumps(line).encode() for line in lines),
        headers={"content-type": "application/x-ndjson"},
    )


def model_image() -> VisionImage:
    output = BytesIO()
    Image.new("RGB", (12, 8), (20, 40, 60)).save(output, format="JPEG")
    return VisionImage(
        capture_id=uuid4(),
        context_id=uuid4(),
        operation_id=uuid4(),
        width=12,
        height=8,
        jpeg=bytearray(output.getvalue()),
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


@pytest.mark.asyncio
async def test_ollama_uses_structured_tool_calling_and_validates_calls() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return response(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "get_system_stats",
                                    "arguments": {},
                                },
                            }
                        ],
                    },
                    "done": True,
                }
            ]
        )

    tool = ModelToolDefinition(
        function={
            "name": "get_system_stats",
            "description": "Statystyki",
            "parameters": {"type": "object", "additionalProperties": False},
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaLanguageModelProvider(client=client)
        events = [
            event
            async for event in provider.stream_turn(
                LanguageModelRequest(
                    messages=({"role": "user", "content": "Sprawdź komputer"},),
                    tools=(tool,),
                )
            )
        ]

    assert len(events) == 1
    assert isinstance(events[0], ModelToolCallDelta)
    assert events[0].call.name == "get_system_stats"
    assert seen["tools"] == [tool.model_dump(mode="json")]
    assert "RUN:" not in json.dumps(seen)


@pytest.mark.asyncio
async def test_ollama_multimodal_request_uses_official_message_images_shape() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return response(
            [{"message": {"role": "assistant", "content": "Widzę okno."}, "done": True}]
        )

    image = model_image()
    expected = b64encode(image.bytes_for_provider()).decode("ascii")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaLanguageModelProvider(client=client)
        events = [
            event
            async for event in provider.stream_turn(
                LanguageModelRequest(
                    messages=(
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": "Co jest na obrazie?"},
                    ),
                    images=(image,),
                )
            )
        ]

    messages = seen["messages"]
    assert isinstance(messages, list)
    assert messages[-1] == {
        "role": "user",
        "content": "Co jest na obrazie?",
        "images": [expected],
    }
    assert "niezaufan" in messages[0]["content"]
    assert isinstance(events[0], ModelTextDelta)
    assert events[0].text == "Widzę okno."
    assert image.cleared is False
    image.clear()


@pytest.mark.asyncio
async def test_multimodal_provider_timeout_fails_without_reclassifying_success() -> None:
    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    image = model_image()
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        provider = OllamaLanguageModelProvider(client=client)
        with pytest.raises(ProviderUnavailableError):
            _ = [
                event
                async for event in provider.stream_turn(
                    LanguageModelRequest(
                        messages=({"role": "user", "content": "Sprawdź obraz"},),
                        images=(image,),
                    )
                )
            ]
    image.clear()


def test_malformed_image_payload_is_rejected_before_provider_use() -> None:
    with pytest.raises(VisionCaptureInvalid):
        VisionImage(
            capture_id=uuid4(),
            context_id=uuid4(),
            operation_id=uuid4(),
            width=10,
            height=10,
            jpeg=bytearray(b"not-an-image"),
        )


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
