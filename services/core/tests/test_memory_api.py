from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from moj_asystent_core.api import CoreSettings, create_app
from moj_asystent_core.auth import SessionCredential
from moj_asystent_core.llm import LanguageModelRequest, ModelStatus, ModelTextDelta
from moj_asystent_core.memory import SQLiteMemoryStore
from moj_asystent_core.protocol import PROTOCOL_VERSION

TOKEN = "M" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Provider:
    model = "test-memory"

    async def status(self) -> ModelStatus:
        return ModelStatus(model=self.model, state="ready")

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelTextDelta]:
        yield ModelTextDelta(text="OK")

    async def close(self) -> None:
        pass


def make_app(path: Path):
    store = SQLiteMemoryStore(path)
    settings = CoreSettings(credential=SessionCredential.from_value(TOKEN))
    return create_app(settings, Provider(), memory_store=store)


def test_memory_settings_records_aliases_and_clear_are_authenticated(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path / "memory.sqlite3"), base_url="http://127.0.0.1") as client:
        assert client.get("/memory/settings", headers=AUTH).json() == {"history_retention": False}
        enabled = client.patch(
            "/memory/settings",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "enabled": True},
        )
        assert enabled.status_code == 200
        memory = client.post(
            "/memories",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "key": "projekt",
                "value": "AimPeak",
            },
        )
        assert memory.status_code == 200
        alias = client.post(
            "/aliases",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "kind": "project",
                "alias": "aim",
                "target": r"C:\Projects\AimPeak",
            },
        )
        assert alias.status_code == 200
        assert len(client.get("/memories", headers=AUTH).json()["memories"]) == 1
        assert len(client.get("/aliases", headers=AUTH).json()["aliases"]) == 1
        assert (
            client.post(
                "/memory/clear",
                headers=AUTH,
                json={"protocol_version": PROTOCOL_VERSION, "confirm": False},
            ).status_code
            == 422
        )
        assert client.post(
            "/memory/clear",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "confirm": True},
        ).json() == {"removed": 2}

    with TestClient(make_app(tmp_path / "memory.sqlite3"), base_url="http://127.0.0.1") as client:
        assert client.get("/memories", headers=AUTH).json()["memories"] == []
        assert client.get("/memory/settings", headers=AUTH).json()["history_retention"] is True


def test_memory_api_rejects_secrets_and_routine_unknown_tools(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path / "memory.sqlite3"), base_url="http://127.0.0.1") as client:
        secret = client.post(
            "/memories",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "key": "api_key", "value": "x"},
        )
        assert secret.status_code == 422
        routine = client.post(
            "/routines",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "name": "Nieznana",
                "steps": [{"tool_name": "shell", "arguments": {}}],
            },
        )
        assert routine.status_code == 422


def test_routine_crud_round_trip_is_typed(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path / "memory.sqlite3"), base_url="http://127.0.0.1") as client:
        created = client.post(
            "/routines",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "name": "Sprawdź okno",
                "description": "Bezpieczny odczyt",
                "steps": [{"tool_name": "get_active_window", "arguments": {}}],
            },
        )
        assert created.status_code == 200
        routine_id = created.json()["routine_id"]
        listed = client.get("/routines", headers=AUTH)
        assert listed.status_code == 200
        assert listed.json()["routines"][0]["step_count"] == 1
        inspected = client.get(f"/routines/{routine_id}", headers=AUTH)
        assert inspected.json()["steps"][0]["tool_name"] == "get_active_window"
        renamed = client.patch(
            f"/routines/{routine_id}",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "name": "Sprawdź aktywne okno"},
        )
        assert renamed.json()["name"] == "Sprawdź aktywne okno"
        assert client.delete(f"/routines/{routine_id}", headers=AUTH).json() == {"removed": True}
