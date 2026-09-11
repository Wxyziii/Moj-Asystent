from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from moj_asystent_core.api import CoreSettings, create_app
from moj_asystent_core.auth import SessionCredential
from moj_asystent_core.protocol import PROTOCOL_VERSION
from moj_asystent_core.tools.platform import PathPolicy, WindowsToolPlatform
from moj_asystent_core.watchers import Observation

TOKEN = "W" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Provider:
    def observe(self, _watcher) -> Observation:
        return Observation("absent", {})


def test_watcher_api_requires_explicit_intent_and_supports_lifecycle(tmp_path: Path) -> None:
    platform = WindowsToolPlatform(path_policy=PathPolicy(roots=(tmp_path,)))
    app = create_app(
        CoreSettings(
            credential=SessionCredential.from_value(TOKEN),
            memory_database_path=tmp_path / "memory.sqlite3",
        ),
        tool_platform=platform,
        watcher_provider=Provider(),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert {
            "create_watcher",
            "list_watchers",
            "pause_watcher",
            "resume_watcher",
            "cancel_watcher",
            "delete_watcher",
        } <= {item.name for item in app.state.tool_engine.registry.definitions()}
        create_definition = app.state.tool_engine.registry.get("create_watcher")
        assert create_definition is not None
        assert create_definition.permission.value == "sensitive"
        assert create_definition.persistent_approval is False
        rejected = client.post(
            "/watchers",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "explicit_intent": False,
                "type": "download",
                "name": "Pobieranie",
                "target": {"path": str(tmp_path / "file.iso")},
                "condition": {"kind": "stable_size"},
            },
        )
        assert rejected.status_code == 422
        outside = client.post(
            "/watchers",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "explicit_intent": True,
                "type": "file",
                "name": "Poza korzeniem",
                "target": {"path": str(tmp_path.parent / "outside.txt")},
                "condition": {"kind": "created"},
            },
        )
        assert outside.status_code == 422
        created = client.post(
            "/watchers",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "explicit_intent": True,
                "type": "download",
                "name": "Pobieranie",
                "target": {"path": str(tmp_path / "file.iso")},
                "condition": {"kind": "stable_size", "stability_seconds": 2},
                "interval_seconds": 2,
            },
        )
        assert created.status_code == 200
        watcher_id = created.json()["watcher_id"]
        assert client.get("/watchers", headers=AUTH).json()["watchers"]
        assert (
            client.post(f"/watchers/{watcher_id}/pause", headers=AUTH).json()["status"] == "paused"
        )
        assert (
            client.post(f"/watchers/{watcher_id}/resume", headers=AUTH).json()["status"] == "active"
        )
        assert (
            client.post(f"/watchers/{watcher_id}/cancel", headers=AUTH).json()["status"]
            == "cancelled"
        )
        assert client.delete(f"/watchers/{watcher_id}", headers=AUTH).json() == {"removed": True}
