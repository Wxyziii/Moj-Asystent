import base64
from pathlib import Path

from fastapi.testclient import TestClient

from moj_asystent_core.api import CoreSettings, create_app
from moj_asystent_core.auth import SessionCredential

TOKEN = "W" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def app(root: Path):
    return create_app(
        CoreSettings(
            credential=SessionCredential.from_value(TOKEN),
            wake_data_root=root,
        )
    )


def test_training_controls_require_authentication_and_reject_extra_fields(tmp_path: Path) -> None:
    with TestClient(app(tmp_path), base_url="http://127.0.0.1") as client:
        assert client.get("/onboarding/status").status_code == 401
        assert client.post("/onboarding/sessions", json={"name": "Nora"}).status_code == 401
        malformed = client.post(
            "/onboarding/sessions",
            headers=AUTH,
            json={"protocol_version": "1.2", "name": "Nora", "model_path": "C:/outside/model.onnx"},
        )
    assert malformed.status_code == 422


def test_authenticated_session_returns_analysis_and_guided_curriculum(tmp_path: Path) -> None:
    with TestClient(app(tmp_path), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/onboarding/sessions",
            headers=AUTH,
            json={"protocol_version": "1.2", "name": "  Żorina  ", "microphone_device": 2},
        )
        status = client.get("/onboarding/status", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["name"]["display_name"] == "Żorina"
    assert body["curriculum"][0]["kind"] == "positive"
    assert body["curriculum"][0]["phrase"] == "Żorina"
    assert status.json() == {"completed": False, "active": None}


def test_audio_payload_is_validated_before_session_lookup(tmp_path: Path) -> None:
    with TestClient(app(tmp_path), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/onboarding/samples",
            headers=AUTH,
            json={
                "session_id": "4fa6ec1d-3379-4c4c-9451-93e515d91e12",
                "protocol_version": "1.2",
                "step_id": "../../escape",
                "sample_rate": 16_000,
                "pcm_s16le": base64.b64encode(b"\0\0" * 100).decode(),
            },
        )
    assert response.status_code == 422
