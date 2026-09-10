import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from moj_asystent_core.api import create_app
from moj_asystent_core.protocol import PROTOCOL_VERSION


def test_health_endpoint_reports_a_ready_versioned_local_core() -> None:
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "core",
        "status": "ready",
        "protocol_version": PROTOCOL_VERSION,
        "assistant_state": "idle",
    }


def test_websocket_handshake_synchronizes_health_and_initial_state() -> None:
    with (
        TestClient(create_app(), base_url="http://127.0.0.1") as client,
        client.websocket_connect("/ws", headers={"host": "127.0.0.1"}) as socket,
    ):
        socket.send_json(
            {
                "protocol_version": PROTOCOL_VERSION,
                "event_id": "c1f8377b-0c54-4d23-9a8a-74f9220cb139",
                "occurred_at": "2026-09-09T20:00:00+00:00",
                "type": "client.hello",
                "correlation_id": None,
                "payload": {"client_id": "desktop-test", "protocol_version": PROTOCOL_VERSION},
            }
        )

        health = socket.receive_json()
        state = socket.receive_json()

    assert health["type"] == "system.health"
    assert health["payload"]["status"] == "ready"
    assert state == {
        **state,
        "protocol_version": PROTOCOL_VERSION,
        "type": "assistant.state.changed",
        "payload": {"previous_state": None, "state": "idle"},
    }


def test_websocket_rejects_malformed_payloads_and_closes() -> None:
    with (
        TestClient(create_app(), base_url="http://127.0.0.1") as client,
        client.websocket_connect("/ws", headers={"host": "127.0.0.1"}) as socket,
    ):
        socket.send_text("not json")
        error = socket.receive_json()
        assert error["type"] == "system.error"
        assert error["payload"]["code"] == "invalid_message"
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

    assert closed.value.code == 1008


def test_websocket_rejects_an_incompatible_protocol_version() -> None:
    with (
        TestClient(create_app(), base_url="http://127.0.0.1") as client,
        client.websocket_connect("/ws", headers={"host": "127.0.0.1"}) as socket,
    ):
        socket.send_json(
            {
                "protocol_version": "2.0",
                "event_id": "c1f8377b-0c54-4d23-9a8a-74f9220cb139",
                "occurred_at": "2026-09-09T20:00:00+00:00",
                "type": "client.hello",
                "correlation_id": None,
                "payload": {"client_id": "desktop-test", "protocol_version": "2.0"},
            }
        )
        assert socket.receive_json()["payload"]["code"] == "unsupported_protocol"
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
