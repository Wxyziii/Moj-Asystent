from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from moj_asystent_core.api import CoreSettings, create_app
from moj_asystent_core.auth import SessionCredential
from moj_asystent_core.llm import (
    LanguageModelRequest,
    ModelStatus,
    ModelStreamEvent,
    ModelTextDelta,
)
from moj_asystent_core.memory import SQLiteMemoryStore
from moj_asystent_core.model_routing import (
    HardwareSnapshot,
    ModelCandidate,
    ModelRouter,
    ProviderCapabilities,
)
from moj_asystent_core.protocol import PROTOCOL_VERSION

TOKEN = "R" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Provider:
    model = "qwen3.5:4b"

    async def status(self) -> ModelStatus:
        return ModelStatus(model=self.model, state="ready")

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield ModelTextDelta(text="OK")

    async def close(self) -> None:
        pass


class Hardware:
    async def capture(self) -> HardwareSnapshot:
        return HardwareSnapshot(
            free_ram_bytes=24 * 1024**3,
            total_ram_bytes=32 * 1024**3,
            free_vram_bytes=7 * 1024**3,
            total_vram_bytes=8 * 1024**3,
            gpu_utilization_percent=5,
            high_load_active=False,
        )


def router() -> ModelRouter:
    return ModelRouter(
        (
            ModelCandidate(
                tier="fast",
                provider_name="ollama",
                provider=Provider(),
                local=True,
                capabilities=ProviderCapabilities(
                    text=True,
                    image=True,
                    structured_output=True,
                    tools=True,
                    context_size=32_768,
                ),
                approximate_size_bytes=3_650_722_099,
            ),
        ),
        Hardware(),
    )


def app(path: Path, selected_router: ModelRouter):
    return create_app(
        CoreSettings(credential=SessionCredential.from_value(TOKEN)),
        memory_store=SQLiteMemoryStore(path),
        model_router=selected_router,
    )


def test_model_mode_and_cloud_policy_are_authenticated_and_persisted(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with TestClient(app(path, router()), base_url="http://127.0.0.1") as client:
        assert client.get("/model/settings").status_code == 401
        assert client.get("/model/settings", headers=AUTH).json() == {
            "mode": "auto",
            "data_policy": "local_only",
            "effective_data_policy": "local_only",
        }
        changed = client.patch(
            "/model/settings",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "mode": "quality",
                "data_policy": "cloud_allowed",
            },
        )
        assert changed.status_code == 200
        assert changed.json()["mode"] == "quality"
        assert changed.json()["data_policy"] == "cloud_allowed"

    with TestClient(app(path, router()), base_url="http://127.0.0.1") as client:
        restored = client.get("/model/settings", headers=AUTH).json()
        assert restored["mode"] == "quality"
        assert restored["data_policy"] == "cloud_allowed"


def test_private_mode_forces_effective_local_policy_without_erasing_preference(
    tmp_path: Path,
) -> None:
    with TestClient(
        app(tmp_path / "memory.sqlite3", router()), base_url="http://127.0.0.1"
    ) as client:
        response = client.patch(
            "/model/settings",
            headers=AUTH,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "mode": "private",
                "data_policy": "cloud_allowed",
            },
        )

    assert response.json() == {
        "mode": "private",
        "data_policy": "cloud_allowed",
        "effective_data_policy": "local_only",
    }


def test_model_settings_reject_unknown_or_empty_values(tmp_path: Path) -> None:
    with TestClient(
        app(tmp_path / "memory.sqlite3", router()), base_url="http://127.0.0.1"
    ) as client:
        empty = client.patch(
            "/model/settings",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION},
        )
        unknown = client.patch(
            "/model/settings",
            headers=AUTH,
            json={"protocol_version": PROTOCOL_VERSION, "mode": "unlimited"},
        )

    assert empty.status_code == unknown.status_code == 422


def test_model_catalog_exposes_capabilities_without_secrets(tmp_path: Path) -> None:
    with TestClient(
        app(tmp_path / "memory.sqlite3", router()), base_url="http://127.0.0.1"
    ) as client:
        response = client.get("/models/catalog", headers=AUTH)

    assert response.status_code == 200
    model = response.json()["models"][0]
    assert model["model"] == "qwen3.5:4b"
    assert model["location"] == "local"
    assert model["capabilities"]["tools"] is True
    assert "key" not in response.text.casefold()
