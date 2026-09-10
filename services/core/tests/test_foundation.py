import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from moj_asystent_core.api import CoreSettings, create_app
from moj_asystent_core.auth import SessionCredential

TOKEN = "A" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PROTOCOLS = ["moj-asystent.v1", f"credential.{TOKEN}"]


def app():
    return create_app(CoreSettings(credential=SessionCredential.from_value(TOKEN)))


def test_browser_health_check_allows_the_desktop_origin() -> None:
    with TestClient(app(), base_url="http://127.0.0.1") as client:
        response = client.get("/health", headers={**AUTH, "origin": "http://localhost:1420"})
        assert response.headers.get("access-control-allow-origin") == "http://localhost:1420"


def test_binary_websocket_frame_is_rejected_cleanly() -> None:
    with (
        TestClient(app(), base_url="http://127.0.0.1") as client,
        client.websocket_connect(
            "/ws", headers={"host": "127.0.0.1"}, subprotocols=PROTOCOLS
        ) as socket,
    ):
        socket.send_bytes(b"bad")
        assert socket.receive_json()["type"] == "system.error"


def test_foreign_host_header_is_rejected() -> None:
    with TestClient(app(), base_url="http://127.0.0.1") as client:
        assert client.get("/health", headers={"host": "attacker.example"}).status_code == 400


def test_foreign_websocket_origin_is_rejected() -> None:
    with TestClient(app(), base_url="http://127.0.0.1") as client:
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            client.websocket_connect(
                "/ws",
                headers={"host": "127.0.0.1", "origin": "https://attacker.example"},
                subprotocols=PROTOCOLS,
            ),
        ):
            pass
        assert closed.value.code == 1008


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_non_loopback_bind_configuration_is_rejected(host: str) -> None:
    with pytest.raises(ValueError):
        CoreSettings(host=host)
