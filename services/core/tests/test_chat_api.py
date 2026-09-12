import json
import threading
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from moj_asystent_core.api import CoreSettings, create_app
from moj_asystent_core.auth import SessionCredential
from moj_asystent_core.llm import LanguageModelRequest, ModelStatus, ModelTextDelta
from moj_asystent_core.protocol import PROTOCOL_VERSION

TOKEN = "C" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PROTOCOLS = ["moj-asystent.v1", f"credential.{TOKEN}"]


class ChatProvider:
    model = "qwen3.5:4b"

    async def status(self) -> ModelStatus:
        return ModelStatus(model=self.model, state="ready")

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelTextDelta]:
        assert request.messages[-1].content == "Jak się masz?"
        yield ModelTextDelta(text="Dobrze, ")
        yield ModelTextDelta(text="dziękuję.")

    async def close(self) -> None:
        pass


def hello() -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "event_id": "c1f8377b-0c54-4d23-9a8a-74f9220cb139",
        "occurred_at": "2026-09-10T20:00:00Z",
        "correlation_id": None,
        "type": "client.hello",
        "payload": {"client_id": "chat-test", "protocol_version": PROTOCOL_VERSION},
    }


def test_typed_chat_streams_a_local_model_response_over_websocket() -> None:
    settings = CoreSettings(credential=SessionCredential.from_value(TOKEN))
    with (
        TestClient(create_app(settings, ChatProvider()), base_url="http://127.0.0.1") as client,
        client.websocket_connect(
            "/ws", headers={"host": "127.0.0.1"}, subprotocols=PROTOCOLS
        ) as socket,
    ):
        socket.send_text(json.dumps(hello()))
        assert socket.receive_json()["type"] == "system.health"
        assert socket.receive_json()["type"] == "assistant.state.changed"
        assert socket.receive_json()["type"] == "model.status.changed"

        response = client.post(
            "/chat",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "text": "Jak się masz?"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

        events: list[dict[str, object]] = []
        while not any(event["type"] == "assistant.response.completed" for event in events):
            events.append(socket.receive_json())

    types = [event["type"] for event in events]
    assert "assistant.response.started" in types
    assert types.count("assistant.response.delta") == 2
    completed = next(event for event in events if event["type"] == "assistant.response.completed")
    assert completed["payload"] == {
        "operation_id": response.json()["operation_id"],
        "text": "Dobrze, dziękuję.",
        "spoken_text": None,
        "kind": "local_model",
        "model": "qwen3.5:4b",
        "provider": "ollama",
        "tier": "fast",
        "location": "local",
        "fallback_reason": None,
    }


def test_chat_rejects_untrusted_or_blank_payloads() -> None:
    settings = CoreSettings(credential=SessionCredential.from_value(TOKEN))
    with TestClient(create_app(settings, ChatProvider()), base_url="http://127.0.0.1") as client:
        old = client.post("/chat", headers=AUTH, json={"protocol_version": "1.1", "text": "test"})
        blank = client.post(
            "/chat",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "text": "   "},
        )
        extra = client.post(
            "/chat",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "text": "test", "tool": "shell"},
        )

    assert old.status_code == blank.status_code == extra.status_code == 422


class BlockingProvider(ChatProvider):
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelTextDelta]:
        import asyncio

        try:
            yield ModelTextDelta(text="część")
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


def test_chat_cancel_stops_pending_generation_and_returns_to_idle() -> None:
    provider = BlockingProvider()
    settings = CoreSettings(credential=SessionCredential.from_value(TOKEN))
    with TestClient(create_app(settings, provider), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/chat",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "text": "Jak się masz?"},
        )
        assert response.status_code == 200
        cancelled = client.post("/chat/cancel", headers=AUTH)
        assert cancelled.json() == {"status": "idle"}
        assert client.get("/health", headers=AUTH).json()["assistant_state"] == "idle"

    assert provider.cancelled.wait(1)
