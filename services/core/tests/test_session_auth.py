import re

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from moj_asystent_core.api import CoreSettings, create_app
from moj_asystent_core.auth import SessionCredential


def settings(token: str = "A" * 43) -> CoreSettings:
    return CoreSettings(credential=SessionCredential.from_value(token))


def test_session_credentials_are_unique_high_entropy_urlsafe_values() -> None:
    first, second = SessionCredential.generate(), SessionCredential.generate()
    assert first != second
    assert len(first.reveal()) >= 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first.reveal())
    assert "A" * 20 not in repr(first)


def test_health_fails_closed_without_exact_bearer_credential() -> None:
    with TestClient(create_app(settings()), base_url="http://127.0.0.1") as client:
        assert client.get("/health").status_code == 401
        assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert (
            client.get("/health", headers={"Authorization": f"Bearer {'A' * 43}"}).status_code
            == 200
        )


def test_sensitive_audio_commands_require_the_session_credential() -> None:
    with TestClient(create_app(settings()), base_url="http://127.0.0.1") as client:
        assert client.post("/audio/listen").status_code == 401
        assert client.post("/audio/cancel").status_code == 401
        assert client.post("/shutdown").status_code == 401
        assert (
            client.post(
                "/audio/listen", headers={"Authorization": f"Bearer {'A' * 43}"}
            ).status_code
            == 200
        )


def test_websocket_rejects_unauthenticated_session() -> None:
    with TestClient(create_app(settings()), base_url="http://127.0.0.1") as client:
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            client.websocket_connect("/ws", headers={"host": "127.0.0.1"}),
        ):
            pass
        assert closed.value.code == 1008
