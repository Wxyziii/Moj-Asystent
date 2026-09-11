"""Request-scoped, bounded Windows desktop context orchestration."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

logger = logging.getLogger(__name__)
MAX_CONTEXT_SNAPSHOT_BYTES = 7_000


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextSource(StrEnum):
    WINDOWS_METADATA = "windows_metadata"
    UI_AUTOMATION = "ui_automation"
    USER = "user"
    SCREENSHOT = "screenshot"


class Bounds(ContextModel):
    left: int
    top: int
    right: int
    bottom: int

    @model_validator(mode="after")
    def ordered(self) -> Bounds:
        if self.right < self.left or self.bottom < self.top:
            raise ValueError("Bounds must be ordered")
        return self


class WindowIdentity(ContextModel):
    handle: Annotated[StrictInt, Field(gt=0)]
    pid: Annotated[StrictInt, Field(gt=0)]
    process_started_at: Annotated[float, Field(gt=0)] | None = None


UnavailableWindowField = Literal[
    "identity",
    "process_name",
    "executable",
    "process_started_at",
    "title",
    "window_class",
    "monitor",
    "bounds",
    "window",
]


class ActiveWindowSnapshot(ContextModel):
    available: bool
    identity: WindowIdentity | None = None
    process_name: Annotated[str, Field(max_length=260)] | None = None
    executable: Annotated[str, Field(max_length=1_024)] | None = None
    title: Annotated[str, Field(max_length=512)] | None = None
    window_class: Annotated[str, Field(max_length=256)] | None = None
    monitor: Annotated[str, Field(max_length=256)] | None = None
    bounds: Bounds | None = None
    is_foreground: bool = False
    unavailable_fields: tuple[UnavailableWindowField, ...] = ()
    reason: Annotated[str, Field(max_length=128)] | None = None
    provenance: Literal[ContextSource.WINDOWS_METADATA] = ContextSource.WINDOWS_METADATA

    @model_validator(mode="after")
    def require_identity_when_available(self) -> ActiveWindowSnapshot:
        if self.available and self.identity is None:
            raise ValueError("An available window requires an identity")
        return self


class ContextLimits(ContextModel):
    max_nodes: Annotated[StrictInt, Field(ge=1, le=512)] = 120
    max_depth: Annotated[StrictInt, Field(ge=0, le=16)] = 6
    max_text_length: Annotated[StrictInt, Field(ge=16, le=512)] = 320
    max_payload_bytes: Annotated[StrictInt, Field(ge=512, le=6_000)] = 4_800
    traversal_timeout_ms: Annotated[StrictInt, Field(ge=10, le=5_000)] = 1_500


class UiaNode(ContextModel):
    path: tuple[Annotated[StrictInt, Field(ge=0)], ...] = Field(max_length=16)
    control_type: Annotated[str, Field(min_length=1, max_length=128)]
    name: Annotated[str, Field(max_length=2_048)] | None = None
    automation_id: Annotated[str, Field(max_length=512)] | None = None
    enabled: bool | None = None
    focused: bool | None = None
    bounds: Bounds | None = None
    value: Annotated[str, Field(max_length=2_048)] | None = None
    text: Annotated[str, Field(max_length=2_048)] | None = None
    patterns: tuple[Annotated[str, Field(min_length=1, max_length=64)], ...] = Field(
        default=(), max_length=16
    )
    provenance: Literal[ContextSource.UI_AUTOMATION] = ContextSource.UI_AUTOMATION


TruncationReason = Literal["node_limit", "depth_limit", "text_limit", "payload_limit"]


class UiaTreeSnapshot(ContextModel):
    available: bool
    nodes: tuple[UiaNode, ...] = Field(max_length=512)
    node_count: Annotated[StrictInt, Field(ge=0, le=512)]
    skipped_nodes: Annotated[StrictInt, Field(ge=0)] = 0
    payload_bytes: Annotated[StrictInt, Field(ge=0, le=32_768)] = 0
    truncated: bool = False
    truncation_reasons: tuple[TruncationReason, ...] = ()
    reason: Annotated[str, Field(max_length=128)] | None = None
    provenance: Literal[ContextSource.UI_AUTOMATION] = ContextSource.UI_AUTOMATION


class ObservedText(ContextModel):
    available: bool
    value: Annotated[str, Field(max_length=2_048)] | None = None
    reason: (
        Literal[
            "selection_unavailable",
            "focused_control_unavailable",
            "protected_control",
            "context_excluded",
            "stale_window",
            "window_unavailable",
            "uia_unavailable",
        ]
        | None
    ) = None
    provenance: Literal[ContextSource.UI_AUTOMATION] = ContextSource.UI_AUTOMATION

    @model_validator(mode="after")
    def validate_availability(self) -> ObservedText:
        if self.available != (self.value is not None):
            raise ValueError("Observed text availability must match its value")
        if self.available == (self.reason is not None):
            raise ValueError("Observed text requires either a value or a reason")
        return self


class ScreenshotAvailability(ContextModel):
    available: Literal[False] = False
    reason: Literal["vision_not_implemented"] = "vision_not_implemented"
    planned_milestone: Literal[8] = 8
    provenance: Literal[ContextSource.SCREENSHOT] = ContextSource.SCREENSHOT


class ContextTimings(ContextModel):
    active_window_ms: Annotated[float, Field(ge=0)]
    uia_ms: Annotated[float, Field(ge=0)]
    total_ms: Annotated[float, Field(ge=0)]


class DesktopContextSnapshot(ContextModel):
    context_id: UUID
    captured_at: datetime
    available: bool
    blocked: bool = False
    stale: bool = False
    reason: Annotated[str, Field(max_length=128)] | None = None
    request_reason: Annotated[str, Field(min_length=1, max_length=256)]
    request_reason_provenance: Literal[ContextSource.USER] = ContextSource.USER
    active_window: ActiveWindowSnapshot
    focused_control: UiaNode | None = None
    selected_text: ObservedText
    ui_tree: UiaTreeSnapshot
    screenshot: ScreenshotAvailability = ScreenshotAvailability()
    timings: ContextTimings


class WindowMetadataProvider(Protocol):
    def capture(self) -> ActiveWindowSnapshot: ...


class AutomationElement(Protocol):
    def read_properties(self) -> Mapping[str, object]: ...

    def children(self) -> Iterable[AutomationElement]: ...


class UiaBackend(Protocol):
    def initialize(self) -> None: ...

    def element_from_handle(
        self, handle: int, cancel: threading.Event
    ) -> AutomationElement | None: ...

    def focused_element(self, cancel: threading.Event) -> AutomationElement | None: ...

    def close(self) -> None: ...


class UiaCaptureError(RuntimeError):
    """Expected, content-free UI Automation failure."""


class UiaCaptureTimeout(UiaCaptureError):
    pass


class UiaCaptureCancelled(UiaCaptureError):
    pass


class UiaProviderUnavailable(UiaCaptureError):
    pass


@dataclass(frozen=True)
class UiaInspection:
    tree: UiaTreeSnapshot
    focused_control: UiaNode | None
    selected_text: ObservedText
    elapsed_ms: float


def _safe_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _limited_text(value: object, limit: int) -> tuple[str | None, bool]:
    text = _safe_string(value)
    if text is None:
        return None, False
    if len(text) <= limit:
        return text, False
    return f"{text[: limit - 1]}…", True


def _safe_bounds(value: object) -> Bounds | None:
    if not isinstance(value, tuple | list) or len(value) != 4:
        return None
    if any(type(item) is not int for item in value):
        return None
    try:
        return Bounds(left=value[0], top=value[1], right=value[2], bottom=value[3])
    except ValueError:
        return None


class BoundedUiaCollector:
    """Traverse only useful UIA properties within deterministic resource limits."""

    def __init__(self, backend: UiaBackend) -> None:
        self._backend = backend

    def capture(
        self,
        identity: WindowIdentity,
        limits: ContextLimits,
        cancel: threading.Event,
    ) -> UiaInspection:
        started = time.monotonic()
        deadline = started + limits.traversal_timeout_ms / 1_000
        root = self._backend.element_from_handle(identity.handle, cancel)
        if root is None:
            raise UiaProviderUnavailable
        focused = self._backend.focused_element(cancel)
        focused_raw = self._read_properties(focused) if focused is not None else None
        if focused_raw is not None and focused_raw.get("process_id") != identity.pid:
            focused_raw = None
        focused_runtime_id = (
            _safe_string(focused_raw.get("runtime_id")) if focused_raw is not None else None
        )

        nodes: list[UiaNode] = []
        stack: list[tuple[AutomationElement, tuple[int, ...], int]] = [(root, (), 0)]
        skipped = 0
        payload_bytes = 0
        reasons: set[TruncationReason] = set()
        focused_node = (
            self._node(focused_raw, (), limits.max_text_length)[0]
            if focused_raw is not None
            else None
        )

        while stack:
            self._check(deadline, cancel)
            if len(nodes) >= limits.max_nodes:
                reasons.add("node_limit")
                break
            element, path, depth = stack.pop()
            raw = self._read_properties(element)
            if raw is None:
                skipped += 1
                continue
            node, text_limited = self._node(raw, path, limits.max_text_length)
            if text_limited:
                reasons.add("text_limit")
            encoded_size = len(node.model_dump_json().encode("utf-8"))
            if payload_bytes + encoded_size > limits.max_payload_bytes:
                reasons.add("payload_limit")
                break
            nodes.append(node)
            payload_bytes += encoded_size
            runtime_id = _safe_string(raw.get("runtime_id"))
            if (
                focused_runtime_id is not None and runtime_id == focused_runtime_id
            ) or node.focused is True:
                focused_node = node

            if depth >= limits.max_depth:
                reasons.add("depth_limit")
                continue
            try:
                remaining = limits.max_nodes - len(nodes)
                children = []
                for child in element.children():
                    self._check(deadline, cancel)
                    if len(children) >= remaining:
                        reasons.add("node_limit")
                        break
                    children.append(child)
            except Exception:
                skipped += 1
                continue
            for index in range(len(children) - 1, -1, -1):
                stack.append((children[index], (*path, index), depth + 1))

        selected = self._selected_text(focused_raw, limits.max_text_length)
        elapsed_ms = (time.monotonic() - started) * 1_000
        ordered_reasons = tuple(
            reason
            for reason in ("node_limit", "depth_limit", "text_limit", "payload_limit")
            if reason in reasons
        )
        return UiaInspection(
            tree=UiaTreeSnapshot(
                available=True,
                nodes=tuple(nodes),
                node_count=len(nodes),
                skipped_nodes=skipped,
                payload_bytes=payload_bytes,
                truncated=bool(ordered_reasons),
                truncation_reasons=ordered_reasons,
            ),
            focused_control=focused_node,
            selected_text=selected,
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def _read_properties(element: AutomationElement | None) -> Mapping[str, object] | None:
        if element is None:
            return None
        try:
            value = element.read_properties()
        except Exception:
            return None
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def _node(
        raw: Mapping[str, object], path: tuple[int, ...], text_limit: int
    ) -> tuple[UiaNode, bool]:
        protected = raw.get("is_password") is True
        control_type = _safe_string(raw.get("control_type")) or "Unknown"
        name, name_limited = _limited_text(raw.get("name"), text_limit)
        automation_id, automation_limited = _limited_text(raw.get("automation_id"), text_limit)
        value, value_limited = (
            (None, False) if protected else _limited_text(raw.get("value"), text_limit)
        )
        text, text_limited = (
            (None, False) if protected else _limited_text(raw.get("text"), text_limit)
        )
        raw_patterns = raw.get("patterns")
        patterns = (
            tuple(item[:64] for item in raw_patterns if isinstance(item, str) and item)[:16]
            if isinstance(raw_patterns, tuple | list)
            else ()
        )
        raw_enabled = raw.get("enabled")
        raw_focused = raw.get("focused")
        return (
            UiaNode(
                path=path,
                control_type=control_type[:128],
                name=name,
                automation_id=automation_id,
                enabled=raw_enabled if isinstance(raw_enabled, bool) else None,
                focused=raw_focused if isinstance(raw_focused, bool) else None,
                bounds=_safe_bounds(raw.get("bounds")),
                value=value,
                text=text,
                patterns=patterns,
            ),
            name_limited or automation_limited or value_limited or text_limited,
        )

    @staticmethod
    def _selected_text(raw: Mapping[str, object] | None, limit: int) -> ObservedText:
        if raw is None:
            return ObservedText(available=False, reason="focused_control_unavailable")
        if raw.get("is_password") is True:
            return ObservedText(available=False, reason="protected_control")
        selected, _ = _limited_text(raw.get("selected_text"), limit)
        if selected is None:
            return ObservedText(available=False, reason="selection_unavailable")
        return ObservedText(available=True, value=selected)

    @staticmethod
    def _check(deadline: float, cancel: threading.Event) -> None:
        if cancel.is_set():
            raise UiaCaptureCancelled
        if time.monotonic() >= deadline:
            raise UiaCaptureTimeout


@dataclass
class _WorkerRequest:
    identity: WindowIdentity
    limits: ContextLimits
    cancellation: threading.Event
    result: queue.Queue[UiaInspection | BaseException]


class UiaWorker:
    """One lazy, bounded MTA-owned worker for all UI Automation objects."""

    def __init__(self, backend_factory: Callable[[], UiaBackend], *, queue_size: int = 4) -> None:
        if not 1 <= queue_size <= 16:
            raise ValueError("UIA queue size must be between 1 and 16")
        self._backend_factory = backend_factory
        self._requests: queue.Queue[_WorkerRequest | None] = queue.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._active_cancellations: set[threading.Event] = set()
        self._ready = threading.Event()
        self._initialization_error: BaseException | None = None

    def capture(
        self,
        identity: WindowIdentity,
        limits: ContextLimits,
        *,
        cancellation: threading.Event | None = None,
    ) -> UiaInspection:
        external = cancellation
        internal = threading.Event()
        result: queue.Queue[UiaInspection | BaseException] = queue.Queue(maxsize=1)
        request = _WorkerRequest(identity, limits, internal, result)
        self._ensure_started()
        with self._lock:
            if self._closed:
                raise UiaProviderUnavailable
            self._active_cancellations.add(internal)
        try:
            self._requests.put(request, timeout=0.05)
        except queue.Full as error:
            with self._lock:
                self._active_cancellations.discard(internal)
            raise UiaProviderUnavailable from error

        deadline = time.monotonic() + limits.traversal_timeout_ms / 1_000
        while True:
            if external is not None and external.is_set():
                internal.set()
                raise UiaCaptureCancelled
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                internal.set()
                raise UiaCaptureTimeout
            try:
                outcome = result.get(timeout=min(remaining, 0.01))
            except queue.Empty:
                continue
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    def cancel_all(self) -> None:
        with self._lock:
            for cancellation in self._active_cancellations:
                cancellation.set()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for cancellation in self._active_cancellations:
                cancellation.set()
            thread = self._thread
        if thread is None:
            return
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                self._requests.put(None, timeout=0.05)
                break
            except queue.Full:
                continue
        thread.join(timeout=2.0)

    def _ensure_started(self) -> None:
        with self._lock:
            if self._closed:
                raise UiaProviderUnavailable
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="moj-asystent-uia",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(timeout=5.0) or self._initialization_error is not None:
            raise UiaProviderUnavailable

    def _run(self) -> None:
        backend: UiaBackend | None = None
        try:
            backend = self._backend_factory()
            backend.initialize()
        except BaseException:
            self._initialization_error = UiaProviderUnavailable()
        finally:
            self._ready.set()
        try:
            while True:
                request = self._requests.get()
                if request is None:
                    return
                try:
                    if self._initialization_error is not None or backend is None:
                        outcome: UiaInspection | BaseException = UiaProviderUnavailable()
                    elif request.cancellation.is_set():
                        outcome = UiaCaptureCancelled()
                    else:
                        outcome = BoundedUiaCollector(backend).capture(
                            request.identity, request.limits, request.cancellation
                        )
                except BaseException as error:
                    outcome = (
                        error if isinstance(error, UiaCaptureError) else UiaProviderUnavailable()
                    )
                finally:
                    with self._lock:
                        self._active_cancellations.discard(request.cancellation)
                with suppress(queue.Full):
                    request.result.put_nowait(outcome)
        finally:
            if backend is not None:
                with suppress(Exception):
                    backend.close()


class ContextExclusionPolicy(ContextModel):
    excluded_processes: frozenset[Annotated[str, Field(min_length=1, max_length=260)]] = frozenset()
    excluded_title_fragments: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = (
        Field(default=(), max_length=64)
    )

    def blocks(self, window: ActiveWindowSnapshot) -> bool:
        process = window.process_name.casefold() if window.process_name else None
        excluded = {item.casefold() for item in self.excluded_processes}
        if process is not None and process in excluded:
            return True
        title = window.title.casefold() if window.title else ""
        return any(fragment.casefold() in title for fragment in self.excluded_title_fragments)


DEFAULT_EXCLUDED_CONTEXT_PROCESSES = frozenset(
    {"1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe"}
)


class ContextSettings(ContextModel):
    enabled: bool = True
    limits: ContextLimits = ContextLimits()
    exclusions: ContextExclusionPolicy = ContextExclusionPolicy(
        excluded_processes=DEFAULT_EXCLUDED_CONTEXT_PROCESSES
    )

    @classmethod
    def from_environment(cls) -> ContextSettings:
        def integer(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError as error:
                raise ValueError(f"{name} must be an integer") from error

        enabled_raw = os.environ.get("MOJ_ASYSTENT_CONTEXT_ENABLED", "1").strip().casefold()
        if enabled_raw not in {"0", "1", "false", "true"}:
            raise ValueError("MOJ_ASYSTENT_CONTEXT_ENABLED must be true or false")
        apps = {
            item.strip()
            for item in os.environ.get("MOJ_ASYSTENT_CONTEXT_EXCLUDED_APPS", "").split(",")
            if item.strip()
        }
        titles = tuple(
            item.strip()
            for item in os.environ.get("MOJ_ASYSTENT_CONTEXT_EXCLUDED_TITLES", "").split("|")
            if item.strip()
        )
        return cls(
            enabled=enabled_raw in {"1", "true"},
            limits=ContextLimits(
                max_nodes=integer("MOJ_ASYSTENT_CONTEXT_MAX_NODES", 120),
                max_depth=integer("MOJ_ASYSTENT_CONTEXT_MAX_DEPTH", 6),
                max_text_length=integer("MOJ_ASYSTENT_CONTEXT_MAX_TEXT_LENGTH", 320),
                max_payload_bytes=integer("MOJ_ASYSTENT_CONTEXT_MAX_PAYLOAD_BYTES", 4_800),
                traversal_timeout_ms=integer("MOJ_ASYSTENT_CONTEXT_TIMEOUT_MS", 1_500),
            ),
            exclusions=ContextExclusionPolicy(
                excluded_processes=DEFAULT_EXCLUDED_CONTEXT_PROCESSES | apps,
                excluded_title_fragments=titles,
            ),
        )


def _empty_tree(reason: str) -> UiaTreeSnapshot:
    return UiaTreeSnapshot(
        available=False,
        nodes=(),
        node_count=0,
        payload_bytes=0,
        reason=reason,
    )


class WindowsContextService:
    def __init__(
        self,
        windows: WindowMetadataProvider,
        worker: UiaWorker,
        *,
        limits: ContextLimits | None = None,
        exclusions: ContextExclusionPolicy | None = None,
        enabled: bool = True,
    ) -> None:
        self._windows = windows
        self._worker = worker
        self._limits = limits or ContextLimits()
        self._exclusions = exclusions or ContextExclusionPolicy()
        self._enabled = enabled

    def active_window(self) -> ActiveWindowSnapshot:
        if not self._enabled:
            return ActiveWindowSnapshot(
                available=False,
                unavailable_fields=("window",),
                reason="context_disabled",
            )
        active = self._windows.capture()
        if active.available and self._exclusions.blocks(active):
            return active.model_copy(
                update={
                    "available": False,
                    "title": None,
                    "executable": None,
                    "bounds": None,
                    "reason": "context_excluded",
                }
            )
        return active

    def capture(self, reason: str) -> DesktopContextSnapshot:
        context_id = uuid4()
        captured_at = datetime.now(UTC)
        started = time.monotonic()
        if not self._enabled:
            active = self.active_window()
            return self._empty_snapshot(
                context_id,
                captured_at,
                reason,
                active,
                "context_disabled",
                0,
                started,
            )
        window_started = time.monotonic()
        active = self._windows.capture()
        active_ms = (time.monotonic() - window_started) * 1_000
        if not active.available or active.identity is None:
            return self._empty_snapshot(
                context_id,
                captured_at,
                reason,
                active,
                active.reason or "window_unavailable",
                active_ms,
                started,
            )
        if self._exclusions.blocks(active):
            redacted = active.model_copy(update={"title": None, "executable": None, "bounds": None})
            return self._empty_snapshot(
                context_id,
                captured_at,
                reason,
                redacted,
                "context_excluded",
                active_ms,
                started,
                blocked=True,
            )

        try:
            inspection = self._worker.capture(active.identity, self._limits)
        except UiaCaptureTimeout:
            return self._empty_snapshot(
                context_id,
                captured_at,
                reason,
                active,
                "uia_timeout",
                active_ms,
                started,
            )
        except UiaCaptureCancelled:
            return self._empty_snapshot(
                context_id,
                captured_at,
                reason,
                active,
                "uia_cancelled",
                active_ms,
                started,
            )
        except UiaCaptureError as error:
            logger.info("context_unavailable", extra={"error_class": type(error).__name__})
            return self._empty_snapshot(
                context_id,
                captured_at,
                reason,
                active,
                "uia_unavailable",
                active_ms,
                started,
            )

        current = self._windows.capture()
        if current.identity != active.identity or not current.is_foreground:
            return DesktopContextSnapshot(
                context_id=context_id,
                captured_at=captured_at,
                available=False,
                stale=True,
                reason="stale_window",
                request_reason=reason,
                active_window=active,
                selected_text=ObservedText(available=False, reason="stale_window"),
                ui_tree=_empty_tree("stale_window"),
                timings=ContextTimings(
                    active_window_ms=active_ms,
                    uia_ms=inspection.elapsed_ms,
                    total_ms=(time.monotonic() - started) * 1_000,
                ),
            )

        total_ms = (time.monotonic() - started) * 1_000
        logger.info(
            "context_collected",
            extra={
                "process_name": active.process_name,
                "node_count": inspection.tree.node_count,
                "truncated": inspection.tree.truncated,
                "elapsed_ms": round(total_ms, 1),
            },
        )
        snapshot = DesktopContextSnapshot(
            context_id=context_id,
            captured_at=captured_at,
            available=True,
            request_reason=reason,
            active_window=active,
            focused_control=inspection.focused_control,
            selected_text=inspection.selected_text,
            ui_tree=inspection.tree,
            timings=ContextTimings(
                active_window_ms=active_ms,
                uia_ms=inspection.elapsed_ms,
                total_ms=total_ms,
            ),
        )
        return self._fit_for_model(snapshot)

    def cancel(self) -> None:
        self._worker.cancel_all()

    def close(self) -> None:
        self._worker.close()

    @staticmethod
    def _fit_for_model(snapshot: DesktopContextSnapshot) -> DesktopContextSnapshot:
        nodes = list(snapshot.ui_tree.nodes)
        while nodes and context_json_size(snapshot) > MAX_CONTEXT_SNAPSHOT_BYTES:
            nodes.pop()
            reasons = tuple(dict.fromkeys((*snapshot.ui_tree.truncation_reasons, "payload_limit")))
            snapshot = snapshot.model_copy(
                update={
                    "ui_tree": snapshot.ui_tree.model_copy(
                        update={
                            "nodes": tuple(nodes),
                            "node_count": len(nodes),
                            "payload_bytes": sum(
                                len(node.model_dump_json().encode("utf-8")) for node in nodes
                            ),
                            "truncated": True,
                            "truncation_reasons": reasons,
                        }
                    )
                }
            )
        return snapshot

    @staticmethod
    def _empty_snapshot(
        context_id: UUID,
        captured_at: datetime,
        request_reason: str,
        active: ActiveWindowSnapshot,
        reason: str,
        active_ms: float,
        started: float,
        *,
        blocked: bool = False,
    ) -> DesktopContextSnapshot:
        selection_reason = (
            "context_excluded"
            if blocked
            else "window_unavailable"
            if not active.available
            else "uia_unavailable"
        )
        return DesktopContextSnapshot(
            context_id=context_id,
            captured_at=captured_at,
            available=False,
            blocked=blocked,
            reason=reason,
            request_reason=request_reason,
            active_window=active,
            selected_text=ObservedText(available=False, reason=selection_reason),
            ui_tree=_empty_tree(reason),
            timings=ContextTimings(
                active_window_ms=active_ms,
                uia_ms=0,
                total_ms=(time.monotonic() - started) * 1_000,
            ),
        )


def context_json_size(snapshot: DesktopContextSnapshot) -> int:
    """Measured serialized size used by tests and future provider minimization."""
    return len(json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False).encode("utf-8"))
