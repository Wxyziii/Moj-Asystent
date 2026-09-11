"""Deterministic, bounded proactive watchers for Milestone 11.

The watcher service compares small structured observations.  It never sends a
polling prompt to the model and it never executes a command as a consequence
of an observation.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
from collections.abc import Callable, Coroutine, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast
from uuid import UUID, uuid4

import psutil
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from .context import ActiveWindowSnapshot
from .memory import (
    MemoryStoreError,
    SQLiteMemoryStore,
    WatcherEventRecord,
    WatcherRecord,
    WatcherStatus,
    WatcherType,
)
from .protocol import WatcherNotificationPayload as ProtocolWatcherNotificationPayload
from .telemetry import TelemetrySnapshot
from .tools.platform import ToolPlatformError

MAX_ACTIVE_WATCHERS = 32
MAX_CONCURRENT_CHECKS = 4
MAX_EVENT_HISTORY = 256
MAX_BUILD_LOG_BYTES = 65_536
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
LiteralTrue = Literal[True]


class WatcherValidationError(ValueError):
    """A watcher request is not a safe, supported deterministic definition."""


class WatcherModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WatcherCreateRequest(WatcherModel):
    explicit_intent: LiteralTrue = Field(alias="explicit_intent")
    watcher_type: WatcherType = Field(alias="type")
    name: str = Field(min_length=1, max_length=96)
    target: dict[str, JsonValue]
    condition: dict[str, JsonValue]
    interval_seconds: StrictInt = Field(default=2, ge=1, le=3_600)
    expires_at: datetime | None = None
    one_shot: StrictBool = True
    notification_level: Literal["normal", "quiet"] = "normal"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = _clean_text(value, 96)
        if not cleaned:
            raise ValueError("Nazwa obserwacji nie może być pusta.")
        return cleaned

    @field_validator("expires_at")
    @classmethod
    def utc_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("Data wygaśnięcia musi być podana w UTC.")
        return value


class WatcherActionArguments(WatcherModel):
    watcher_id: UUID


class WatcherListOutput(WatcherModel):
    watchers: tuple[WatcherRecord, ...] = Field(max_length=128)


class WatcherDeleteOutput(WatcherModel):
    removed: bool


@dataclass(frozen=True)
class Observation:
    state: str
    details: dict[str, JsonValue]


class WatcherObservationProvider(Protocol):
    def observe(self, watcher: WatcherRecord, /) -> Observation: ...


class WatcherNotificationSink(Protocol):
    def __call__(self, payload: ProtocolWatcherNotificationPayload, /) -> None: ...


class WatcherPathPolicy(Protocol):
    def watch_path(self, value: str) -> Path: ...


class WatcherPlatform(Protocol):
    @property
    def paths(self) -> WatcherPathPolicy: ...

    def get_active_window(self) -> ActiveWindowSnapshot: ...

    def get_system_stats(self, operation_id: UUID, /) -> TelemetrySnapshot: ...


class LocalWatcherObservationProvider:
    """Small local adapters; all expensive/native calls remain request bounded."""

    def __init__(self, platform: WatcherPlatform) -> None:
        self._platform = platform

    def observe(self, watcher: WatcherRecord) -> Observation:
        if watcher.watcher_type == "window":
            return self._window(watcher)
        if watcher.watcher_type == "process":
            return self._process(watcher)
        if watcher.watcher_type in ("file", "download"):
            return self._file(watcher)
        if watcher.watcher_type == "resource":
            return self._resource(watcher)
        if watcher.watcher_type == "build":
            return self._build(watcher)
        raise WatcherValidationError("Nieobsługiwany typ obserwacji.")

    def _window(self, watcher: WatcherRecord) -> Observation:
        target = watcher.target
        active = self._platform.get_active_window()
        if not active.available or active.identity is None:
            return Observation("absent", {"available": False})
        identity = active.identity
        expected_pid = _int(target.get("pid"))
        expected_handle = _int(target.get("handle"))
        expected_started = _number(target.get("process_started_at"))
        expected_title = _safe_observation_text(target.get("title"))
        expected_process = _safe_observation_text(target.get("process_name"))
        if expected_pid and identity.pid != expected_pid:
            return Observation("identity_changed", {"pid": identity.pid})
        if expected_handle and identity.handle != expected_handle:
            return Observation("absent", {"handle": identity.handle})
        if expected_started and (
            identity.process_started_at is None
            or abs(identity.process_started_at - expected_started) > 0.01
        ):
            return Observation(
                "identity_changed", {"process_started_at": identity.process_started_at}
            )
        if expected_title and _safe_observation_text(active.title) != expected_title:
            return Observation("absent", {"title": _safe_observation_text(active.title)})
        observed_process = _safe_observation_text(active.process_name)
        if expected_process and (
            observed_process is None or observed_process.casefold() != expected_process.casefold()
        ):
            return Observation(
                "absent", {"process_name": _safe_observation_text(active.process_name)}
            )
        return Observation(
            "present",
            {
                "handle": identity.handle,
                "pid": identity.pid,
                "process_started_at": identity.process_started_at,
                "title": _safe_observation_text(active.title),
                "foreground": True,
            },
        )

    def _process(self, watcher: WatcherRecord) -> Observation:
        target = watcher.target
        pid = _int(target.get("pid"))
        expected_started = _number(target.get("process_started_at"))
        expected_name = _safe_observation_text(target.get("process_name"))
        for process in psutil.process_iter(["pid", "name", "create_time"]):
            try:
                if pid is not None and process.pid != pid:
                    continue
                info = process.info
                name = info.get("name")
                if expected_name and (
                    not isinstance(name, str) or name.casefold() != expected_name.casefold()
                ):
                    if pid is not None:
                        return Observation(
                            "identity_changed",
                            {"pid": process.pid, "name": _safe_observation_text(name)},
                        )
                    continue
                create_time = info.get("create_time")
                created = float(create_time) if isinstance(create_time, int | float) else None
                if expected_started is not None and (
                    created is None or abs(created - expected_started) > 0.01
                ):
                    return Observation("identity_changed", {"pid": process.pid})
                return Observation(
                    "running",
                    {
                        "pid": process.pid,
                        "name": _safe_observation_text(name),
                        "process_started_at": created,
                    },
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        return Observation("absent", {"pid": pid, "process_name": expected_name})

    def _file(self, watcher: WatcherRecord) -> Observation:
        path = Path(str(watcher.target["path"]))
        try:
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                return Observation("unsafe", {})
            stat = path.stat()
        except FileNotFoundError:
            return Observation("absent", {"path": str(path)})
        except OSError:
            return Observation("unavailable", {"path": str(path)})
        if not path.is_file():
            return Observation("absent", {"path": str(path)})
        return Observation(
            "present",
            {
                "path": str(path),
                "size_bytes": max(0, int(stat.st_size)),
                "modified_ns": int(stat.st_mtime_ns),
            },
        )

    def _resource(self, watcher: WatcherRecord) -> Observation:
        snapshot = self._platform.get_system_stats(UUID(int=0))
        metric = str(watcher.condition["metric"])
        value = _telemetry_metric(snapshot, metric)
        if value is None:
            return Observation("unavailable", {"metric": metric})
        return Observation("sample", {"metric": metric, "value": round(value, 2)})

    def _build(self, watcher: WatcherRecord) -> Observation:
        path = Path(str(watcher.target["path"]))
        try:
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                return Observation("unsafe", {})
            with path.open("rb") as source:
                raw = source.read(MAX_BUILD_LOG_BYTES)
        except FileNotFoundError:
            return Observation("pending", {})
        except OSError:
            return Observation("unavailable", {})
        text = raw.decode("utf-8", errors="replace")
        success = _marker_list(watcher.condition.get("success_markers"))
        failure = _marker_list(watcher.condition.get("failure_markers"))
        if any(marker in text for marker in failure):
            return Observation("failure", {"marker": "failure"})
        if any(marker in text for marker in success):
            return Observation("success", {"marker": "success"})
        return Observation("pending", {})


class WatcherService:
    """Single bounded asyncio scheduler owning all watcher tasks."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        platform: WatcherPlatform,
        notify: WatcherNotificationSink,
        *,
        provider: WatcherObservationProvider | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_active: int = MAX_ACTIVE_WATCHERS,
    ) -> None:
        if not 1 <= max_active <= MAX_ACTIVE_WATCHERS:
            raise ValueError("Limit aktywnych obserwacji jest poza zakresem.")
        self._store = store
        self._platform = platform
        self._provider = provider or LocalWatcherObservationProvider(platform)
        self._notify = notify
        self._clock = clock
        self._max_active = max_active
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._stable_since: dict[UUID, datetime] = {}
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
        self._state_lock = asyncio.Lock()
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        records = await asyncio.to_thread(self._store.list_watchers)
        for record in records:
            if record.status != "active":
                continue
            try:
                self._validate_record(record)
            except WatcherValidationError:
                await self._set_status(record, "failed")
                continue
            if record.expires_at and record.expires_at <= self._clock():
                await self._set_status(record, "expired")
                continue
            self._start_task(record.watcher_id)

    async def shutdown(self) -> None:
        self._stopping = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._stable_since.clear()

    async def create(self, request: WatcherCreateRequest) -> WatcherRecord:
        if request.explicit_intent is not True:
            raise WatcherValidationError("Obserwacja wymaga jawnej prośby użytkownika.")
        if (
            sum(record.status in ("active", "paused") for record in await self._list())
            >= self._max_active
        ):
            raise WatcherValidationError("Osiągnięto limit aktywnych obserwacji.")
        target, condition = self._validate_definition(
            request.watcher_type, request.target, request.condition
        )
        now = self._clock()
        record = WatcherRecord(
            watcher_id=uuid4(),
            watcher_type=request.watcher_type,
            status="active",
            name=request.name,
            target=target,
            condition=condition,
            interval_seconds=request.interval_seconds,
            expires_at=request.expires_at,
            one_shot=request.one_shot,
            notification_level=request.notification_level,
            created_at=now,
            updated_at=now,
        )
        await asyncio.to_thread(self._store.create_watcher, record)
        self._start_task(record.watcher_id)
        return record

    async def list(self) -> tuple[WatcherRecord, ...]:
        return await self._list()

    async def get(self, watcher_id: UUID) -> WatcherRecord | None:
        return await asyncio.to_thread(self._store.get_watcher, watcher_id)

    async def pause(self, watcher_id: UUID) -> WatcherRecord:
        return await self._change_status(watcher_id, "paused")

    async def resume(self, watcher_id: UUID) -> WatcherRecord:
        async with self._state_lock:
            record = await self._require(watcher_id)
            if record.status not in ("paused", "active"):
                raise WatcherValidationError("Zakończonej obserwacji nie można wznowić.")
            if record.expires_at and record.expires_at <= self._clock():
                return await self._set_status(record, "expired")
            updated = await self._replace(record, status="active")
            self._start_task(watcher_id)
            return updated

    async def cancel(self, watcher_id: UUID) -> WatcherRecord:
        return await self._change_status(watcher_id, "cancelled")

    async def delete(self, watcher_id: UUID) -> bool:
        async with self._state_lock:
            task = self._tasks.pop(watcher_id, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            return await asyncio.to_thread(self._store.delete_watcher, watcher_id)

    async def events(
        self, watcher_id: UUID | None = None, *, limit: int = 128
    ) -> tuple[WatcherEventRecord, ...]:
        return await asyncio.to_thread(self._store.list_watcher_events, watcher_id, limit=limit)

    async def clear_events(self, watcher_id: UUID | None = None) -> int:
        return await asyncio.to_thread(self._store.clear_watcher_events, watcher_id)

    async def poll_once(self, watcher_id: UUID) -> WatcherRecord:
        record = await self._require(watcher_id)
        if record.status != "active":
            return record
        return await self._poll(record)

    async def _list(self) -> tuple[WatcherRecord, ...]:
        return await asyncio.to_thread(self._store.list_watchers)

    def _start_task(self, watcher_id: UUID) -> None:
        if self._stopping or watcher_id in self._tasks:
            return
        task = asyncio.create_task(self._run(watcher_id), name=f"watcher-{watcher_id}")
        self._tasks[watcher_id] = task

        def remove_finished(completed: asyncio.Task[None]) -> None:
            # A cancelled task may finish after a replacement task has been
            # scheduled.  Never let the stale callback remove that replacement.
            if self._tasks.get(watcher_id) is completed:
                self._tasks.pop(watcher_id, None)

        task.add_done_callback(remove_finished)

    async def _run(self, watcher_id: UUID) -> None:
        try:
            while not self._stopping:
                record = await self._require(watcher_id)
                if record.status != "active":
                    return
                updated = await self._poll(record)
                if updated.status != "active":
                    return
                await asyncio.sleep(updated.interval_seconds)
        except asyncio.CancelledError:
            raise
        except (MemoryStoreError, WatcherValidationError):
            with suppress(Exception):
                record = await self._require(watcher_id)
                await self._set_status(record, "failed")

    async def _poll(self, record: WatcherRecord) -> WatcherRecord:
        async with self._state_lock:
            now = self._clock()
            if record.expires_at and record.expires_at <= now:
                return await self._set_status(record, "expired")
            try:
                async with _semaphore_guard(self._semaphore):
                    observation = await asyncio.to_thread(self._provider.observe, record)
            except asyncio.CancelledError:
                raise
            except Exception:
                return await self._set_status(record, "failed")
            previous = record.last_observed
            observed_details = dict(observation.details)
            next_event_type = record.last_event_type
            if record.watcher_type == "resource" and observation.state == "sample":
                value = _as_float(observation.details.get("value"))
                threshold = _as_float(record.condition.get("threshold"))
                direction = str(record.condition.get("direction", "above"))
                active = value >= threshold if direction == "above" else value <= threshold
                if active:
                    observed_details["threshold_since"] = (
                        previous.get("threshold_since")
                        if previous and previous.get("threshold_since")
                        else now.isoformat()
                    )
                elif record.last_event_type == "resource.threshold":
                    next_event_type = None
            elif (
                record.watcher_type == "download" and record.last_event_type == "download.completed"
            ):
                if (
                    previous is None
                    or observation.state != "present"
                    or previous.get("size_bytes") != observation.details.get("size_bytes")
                    or previous.get("modified_ns") != observation.details.get("modified_ns")
                ):
                    next_event_type = None
            current = Observation(observation.state, observed_details)
            event = self._meaningful_event(record, previous, current, now)
            updated = await self._replace(
                record,
                last_observed={
                    **observed_details,
                    "state": observation.state,
                    "observed_at": now.isoformat(),
                },
                last_event_type=event[0] if event else next_event_type,
            )
            if event is not None:
                if observation.state in {"unsafe", "identity_changed"}:
                    updated = await self._set_status(updated, "failed")
                elif record.one_shot and updated.status == "active":
                    updated = await self._set_status(updated, "completed")
                await self._emit(record, updated, event[0], event[1], now)
            return updated

    async def _emit(
        self,
        record: WatcherRecord,
        updated: WatcherRecord,
        event_type: str,
        message: str,
        now: datetime,
    ) -> None:
        event_id = uuid4()
        payload = {
            "message": message,
            "state": str(updated.last_observed.get("state", "")) if updated.last_observed else "",
        }
        history = WatcherEventRecord(
            event_id=event_id,
            watcher_id=record.watcher_id,
            occurred_at=now,
            event_type=event_type,
            payload=payload,
            notified=record.notification_level == "normal",
            interpreted=False,
        )
        await asyncio.to_thread(self._store.append_watcher_event, history, limit=MAX_EVENT_HISTORY)
        if record.notification_level == "quiet":
            return
        self._notify(
            ProtocolWatcherNotificationPayload(
                watcher_id=record.watcher_id,
                notification_id=event_id,
                watcher_type=record.watcher_type,
                event_type=event_type,
                title=record.name,
                message=message,
                target=_display_target(record.target),
                status=updated.status,
                actions=("inspect", "later", "stop"),
            )
        )

    async def _change_status(self, watcher_id: UUID, status: WatcherStatus) -> WatcherRecord:
        async with self._state_lock:
            record = await self._require(watcher_id)
            if record.status in ("completed", "failed", "cancelled", "expired"):
                if record.status != status:
                    raise WatcherValidationError("Obserwacja jest już zakończona.")
                return record
            updated = await self._set_status(record, status)
            if status != "active":
                task = self._tasks.pop(watcher_id, None)
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            elif status == "active":
                self._start_task(watcher_id)
            return updated

    async def _set_status(self, record: WatcherRecord, status: WatcherStatus) -> WatcherRecord:
        return await self._replace(record, status=status)

    async def _replace(self, record: WatcherRecord, **changes: object) -> WatcherRecord:
        updated = record.model_copy(update={"updated_at": self._clock(), **changes})
        await asyncio.to_thread(self._store.update_watcher, updated)
        return updated

    async def _require(self, watcher_id: UUID) -> WatcherRecord:
        record = await self.get(watcher_id)
        if record is None:
            raise WatcherValidationError("Obserwacja nie istnieje.")
        return record

    def _validate_record(self, record: WatcherRecord) -> None:
        self._validate_definition(record.watcher_type, record.target, record.condition)

    def _validate_definition(
        self,
        watcher_type: WatcherType,
        target: Mapping[str, JsonValue],
        condition: Mapping[str, JsonValue],
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        target_out = dict(target)
        condition_out = dict(condition)
        allowed_targets = {
            "window": {"handle", "pid", "process_started_at", "title", "process_name"},
            "process": {"pid", "process_started_at", "process_name"},
            "file": {"path"},
            "download": {"path"},
            "build": {"path"},
            "resource": set(),
        }[watcher_type]
        if set(target_out) - allowed_targets:
            raise WatcherValidationError("Cel obserwacji zawiera nieobsługiwane pola.")
        if len(condition_out) > 12:
            raise WatcherValidationError("Warunek obserwacji zawiera zbyt wiele pól.")
        allowed_conditions = {
            "window": {"kind"},
            "process": {"kind"},
            "file": {"kind", "stability_seconds"},
            "download": {"kind", "stability_seconds"},
            "resource": {"metric", "threshold", "direction", "duration_seconds", "hysteresis"},
            "build": {"success_markers", "failure_markers"},
        }[watcher_type]
        if set(condition_out) - allowed_conditions:
            raise WatcherValidationError("Warunek obserwacji zawiera nieobsługiwane pola.")
        if watcher_type in ("file", "download", "build"):
            path = target_out.get("path")
            if not isinstance(path, str):
                raise WatcherValidationError("Obserwacja pliku wymaga ścieżki.")
            try:
                target_out["path"] = str(self._platform.paths.watch_path(path))
            except ToolPlatformError as error:
                raise WatcherValidationError(str(error)) from error
        if watcher_type == "window":
            if not any(key in target_out for key in ("handle", "pid", "title")):
                raise WatcherValidationError("Okno wymaga uchwytu, PID albo tytułu.")
            _bounded_target_text(target_out)
            if any(
                key in target_out and _int(target_out[key]) is None for key in ("handle", "pid")
            ):
                raise WatcherValidationError("Tożsamość okna ma nieprawidłowy identyfikator.")
            if (
                "process_started_at" in target_out
                and _number(target_out["process_started_at"]) is None
            ):
                raise WatcherValidationError("Czas uruchomienia procesu jest nieprawidłowy.")
            if "pid" in target_out and "process_started_at" not in target_out:
                raise WatcherValidationError("PID okna wymaga czasu uruchomienia procesu.")
            if condition_out.get("kind", "closed") not in {
                "closed",
                "appeared",
                "title_changed",
                "foreground",
            }:
                raise WatcherValidationError("Warunek okna jest nieobsługiwany.")
        elif watcher_type == "process":
            if "pid" not in target_out and "process_name" not in target_out:
                raise WatcherValidationError("Proces wymaga PID albo nazwy.")
            if "pid" in target_out and "process_started_at" not in target_out:
                raise WatcherValidationError("PID procesu wymaga czasu uruchomienia.")
            if "pid" in target_out and _int(target_out["pid"]) is None:
                raise WatcherValidationError("PID procesu jest nieprawidłowy.")
            if (
                "process_started_at" in target_out
                and _number(target_out["process_started_at"]) is None
            ):
                raise WatcherValidationError("Czas uruchomienia procesu jest nieprawidłowy.")
            _bounded_target_text(target_out)
            if condition_out.get("kind", "exited") not in {"started", "exited", "active"}:
                raise WatcherValidationError("Warunek procesu jest nieobsługiwany.")
        elif watcher_type == "file":
            if condition_out.get("kind", "modified") not in {
                "created",
                "modified",
                "deleted",
                "stable",
            }:
                raise WatcherValidationError("Warunek pliku jest nieobsługiwany.")
            _validate_stability(condition_out)
        elif watcher_type == "download":
            if condition_out.get("kind", "stable_size") != "stable_size":
                raise WatcherValidationError(
                    "Warunek pobierania musi dotyczyć stabilnego rozmiaru."
                )
            _validate_stability(condition_out)
        elif watcher_type == "resource":
            if condition_out.get("metric") not in {
                "cpu_percent",
                "memory_percent",
                "gpu_utilization_percent",
                "vram_percent",
                "gpu_temperature_celsius",
            }:
                raise WatcherValidationError("Metryka zasobów jest nieobsługiwana.")
            threshold = condition_out.get("threshold")
            if (
                not isinstance(threshold, int | float)
                or isinstance(threshold, bool)
                or not 0 <= float(threshold) <= 200
            ):
                raise WatcherValidationError("Próg zasobów jest poza zakresem.")
            if condition_out.get("direction", "above") not in {"above", "below"}:
                raise WatcherValidationError("Kierunek progu jest nieobsługiwany.")
            duration = condition_out.get("duration_seconds", 1)
            if (
                not isinstance(duration, int | float)
                or isinstance(duration, bool)
                or not 0 <= float(duration) <= 3_600
            ):
                raise WatcherValidationError("Czas progu jest poza zakresem.")
            hysteresis = condition_out.get("hysteresis", 5)
            if (
                not isinstance(hysteresis, int | float)
                or isinstance(hysteresis, bool)
                or not 0 <= float(hysteresis) <= 50
            ):
                raise WatcherValidationError("Histereza jest poza zakresem.")
        elif watcher_type == "build":
            if not isinstance(condition_out.get("success_markers"), list) or not isinstance(
                condition_out.get("failure_markers"), list
            ):
                raise WatcherValidationError("Build wymaga list markerów sukcesu i błędu.")
            if not condition_out["success_markers"] and not condition_out["failure_markers"]:
                raise WatcherValidationError("Build wymaga przynajmniej jednego markera.")
            success_markers = _marker_list(condition_out.get("success_markers"))
            failure_markers = _marker_list(condition_out.get("failure_markers"))
            if not success_markers and not failure_markers:
                raise WatcherValidationError("Build wymaga przynajmniej jednego markera.")
            condition_out["success_markers"] = list(success_markers)
            condition_out["failure_markers"] = list(failure_markers)
        return target_out, condition_out

    def _meaningful_event(
        self,
        record: WatcherRecord,
        previous: dict[str, JsonValue] | None,
        current: Observation,
        now: datetime,
    ) -> tuple[str, str] | None:
        if current.state in {"unsafe", "identity_changed"}:
            return (
                "identity_changed",
                "Tożsamość obserwowanego celu zmieniła się — obserwację zatrzymano.",
            )
        old_state = str(previous.get("state")) if previous else None
        kind = str(record.condition.get("kind", ""))
        if previous is None:
            if record.watcher_type == "resource":
                return None
            return None
        if record.watcher_type == "window":
            if kind == "closed" and old_state == "present" and current.state == "absent":
                return ("window.closed", "Obserwowane okno zostało zamknięte.")
            if kind == "appeared" and old_state == "absent" and current.state == "present":
                return ("window.appeared", "Obserwowane okno pojawiło się.")
            if kind == "foreground" and old_state != "present" and current.state == "present":
                return ("window.foreground", "Obserwowane okno stało się aktywne.")
            if (
                kind == "title_changed"
                and old_state == "present"
                and current.state == "present"
                and previous.get("title") != current.details.get("title")
            ):
                return ("window.title_changed", "Tytuł obserwowanego okna zmienił się.")
        elif record.watcher_type == "process":
            if kind == "started" and old_state == "absent" and current.state == "running":
                return ("process.started", "Obserwowany proces został uruchomiony.")
            if kind == "exited" and old_state == "running" and current.state == "absent":
                return ("process.exited", "Obserwowany proces zakończył działanie.")
            if kind == "active" and old_state != "running" and current.state == "running":
                return ("process.active", "Obserwowany proces jest aktywny.")
        elif record.watcher_type == "file":
            if kind == "created" and old_state == "absent" and current.state == "present":
                return ("file.created", "Obserwowany plik został utworzony.")
            if kind == "deleted" and old_state == "present" and current.state == "absent":
                return ("file.deleted", "Obserwowany plik został usunięty.")
            if (
                kind == "modified"
                and old_state == "present"
                and current.state == "present"
                and previous.get("modified_ns") != current.details.get("modified_ns")
            ):
                return ("file.modified", "Obserwowany plik został zmieniony.")
            if (
                kind == "stable"
                and old_state == "present"
                and current.state == "present"
                and previous.get("size_bytes") == current.details.get("size_bytes")
                and previous.get("modified_ns") == current.details.get("modified_ns")
                and record.last_event_type != "file.stable"
                and _elapsed_since(previous.get("observed_at"), now)
                >= _as_float(record.condition.get("stability_seconds"), 3)
            ):
                return ("file.stable", "Rozmiar obserwowanego pliku jest stabilny.")
        elif record.watcher_type == "download":
            if (
                old_state == "present"
                and current.state == "present"
                and previous.get("size_bytes") == current.details.get("size_bytes")
                and previous.get("modified_ns") == current.details.get("modified_ns")
                and record.last_event_type != "download.completed"
            ):
                stable_for = _elapsed_since(previous.get("observed_at"), now)
                if stable_for >= _as_float(record.condition.get("stability_seconds"), 3):
                    return (
                        "download.completed",
                        "Pobieranie zakończyło się — plik ma stabilny rozmiar.",
                    )
        elif record.watcher_type == "build":
            if old_state == "pending" and current.state == "success":
                return ("build.succeeded", "Build zakończył się powodzeniem.")
            if old_state == "pending" and current.state == "failure":
                return ("build.failed", "Build zakończył się błędem.")
        elif record.watcher_type == "resource" and current.state == "sample":
            value = _as_float(current.details.get("value"))
            threshold = _as_float(record.condition.get("threshold"))
            direction = str(record.condition.get("direction", "above"))
            hysteresis = _as_float(record.condition.get("hysteresis"), 5)
            active = value >= threshold if direction == "above" else value <= threshold
            clear = (
                value <= threshold - hysteresis
                if direction == "above"
                else value >= threshold + hysteresis
            )
            was_active = previous.get("threshold_since") is not None
            if clear:
                was_active = False
            if active and not was_active:
                started = _elapsed_since(previous.get("threshold_since"), now)
                if started >= _as_float(record.condition.get("duration_seconds"), 1):
                    return (
                        "resource.threshold",
                        f"{record.condition['metric']} przekroczył ustawiony próg.",
                    )
                return None
            if active and previous.get("threshold_since") is not None:
                started = _elapsed_since(previous.get("threshold_since"), now)
                if (
                    started >= _as_float(record.condition.get("duration_seconds"), 1)
                    and record.last_event_type != "resource.threshold"
                ):
                    return (
                        "resource.threshold",
                        f"{record.condition['metric']} przekroczył ustawiony próg.",
                    )
        return None


_WatcherResult = TypeVar("_WatcherResult")


class WatcherToolBridge:
    """Synchronous adapter for the existing worker-thread ToolEngine.

    Tool implementations run in a bounded worker thread.  The watcher service
    remains owned by the core event loop, so this bridge schedules its coroutine
    there and waits with the same finite timeout as other typed tools.
    """

    def __init__(self, service: WatcherService, *, timeout_seconds: float = 4.5) -> None:
        self._service = service
        self._loop = asyncio.get_running_loop()
        self._timeout_seconds = timeout_seconds

    def create(self, value: BaseModel) -> WatcherRecord:
        return self._run(self._service.create(cast(WatcherCreateRequest, value)))

    def list(self, _: BaseModel) -> WatcherListOutput:
        return WatcherListOutput(watchers=self._run(self._service.list()))

    def pause(self, value: BaseModel) -> WatcherRecord:
        arguments = cast(WatcherActionArguments, value)
        return self._run(self._service.pause(arguments.watcher_id))

    def resume(self, value: BaseModel) -> WatcherRecord:
        arguments = cast(WatcherActionArguments, value)
        return self._run(self._service.resume(arguments.watcher_id))

    def cancel(self, value: BaseModel) -> WatcherRecord:
        arguments = cast(WatcherActionArguments, value)
        return self._run(self._service.cancel(arguments.watcher_id))

    def delete(self, value: BaseModel) -> WatcherDeleteOutput:
        arguments = cast(WatcherActionArguments, value)
        return WatcherDeleteOutput(removed=self._run(self._service.delete(arguments.watcher_id)))

    def _run(self, operation: Coroutine[Any, Any, _WatcherResult]) -> _WatcherResult:
        if self._loop.is_closed():
            raise RuntimeError("Rdzeń obserwacji jest zamknięty.")
        future = asyncio.run_coroutine_threadsafe(operation, self._loop)
        try:
            return future.result(timeout=self._timeout_seconds)
        except concurrent.futures.TimeoutError as error:
            future.cancel()
            raise RuntimeError("Operacja obserwacji przekroczyła limit czasu.") from error


class _semaphore_guard:
    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()

    async def __aexit__(self, *_: object) -> None:
        self._semaphore.release()


def _clean_text(value: str, maximum: int) -> str:
    return _CONTROL_CHARS.sub(" ", value).strip()[:maximum]


def _safe_observation_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _clean_text(value, 256)
    return cleaned or None


def _bounded_target_text(target: Mapping[str, JsonValue]) -> None:
    for key in ("title", "process_name"):
        value = target.get(key)
        if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 256):
            raise WatcherValidationError("Cel obserwacji ma nieprawidłowy tekst.")


def _validate_stability(condition: dict[str, JsonValue]) -> None:
    value = condition.get("stability_seconds", 3)
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not 1 <= float(value) <= 3_600
    ):
        raise WatcherValidationError("Okno stabilności jest poza zakresem.")
    condition["stability_seconds"] = int(value)


def _marker_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WatcherValidationError("Markery muszą być listą.")
    result: list[str] = []
    for marker in value:
        if not isinstance(marker, str) or not 1 <= len(marker) <= 128:
            raise WatcherValidationError("Marker builda ma nieprawidłową długość.")
        result.append(_clean_text(marker, 128))
    return tuple(result)


def _as_float(value: object, default: float = 0.0) -> float:
    return (
        float(value) if isinstance(value, int | float) and not isinstance(value, bool) else default
    )


def _int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _number(value: object) -> float | None:
    return (
        float(value)
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0
        else None
    )


def _elapsed_since(value: object, now: datetime) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        then = datetime.fromisoformat(value).astimezone(UTC)
        return max(0.0, (now - then).total_seconds())
    except ValueError:
        return 0.0


def _telemetry_metric(snapshot: TelemetrySnapshot, metric: str) -> float | None:
    if metric == "cpu_percent":
        return snapshot.cpu.percent
    if metric == "memory_percent":
        return snapshot.memory.percent
    if metric == "gpu_temperature_celsius":
        return snapshot.gpus[0].temperature_celsius if snapshot.gpus else None
    if metric == "gpu_utilization_percent":
        return snapshot.gpus[0].utilization_percent if snapshot.gpus else None
    if metric == "vram_percent":
        if not snapshot.gpus or snapshot.gpus[0].memory_total_bytes <= 0:
            return None
        gpu = snapshot.gpus[0]
        return gpu.memory_used_bytes / gpu.memory_total_bytes * 100
    return None


def _display_target(target: Mapping[str, JsonValue]) -> str:
    if isinstance(target.get("path"), str):
        return str(target["path"])[:1_024]
    if isinstance(target.get("process_name"), str):
        return str(target["process_name"])[:256]
    if target.get("pid") is not None:
        return f"PID {target['pid']}"
    if target.get("title") is not None:
        return str(target["title"])[:256]
    return "lokalny cel"
