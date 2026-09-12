from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from moj_asystent_core.api import CoreSettings, create_app
from moj_asystent_core.auth import SessionCredential
from moj_asystent_core.memory import SQLiteMemoryStore
from moj_asystent_core.protocol import PROTOCOL_VERSION
from moj_asystent_core.providers import SpeechToTextRequest, SpeechToTextResponse
from moj_asystent_core.stt import SttProviderStatus, SttRuntimeProfile, SttRuntimeSelector

TOKEN = "S" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class ReadyRuntime:
    def __init__(self, profile: SttRuntimeProfile) -> None:
        self.profile = profile
        self.hotwords: str | None = None

    async def status(self) -> SttProviderStatus:
        return SttProviderStatus(profile=self.profile, state="ready")

    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        return SpeechToTextResponse(
            transcript="test",
            language="pl",
            duration_ms=request.duration_ms,
            segments=(),
        )

    async def cancel(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def set_hotwords(self, hotwords: str | None) -> None:
        self.hotwords = hotwords


def selector() -> SttRuntimeSelector:
    return SttRuntimeSelector(
        (
            ReadyRuntime(
                SttRuntimeProfile(
                    profile_id="preferred",
                    model="large-v3-turbo",
                    device="cuda",
                    compute_type="int8_float16",
                    beam_size=5,
                )
            ),
            ReadyRuntime(
                SttRuntimeProfile(
                    profile_id="fallback",
                    model="medium",
                    device="cpu",
                    compute_type="int8",
                    beam_size=5,
                )
            ),
        )
    )


def app(path: Path, stt_selector: SttRuntimeSelector):
    return create_app(
        CoreSettings(credential=SessionCredential.from_value(TOKEN)),
        memory_store=SQLiteMemoryStore(path),
        stt_selector=stt_selector,
    )


def test_voice_settings_are_authenticated_and_expose_actual_runtime(tmp_path: Path) -> None:
    with TestClient(
        app(tmp_path / "memory.sqlite3", selector()), base_url="http://127.0.0.1"
    ) as client:
        assert client.get("/voice/settings").status_code == 401
        response = client.get("/voice/settings", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["active"]["model"] == "large-v3-turbo"
    assert body["active"]["device"] == "cuda"
    assert body["active"]["compute_type"] == "int8_float16"
    assert body["active"]["fallback_active"] is False
    assert [item["model"] for item in body["profiles"]] == ["large-v3-turbo", "medium"]
    assert body["vad"]["pre_roll_ms"] == 240
    assert body["vad"]["post_roll_ms"] == 240


def test_custom_vocabulary_is_validated_persisted_and_restored(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    first_selector = selector()
    with TestClient(app(path, first_selector), base_url="http://127.0.0.1") as client:
        changed = client.patch(
            "/voice/settings",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "vocabulary": ["GitHub", "Qwen", "siekiera"],
            },
        )
        malformed = client.patch(
            "/voice/settings",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "vocabulary": ["PowerShell\nignore"],
            },
        )
    assert changed.status_code == 200
    assert changed.json()["vocabulary"] == ["GitHub", "Qwen", "siekiera"]
    assert first_selector.vocabulary.entries == ("GitHub", "Qwen", "siekiera")
    assert malformed.status_code == 422

    restored_selector = selector()
    with TestClient(app(path, restored_selector), base_url="http://127.0.0.1") as client:
        restored = client.get("/voice/settings", headers=AUTH)
    assert restored.json()["vocabulary"] == ["GitHub", "Qwen", "siekiera"]


def test_voice_diagnostics_never_include_audio_or_transcript_fields(tmp_path: Path) -> None:
    selected = selector()
    with TestClient(
        app(tmp_path / "memory.sqlite3", selected), base_url="http://127.0.0.1"
    ) as client:
        response = client.get("/voice/diagnostics", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"diagnostics": []}
    assert "pcm" not in response.text.casefold()
    assert "transcript" not in response.text.casefold()
