from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from moj_asystent_core.context import ActiveWindowSnapshot
from moj_asystent_core.memory import MemoryStoreCorruptError, SQLiteMemoryStore
from moj_asystent_core.protocol import WatcherNotificationPayload
from moj_asystent_core.telemetry import TelemetrySnapshot
from moj_asystent_core.tools.models import NoArguments
from moj_asystent_core.watchers import (
    Observation,
    WatcherCreateRequest,
    WatcherListOutput,
    WatcherService,
    WatcherToolBridge,
    WatcherValidationError,
)


class FakePaths:
    def watch_path(self, value: str) -> Path:
        return Path(value)


class FakePlatform:
    paths = FakePaths()

    def get_active_window(self) -> ActiveWindowSnapshot:
        raise AssertionError("not used by fake provider")

    def get_system_stats(self, _operation_id: UUID) -> TelemetrySnapshot:
        raise AssertionError("not used by fake provider")


class FakeProvider:
    def __init__(self, state: str = "absent") -> None:
        self.state = state

    def observe(self, _watcher) -> Observation:
        return Observation(self.state, {"value": self.state})


class ResourceProvider:
    def __init__(self, value: float) -> None:
        self.value = value

    def observe(self, _watcher) -> Observation:
        return Observation("sample", {"value": self.value})


class StateProvider:
    def __init__(self, state: str, details: dict | None = None) -> None:
        self.state = state
        self.details = details or {}

    def observe(self, _watcher) -> Observation:
        return Observation(self.state, dict(self.details))


class FailingProvider:
    def observe(self, _watcher) -> Observation:
        raise RuntimeError("provider unavailable")


def request(
    *, watcher_type: str = "process", condition: dict | None = None
) -> WatcherCreateRequest:
    return WatcherCreateRequest.model_validate(
        {
            "explicit_intent": True,
            "type": watcher_type,
            "name": "Testowa obserwacja",
            "target": {"process_name": "notepad.exe"},
            "condition": condition or {"kind": "started"},
            "interval_seconds": 3_600,
        }
    )


@pytest.mark.asyncio
async def test_watcher_requires_explicit_intent_and_notifies_once(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WatcherCreateRequest.model_validate(
            {
                "type": "process",
                "name": "Brak zgody",
                "target": {"process_name": "notepad.exe"},
                "condition": {"kind": "started"},
            }
        )

    provider = FakeProvider()
    notifications: list[WatcherNotificationPayload] = []
    service = WatcherService(
        SQLiteMemoryStore(tmp_path / "memory.sqlite3"),
        FakePlatform(),
        notifications.append,
        provider=provider,
    )
    watcher = await service.create(request())
    await asyncio.sleep(0.05)  # establish the initial absent baseline
    provider.state = "running"
    await service.poll_once(watcher.watcher_id)
    provider.state = "running"
    await service.poll_once(watcher.watcher_id)

    assert len(notifications) == 1
    assert notifications[0].event_type == "process.started"
    assert notifications[0].status == "completed"
    saved = await service.get(watcher.watcher_id)
    assert saved is not None and saved.status == "completed"
    assert len(await service.events(watcher.watcher_id)) == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_pause_resume_and_cancel_stop_scheduler(tmp_path: Path) -> None:
    provider = FakeProvider()
    service = WatcherService(
        SQLiteMemoryStore(tmp_path / "memory.sqlite3"),
        FakePlatform(),
        lambda _: None,
        provider=provider,
    )
    watcher = await service.create(request())
    paused = await service.pause(watcher.watcher_id)
    assert paused.status == "paused"
    resumed = await service.resume(watcher.watcher_id)
    assert resumed.status == "active"
    cancelled = await service.cancel(watcher.watcher_id)
    assert cancelled.status == "cancelled"
    with pytest.raises(WatcherValidationError):
        await service.resume(watcher.watcher_id)
    await service.shutdown()


@pytest.mark.asyncio
async def test_typed_watcher_tool_bridge_runs_service_on_core_loop(tmp_path: Path) -> None:
    service = WatcherService(
        SQLiteMemoryStore(tmp_path / "memory.sqlite3"),
        FakePlatform(),
        lambda _: None,
        provider=FakeProvider(),
    )
    watcher = await service.create(request())
    bridge = WatcherToolBridge(service)
    result = await asyncio.to_thread(bridge.list, NoArguments())
    assert isinstance(result, WatcherListOutput)
    assert result.watchers[0].watcher_id == watcher.watcher_id
    await service.shutdown()


@pytest.mark.asyncio
async def test_window_file_and_build_transitions_are_deterministic(tmp_path: Path) -> None:
    cases = (
        (
            "window",
            {"title": "Instalator"},
            {"kind": "closed"},
            StateProvider("present", {"title": "Instalator"}),
            "absent",
            {},
            "window.closed",
        ),
        (
            "file",
            {"path": str(tmp_path / "ready.txt")},
            {"kind": "created"},
            StateProvider("absent", {"path": str(tmp_path / "ready.txt")}),
            "present",
            {"path": str(tmp_path / "ready.txt"), "size_bytes": 4, "modified_ns": 2},
            "file.created",
        ),
        (
            "build",
            {"path": str(tmp_path / "build.log")},
            {"success_markers": ["BUILD OK"], "failure_markers": ["BUILD FAILED"]},
            StateProvider("pending"),
            "success",
            {"marker": "success"},
            "build.succeeded",
        ),
    )
    for index, (
        watcher_type,
        target,
        condition,
        provider,
        next_state,
        details,
        event_type,
    ) in enumerate(cases):
        notifications: list[WatcherNotificationPayload] = []
        service = WatcherService(
            SQLiteMemoryStore(tmp_path / f"memory-{index}.sqlite3"),
            FakePlatform(),
            notifications.append,
            provider=provider,
        )
        watcher = await service.create(
            WatcherCreateRequest.model_validate(
                {
                    "explicit_intent": True,
                    "type": watcher_type,
                    "name": f"Test {watcher_type}",
                    "target": target,
                    "condition": condition,
                    "interval_seconds": 3_600,
                }
            )
        )
        await asyncio.sleep(0.05)
        provider.state = next_state
        provider.details = details
        await service.poll_once(watcher.watcher_id)
        assert [item.event_type for item in notifications] == [event_type]
        await service.shutdown()


@pytest.mark.asyncio
async def test_startup_restores_active_watchers_and_expires_stale_ones(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    first = WatcherService(store, FakePlatform(), lambda _: None, provider=FakeProvider())
    watcher = await first.create(request())
    await asyncio.sleep(0.05)
    await first.shutdown()

    restored = WatcherService(store, FakePlatform(), lambda _: None, provider=FakeProvider())
    await restored.start()
    restored_record = await restored.get(watcher.watcher_id)
    assert restored_record is not None and restored_record.status == "active"
    await restored.shutdown()

    expired = watcher.model_copy(
        update={
            "watcher_id": UUID(int=9_999),
            "expires_at": datetime(2025, 1, 1, tzinfo=UTC),
            "status": "active",
        }
    )
    store.create_watcher(expired)
    expiring = WatcherService(store, FakePlatform(), lambda _: None, provider=FakeProvider())
    await expiring.start()
    saved = await expiring.get(expired.watcher_id)
    assert saved is not None and saved.status == "expired"
    await expiring.shutdown()


@pytest.mark.asyncio
async def test_provider_exception_fails_watcher_without_notification(tmp_path: Path) -> None:
    service = WatcherService(
        SQLiteMemoryStore(tmp_path / "memory.sqlite3"),
        FakePlatform(),
        lambda _: pytest.fail("provider failures must not notify"),
        provider=FailingProvider(),
    )
    watcher = await service.create(request())
    await asyncio.sleep(0.1)
    saved = await service.get(watcher.watcher_id)
    assert saved is not None and saved.status == "failed"
    await service.shutdown()


@pytest.mark.asyncio
async def test_active_watcher_limit_and_corrupt_rows_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    store = SQLiteMemoryStore(path)
    service = WatcherService(
        store,
        FakePlatform(),
        lambda _: None,
        provider=FakeProvider(),
        max_active=1,
    )
    await service.create(request())
    with pytest.raises(WatcherValidationError):
        await service.create(request())
    await service.shutdown()

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE watchers SET condition_json = ?",
            ("[]",),
        )
    with pytest.raises(MemoryStoreCorruptError):
        store.list_watchers()


@pytest.mark.asyncio
async def test_file_definition_is_canonicalized_and_history_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "artifact.iso"
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    service = WatcherService(store, FakePlatform(), lambda _: None, provider=FakeProvider())
    watcher = await service.create(
        WatcherCreateRequest.model_validate(
            {
                "explicit_intent": True,
                "type": "download",
                "name": "Pobieranie",
                "target": {"path": str(path)},
                "condition": {"kind": "stable_size", "stability_seconds": 2},
                "interval_seconds": 2,
            }
        )
    )
    assert watcher.target["path"] == str(path)
    for index in range(300):
        from moj_asystent_core.memory import WatcherEventRecord

        now = datetime.now(UTC)
        store.append_watcher_event(
            WatcherEventRecord(
                event_id=UUID(int=index + 1),
                watcher_id=watcher.watcher_id,
                occurred_at=now,
                event_type="test.event",
                payload={"index": index},
                notified=False,
                interpreted=False,
            ),
            limit=256,
        )
    assert len(store.list_watcher_events(limit=1_024)) == 256
    await service.shutdown()


@pytest.mark.asyncio
async def test_resource_threshold_requires_duration_and_hysteresis(tmp_path: Path) -> None:
    current = [datetime(2026, 1, 1, tzinfo=UTC)]
    provider = ResourceProvider(95)
    notifications: list[WatcherNotificationPayload] = []
    service = WatcherService(
        SQLiteMemoryStore(tmp_path / "memory.sqlite3"),
        FakePlatform(),
        notifications.append,
        provider=provider,
        clock=lambda: current[0],
    )
    watcher = await service.create(
        WatcherCreateRequest.model_validate(
            {
                "explicit_intent": True,
                "type": "resource",
                "name": "Wysoki RAM",
                "target": {},
                "condition": {
                    "metric": "memory_percent",
                    "threshold": 90,
                    "duration_seconds": 5,
                    "hysteresis": 5,
                },
                "interval_seconds": 10,
                "one_shot": False,
            }
        )
    )
    await asyncio.sleep(0.05)
    current[0] += timedelta(seconds=4)
    await service.poll_once(watcher.watcher_id)
    assert not notifications
    current[0] += timedelta(seconds=5)
    await service.poll_once(watcher.watcher_id)
    assert len(notifications) == 1
    await service.poll_once(watcher.watcher_id)
    assert len(notifications) == 1
    await service.shutdown()
