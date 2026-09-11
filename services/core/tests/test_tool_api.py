import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from moj_asystent_core.api import CoreSettings, create_app
from moj_asystent_core.auth import SessionCredential
from moj_asystent_core.llm import (
    LanguageModelRequest,
    ModelStatus,
    ModelStreamEvent,
    ModelTextDelta,
    ModelToolCall,
    ModelToolCallDelta,
)
from moj_asystent_core.protocol import PROTOCOL_VERSION
from moj_asystent_core.tools.models import ActionOutput, DeleteFileArguments
from moj_asystent_core.tools.platform import PathPolicy, WindowsToolPlatform

SESSION_TOKEN = "S" * 43
ACTION_TOKEN = "A" * 43
SESSION_AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}"}
ACTION_AUTH = {"Authorization": f"Bearer {ACTION_TOKEN}"}
PROTOCOLS = ["moj-asystent.v1", f"credential.{SESSION_TOKEN}"]


class ToolProvider:
    model = "qwen3.5:4b"

    def __init__(self, target: Path) -> None:
        self.target = target
        self.turns = 0

    async def status(self) -> ModelStatus:
        return ModelStatus(model=self.model, state="ready")

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self.turns += 1
        if self.turns == 1:
            yield ModelToolCallDelta(
                call=ModelToolCall(
                    call_id=uuid4(),
                    name="delete_file",
                    arguments={"path": str(self.target)},
                )
            )
        else:
            assert request.messages[-1].role == "tool"
            yield ModelTextDelta(text="Plik został usunięty.")

    async def close(self) -> None:
        pass


class RecordingPlatform(WindowsToolPlatform):
    def __init__(self, root: Path) -> None:
        super().__init__(path_policy=PathPolicy((root,)))
        self.deleted: list[str] = []

    def delete_file(self, args: DeleteFileArguments) -> ActionOutput:
        self.deleted.append(args.path)
        return ActionOutput(changed=True, target=args.path, current_value="deleted")


def hello() -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "event_id": str(uuid4()),
        "occurred_at": "2026-09-11T08:00:00Z",
        "correlation_id": None,
        "type": "client.hello",
        "payload": {"client_id": "tool-test", "protocol_version": PROTOCOL_VERSION},
    }


def test_confirmation_endpoint_requires_action_credential_and_exact_single_use_binding(
    tmp_path: Path,
) -> None:
    target = tmp_path / "document.txt"
    target.write_text("test", encoding="utf-8")
    provider = ToolProvider(target)
    platform = RecordingPlatform(tmp_path)
    settings = CoreSettings(
        credential=SessionCredential.from_value(SESSION_TOKEN),
        action_credential=SessionCredential.from_value(ACTION_TOKEN),
        permission_policy_path=tmp_path / "permissions.json",
    )
    app = create_app(settings, provider, platform)

    with (
        TestClient(app, base_url="http://127.0.0.1") as client,
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
            headers=SESSION_AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "text": "Usuń dokument"},
        )
        assert response.status_code == 200

        request_event = None
        while request_event is None:
            event = socket.receive_json()
            if event["type"] == "tool.confirmation.requested":
                request_event = event
        payload = request_event["payload"]
        decision = {
            "protocol_version": PROTOCOL_VERSION,
            "confirmation_id": payload["confirmation_id"],
            "operation_id": payload["operation_id"],
            "call_id": payload["call_id"],
            "tool_name": payload["tool_name"],
            "arguments_digest": payload["arguments_digest"],
            "decision": "allow",
        }

        assert (
            client.post(
                "/tool-confirmations/resolve", headers=SESSION_AUTH, json=decision
            ).status_code
            == 401
        )
        modified = {**decision, "arguments_digest": "0" * 64}
        assert (
            client.post(
                "/tool-confirmations/resolve", headers=ACTION_AUTH, json=modified
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/tool-confirmations/resolve", headers=ACTION_AUTH, json=decision
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/tool-confirmations/resolve", headers=ACTION_AUTH, json=decision
            ).status_code
            == 409
        )

        events = []
        while not any(item["type"] == "assistant.response.completed" for item in events):
            events.append(socket.receive_json())

    assert platform.deleted == [str(target)]
    assert any(item["type"] == "tool.confirmation.resolved" for item in events)
    assert any(
        item["type"] == "tool.result" and item["payload"]["status"] == "success" for item in events
    )


def test_core_rejects_reused_session_and_action_credentials() -> None:
    credential = SessionCredential.from_value(SESSION_TOKEN)
    try:
        CoreSettings(credential=credential, action_credential=credential)
    except ValueError as error:
        assert "distinct" in str(error)
    else:
        raise AssertionError("Core accepted one credential for both trust boundaries")


def test_action_credential_cannot_access_the_ordinary_core_api(tmp_path: Path) -> None:
    settings = CoreSettings(
        credential=SessionCredential.from_value(SESSION_TOKEN),
        action_credential=SessionCredential.from_value(ACTION_TOKEN),
        permission_policy_path=tmp_path / "permissions.json",
    )
    app = create_app(settings, ToolProvider(tmp_path / "unused.txt"), RecordingPlatform(tmp_path))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health", headers=ACTION_AUTH).status_code == 401
        assert client.post("/chat/cancel", headers=ACTION_AUTH).status_code == 401


def test_last_websocket_disconnect_invalidates_an_unseen_confirmation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "document.txt"
    target.write_text("test", encoding="utf-8")
    platform = RecordingPlatform(tmp_path)
    settings = CoreSettings(
        credential=SessionCredential.from_value(SESSION_TOKEN),
        action_credential=SessionCredential.from_value(ACTION_TOKEN),
        permission_policy_path=tmp_path / "permissions.json",
    )
    app = create_app(settings, ToolProvider(target), platform)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        with client.websocket_connect(
            "/ws", headers={"host": "127.0.0.1"}, subprotocols=PROTOCOLS
        ) as socket:
            socket.send_text(json.dumps(hello()))
            assert socket.receive_json()["type"] == "system.health"
            assert socket.receive_json()["type"] == "assistant.state.changed"
            assert socket.receive_json()["type"] == "model.status.changed"
            assert (
                client.post(
                    "/chat",
                    headers=SESSION_AUTH,
                    json={"protocol_version": PROTOCOL_VERSION, "text": "Usuń dokument"},
                ).status_code
                == 200
            )
            while socket.receive_json()["type"] != "tool.confirmation.requested":
                pass

        deadline = time.monotonic() + 1
        while app.state.tool_engine.confirmations.pending() and time.monotonic() < deadline:
            time.sleep(0.01)

        assert app.state.tool_engine.confirmations.pending() == ()
        assert platform.deleted == []
