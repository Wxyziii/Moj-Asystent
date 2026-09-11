"""Bounded, memory-only screenshot capture and visual-context lifecycle."""

from __future__ import annotations

import io
import logging
import os
import queue
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from PIL import Image
from pydantic import Field, StrictInt, model_validator

from .context import (
    ActiveWindowSnapshot,
    Bounds,
    ContextExclusionPolicy,
    ContextModel,
    ContextSource,
    WindowIdentity,
    WindowMetadataProvider,
)

logger = logging.getLogger(__name__)


class VisionLimits(ContextModel):
    max_capture_width: Annotated[StrictInt, Field(ge=320, le=8_192)] = 4_096
    max_capture_height: Annotated[StrictInt, Field(ge=240, le=8_192)] = 4_096
    max_capture_pixels: Annotated[StrictInt, Field(ge=76_800, le=24_000_000)] = 12_000_000
    max_normalized_dimension: Annotated[StrictInt, Field(ge=256, le=2_048)] = 1_280
    max_normalized_pixels: Annotated[StrictInt, Field(ge=65_536, le=4_000_000)] = 1_500_000
    max_encoded_image_bytes: Annotated[StrictInt, Field(ge=32_768, le=2_000_000)] = 1_500_000
    max_images_per_request: Literal[1] = 1
    capture_timeout_ms: Annotated[StrictInt, Field(ge=50, le=10_000)] = 2_000
    preview_dimension: Annotated[StrictInt, Field(ge=96, le=512)] = 320
    max_preview_bytes: Annotated[StrictInt, Field(ge=8_192, le=196_608)] = 96_000


class VisionSettings(ContextModel):
    limits: VisionLimits = VisionLimits()

    @classmethod
    def from_environment(cls) -> VisionSettings:
        defaults = VisionLimits()

        def integer(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError as error:
                raise ValueError(f"{name} must be an integer") from error

        return cls(
            limits=VisionLimits(
                max_capture_width=integer(
                    "MOJ_ASYSTENT_VISION_MAX_CAPTURE_WIDTH", defaults.max_capture_width
                ),
                max_capture_height=integer(
                    "MOJ_ASYSTENT_VISION_MAX_CAPTURE_HEIGHT", defaults.max_capture_height
                ),
                max_capture_pixels=integer(
                    "MOJ_ASYSTENT_VISION_MAX_CAPTURE_PIXELS", defaults.max_capture_pixels
                ),
                max_normalized_dimension=integer(
                    "MOJ_ASYSTENT_VISION_MAX_NORMALIZED_DIMENSION",
                    defaults.max_normalized_dimension,
                ),
                max_normalized_pixels=integer(
                    "MOJ_ASYSTENT_VISION_MAX_NORMALIZED_PIXELS",
                    defaults.max_normalized_pixels,
                ),
                max_encoded_image_bytes=integer(
                    "MOJ_ASYSTENT_VISION_MAX_IMAGE_BYTES",
                    defaults.max_encoded_image_bytes,
                ),
                capture_timeout_ms=integer(
                    "MOJ_ASYSTENT_VISION_CAPTURE_TIMEOUT_MS", defaults.capture_timeout_ms
                ),
            )
        )


class VisionCaptureError(RuntimeError):
    """Content-free expected capture error."""


class VisionCaptureTimeout(VisionCaptureError):
    pass


class VisionCaptureCancelled(VisionCaptureError):
    pass


class VisionCaptureInvalid(VisionCaptureError):
    pass


class VisionCaptureUnavailable(VisionCaptureError):
    pass


class RawScreenCapture:
    """Owned BGRA buffer returned by a screenshot backend."""

    __slots__ = ("width", "height", "pixels", "cleared", "_unsafe")

    def __init__(
        self, width: int, height: int, pixels: bytearray, *, _unsafe: bool = False
    ) -> None:
        self.width = width
        self.height = height
        self.pixels = pixels
        self.cleared = False
        self._unsafe = _unsafe
        if not _unsafe:
            self.validate()

    @classmethod
    def unsafe_for_test(cls, width: int, height: int, pixels: bytearray) -> RawScreenCapture:
        return cls(width, height, pixels, _unsafe=True)

    def validate(self) -> None:
        if (
            type(self.width) is not int
            or type(self.height) is not int
            or self.width <= 0
            or self.height <= 0
            or len(self.pixels) != self.width * self.height * 4
        ):
            raise VisionCaptureInvalid

    def clear(self) -> None:
        if self.cleared:
            return
        self.pixels[:] = b"\0" * len(self.pixels)
        self.pixels.clear()
        self.cleared = True


class VisionImage:
    """Short-lived encoded image with explicit best-effort zeroization."""

    __slots__ = (
        "capture_id",
        "context_id",
        "operation_id",
        "media_type",
        "width",
        "height",
        "_data",
        "cleared",
    )

    def __init__(
        self,
        *,
        capture_id: UUID,
        context_id: UUID,
        operation_id: UUID,
        width: int,
        height: int,
        jpeg: bytearray,
    ) -> None:
        if (
            width <= 0
            or height <= 0
            or not jpeg.startswith(b"\xff\xd8")
            or not jpeg.endswith(b"\xff\xd9")
        ):
            raise VisionCaptureInvalid
        self.capture_id = capture_id
        self.context_id = context_id
        self.operation_id = operation_id
        self.media_type: Literal["image/jpeg"] = "image/jpeg"
        self.width = width
        self.height = height
        self._data = jpeg
        self.cleared = False

    @property
    def size_bytes(self) -> int:
        return len(self._data)

    def bytes_for_provider(self) -> bytes:
        if self.cleared or not self._data:
            raise VisionCaptureInvalid
        return bytes(self._data)

    def clear(self) -> None:
        if self.cleared:
            return
        self._data[:] = b"\0" * len(self._data)
        self._data.clear()
        self.cleared = True

    def __repr__(self) -> str:
        return (
            f"VisionImage(capture_id={self.capture_id!r}, width={self.width}, "
            f"height={self.height}, size_bytes={self.size_bytes}, cleared={self.cleared})"
        )


VisionUnavailableReason = Literal[
    "context_disabled",
    "context_excluded",
    "window_unavailable",
    "window_bounds_unavailable",
    "capture_too_large",
    "capture_timeout",
    "capture_cancelled",
    "capture_invalid",
    "capture_unavailable",
    "stale_window",
]


class VisionInspectionResult(ContextModel):
    available: bool
    context_id: UUID
    operation_id: UUID
    capture_id: UUID | None = None
    captured_at: datetime
    source: Literal["active_window", "screen_region"]
    provenance: Literal[ContextSource.SCREENSHOT] = ContextSource.SCREENSHOT
    window_identity: WindowIdentity | None = None
    capture_bounds: Bounds | None = None
    monitor: Annotated[str, Field(max_length=256)] | None = None
    dpi_scale: Annotated[float, Field(ge=0.5, le=4.0)] | None = None
    captured_width: Annotated[StrictInt, Field(gt=0, le=8_192)] | None = None
    captured_height: Annotated[StrictInt, Field(gt=0, le=8_192)] | None = None
    normalized_width: Annotated[StrictInt, Field(gt=0, le=2_048)] | None = None
    normalized_height: Annotated[StrictInt, Field(gt=0, le=2_048)] | None = None
    encoded_image_bytes: Annotated[StrictInt, Field(gt=0, le=2_000_000)] | None = None
    capture_ms: Annotated[float, Field(ge=0)] = 0
    normalization_ms: Annotated[float, Field(ge=0)] = 0
    vision_interpretation: Literal["pending", "unavailable"] = "unavailable"
    blocked: bool = False
    stale: bool = False
    reason: VisionUnavailableReason | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> VisionInspectionResult:
        required = (
            self.capture_id,
            self.window_identity,
            self.capture_bounds,
            self.captured_width,
            self.captured_height,
            self.normalized_width,
            self.normalized_height,
            self.encoded_image_bytes,
        )
        if self.available and (any(value is None for value in required) or self.reason is not None):
            raise ValueError("Available vision result requires complete capture metadata")
        if not self.available and self.reason is None:
            raise ValueError("Unavailable vision result requires a reason")
        if self.available != (self.vision_interpretation == "pending"):
            raise ValueError("Vision interpretation state does not match capture availability")
        return self


@dataclass(slots=True)
class VisionCaptureOutcome:
    result: VisionInspectionResult
    image: VisionImage | None = field(default=None, repr=False)
    preview: bytearray | None = field(default=None, repr=False)

    def clear(self) -> None:
        if self.image is not None:
            self.image.clear()
        if self.preview is not None:
            self.preview[:] = b"\0" * len(self.preview)
            self.preview.clear()
            self.preview = None


@dataclass(slots=True)
class _NormalizedCapture:
    original_width: int
    original_height: int
    width: int
    height: int
    jpeg: bytearray = field(repr=False)
    preview: bytearray = field(repr=False)
    capture_ms: float
    normalization_ms: float

    def clear(self) -> None:
        self.jpeg[:] = b"\0" * len(self.jpeg)
        self.jpeg.clear()
        self.preview[:] = b"\0" * len(self.preview)
        self.preview.clear()


class ScreenshotBackend(Protocol):
    def initialize(self) -> None: ...

    def capture(self, bounds: Bounds, cancel: threading.Event) -> RawScreenCapture: ...

    def close(self) -> None: ...


class MssScreenshotBackend:
    """Memory-only MSS backend; native objects stay on the screenshot worker."""

    def __init__(self) -> None:
        self._session = None

    def initialize(self) -> None:
        if sys.platform != "win32":
            raise VisionCaptureUnavailable
        import mss

        self._session = mss.mss()

    def capture(self, bounds: Bounds, cancel: threading.Event) -> RawScreenCapture:
        if cancel.is_set():
            raise VisionCaptureCancelled
        if self._session is None:
            raise VisionCaptureUnavailable
        width = bounds.right - bounds.left
        height = bounds.bottom - bounds.top
        shot = self._session.grab(
            {"left": bounds.left, "top": bounds.top, "width": width, "height": height}
        )
        pixels = bytearray(shot.bgra)
        if cancel.is_set():
            pixels[:] = b"\0" * len(pixels)
            pixels.clear()
            raise VisionCaptureCancelled
        return RawScreenCapture(shot.width, shot.height, pixels)

    def close(self) -> None:
        if self._session is not None:
            with suppress(Exception):
                self._session.close()
            self._session = None


def _scaled_size(width: int, height: int, limits: VisionLimits) -> tuple[int, int]:
    scale = min(
        1.0,
        limits.max_normalized_dimension / max(width, height),
        (limits.max_normalized_pixels / (width * height)) ** 0.5,
    )
    return max(1, round(width * scale)), max(1, round(height * scale))


def _encode_bounded(image: Image.Image, limit: int) -> tuple[bytearray, Image.Image]:
    candidate = image
    for _ in range(6):
        for quality in (84, 76, 68, 60, 52, 44):
            output = io.BytesIO()
            candidate.save(
                output,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=False,
            )
            encoded = output.getvalue()
            if len(encoded) <= limit:
                return bytearray(encoded), candidate
        next_size = (
            max(64, round(candidate.width * 0.82)),
            max(64, round(candidate.height * 0.82)),
        )
        if next_size == candidate.size:
            break
        candidate = candidate.resize(next_size, Image.Resampling.LANCZOS)
    raise VisionCaptureInvalid


def _normalize(
    raw: RawScreenCapture, limits: VisionLimits, cancel: threading.Event
) -> _NormalizedCapture:
    started = time.monotonic()
    try:
        raw.validate()
        if cancel.is_set():
            raise VisionCaptureCancelled
        if (
            raw.width > limits.max_capture_width
            or raw.height > limits.max_capture_height
            or raw.width * raw.height > limits.max_capture_pixels
        ):
            raise VisionCaptureInvalid
        image = Image.frombytes(
            "RGBA", (raw.width, raw.height), bytes(raw.pixels), "raw", "BGRA"
        ).convert("RGB")
        normalized_size = _scaled_size(raw.width, raw.height, limits)
        if normalized_size != image.size:
            image = image.resize(normalized_size, Image.Resampling.LANCZOS)
        if cancel.is_set():
            raise VisionCaptureCancelled
        jpeg, encoded_image = _encode_bounded(image, limits.max_encoded_image_bytes)
        preview = encoded_image.copy()
        preview.thumbnail(
            (limits.preview_dimension, limits.preview_dimension), Image.Resampling.LANCZOS
        )
        preview_jpeg, _ = _encode_bounded(preview, limits.max_preview_bytes)
        return _NormalizedCapture(
            original_width=raw.width,
            original_height=raw.height,
            width=encoded_image.width,
            height=encoded_image.height,
            jpeg=jpeg,
            preview=preview_jpeg,
            capture_ms=0,
            normalization_ms=(time.monotonic() - started) * 1_000,
        )
    finally:
        raw.clear()


@dataclass(slots=True)
class _ScreenshotRequest:
    bounds: Bounds
    limits: VisionLimits
    cancellation: threading.Event
    abandoned: threading.Event
    result: queue.Queue[_NormalizedCapture | BaseException]


class ScreenshotWorker:
    """One bounded screenshot/normalization worker with explicit shutdown."""

    def __init__(
        self, backend_factory: Callable[[], ScreenshotBackend], *, queue_size: int = 2
    ) -> None:
        if not 1 <= queue_size <= 8:
            raise ValueError("Screenshot queue size must be between 1 and 8")
        self._backend_factory = backend_factory
        self._requests: queue.Queue[_ScreenshotRequest | None] = queue.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._active: set[threading.Event] = set()
        self._ready = threading.Event()
        self._initialization_error = False

    def capture(
        self,
        bounds: Bounds,
        limits: VisionLimits,
        *,
        cancellation: threading.Event | None = None,
    ) -> _NormalizedCapture:
        self._ensure_started()
        internal = threading.Event()
        abandoned = threading.Event()
        result: queue.Queue[_NormalizedCapture | BaseException] = queue.Queue(maxsize=1)
        request = _ScreenshotRequest(bounds, limits, internal, abandoned, result)
        with self._lock:
            if self._closed:
                raise VisionCaptureUnavailable
            self._active.add(internal)
        try:
            self._requests.put(request, timeout=0.05)
        except queue.Full as error:
            with self._lock:
                self._active.discard(internal)
            raise VisionCaptureUnavailable from error

        deadline = time.monotonic() + limits.capture_timeout_ms / 1_000
        while True:
            if cancellation is not None and cancellation.is_set():
                internal.set()
                abandoned.set()
                raise VisionCaptureCancelled
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                internal.set()
                abandoned.set()
                raise VisionCaptureTimeout
            try:
                outcome = result.get(timeout=min(remaining, 0.01))
            except queue.Empty:
                continue
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    def cancel_all(self) -> None:
        with self._lock:
            for cancellation in self._active:
                cancellation.set()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for cancellation in self._active:
                cancellation.set()
            thread = self._thread
        if thread is None:
            return
        while True:
            try:
                queued = self._requests.get_nowait()
            except queue.Empty:
                break
            if queued is not None:
                queued.cancellation.set()
                queued.abandoned.set()
                with suppress(queue.Full):
                    queued.result.put_nowait(VisionCaptureCancelled())
        with suppress(queue.Full):
            self._requests.put(None, timeout=2.0)
        thread.join(timeout=2.0)

    def _ensure_started(self) -> None:
        with self._lock:
            if self._closed:
                raise VisionCaptureUnavailable
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="moj-asystent-screenshot",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(timeout=5) or self._initialization_error:
            raise VisionCaptureUnavailable

    def _run(self) -> None:
        backend: ScreenshotBackend | None = None
        try:
            backend = self._backend_factory()
            backend.initialize()
        except BaseException:
            self._initialization_error = True
        finally:
            self._ready.set()
        try:
            while True:
                request = self._requests.get()
                if request is None:
                    return
                started = time.monotonic()
                try:
                    if self._initialization_error or backend is None:
                        outcome: _NormalizedCapture | BaseException = VisionCaptureUnavailable()
                    elif request.cancellation.is_set():
                        outcome = VisionCaptureCancelled()
                    else:
                        raw = backend.capture(request.bounds, request.cancellation)
                        normalized = _normalize(raw, request.limits, request.cancellation)
                        normalized.capture_ms = (
                            time.monotonic() - started
                        ) * 1_000 - normalized.normalization_ms
                        outcome = normalized
                except BaseException as error:
                    outcome = (
                        error if isinstance(error, VisionCaptureError) else VisionCaptureInvalid()
                    )
                finally:
                    with self._lock:
                        self._active.discard(request.cancellation)
                if request.abandoned.is_set():
                    if isinstance(outcome, _NormalizedCapture):
                        outcome.clear()
                    continue
                with suppress(queue.Full):
                    request.result.put_nowait(outcome)
        finally:
            if backend is not None:
                with suppress(Exception):
                    backend.close()


class VisionCaptureService:
    def __init__(
        self,
        windows: WindowMetadataProvider,
        worker: ScreenshotWorker,
        *,
        limits: VisionLimits | None = None,
        exclusions: ContextExclusionPolicy | None = None,
        enabled: bool = True,
    ) -> None:
        self._windows = windows
        self._worker = worker
        self._limits = limits or VisionLimits()
        self._exclusions = exclusions or ContextExclusionPolicy()
        self._enabled = enabled

    def capture_active(self, reason: str, operation_id: UUID) -> VisionCaptureOutcome:
        active = self._windows.capture()
        if not self._enabled:
            return self._unavailable(reason, operation_id, "active_window", "context_disabled")
        if not active.available or active.identity is None:
            return self._unavailable(reason, operation_id, "active_window", "window_unavailable")
        if self._exclusions.blocks(active):
            return self._unavailable(
                reason,
                operation_id,
                "active_window",
                "context_excluded",
                blocked=True,
            )
        if active.bounds is None:
            return self._unavailable(
                reason,
                operation_id,
                "active_window",
                "window_bounds_unavailable",
                active=active,
            )
        return self._capture(reason, operation_id, "active_window", active.bounds, active, None)

    def capture_region(
        self,
        reason: str,
        operation_id: UUID,
        *,
        region: Bounds,
        monitor_bounds: Bounds,
        dpi_scale: float,
    ) -> VisionCaptureOutcome:
        if not 0.5 <= dpi_scale <= 4.0:
            raise ValueError("DPI scale is outside the supported range")
        if region.right <= region.left or region.bottom <= region.top:
            raise ValueError("Region must have positive area")
        if (
            region.left < monitor_bounds.left
            or region.top < monitor_bounds.top
            or region.right > monitor_bounds.right
            or region.bottom > monitor_bounds.bottom
        ):
            raise ValueError("Region must stay inside one selected monitor")
        if not self._enabled:
            return self._unavailable(reason, operation_id, "screen_region", "context_disabled")
        active = self._windows.capture()
        if not active.available or active.identity is None:
            return self._unavailable(reason, operation_id, "screen_region", "window_unavailable")
        if self._exclusions.blocks(active):
            return self._unavailable(
                reason,
                operation_id,
                "screen_region",
                "context_excluded",
                blocked=True,
            )
        if active.bounds is None:
            return self._unavailable(
                reason,
                operation_id,
                "screen_region",
                "window_bounds_unavailable",
                active=active,
            )
        if (
            region.left < active.bounds.left
            or region.top < active.bounds.top
            or region.right > active.bounds.right
            or region.bottom > active.bounds.bottom
        ):
            raise ValueError("Region must stay inside the active window")
        return self._capture(reason, operation_id, "screen_region", region, active, dpi_scale)

    def _capture(
        self,
        reason: str,
        operation_id: UUID,
        source: Literal["active_window", "screen_region"],
        bounds: Bounds,
        active: ActiveWindowSnapshot,
        dpi_scale: float | None,
    ) -> VisionCaptureOutcome:
        width = bounds.right - bounds.left
        height = bounds.bottom - bounds.top
        if (
            width <= 0
            or height <= 0
            or width > self._limits.max_capture_width
            or height > self._limits.max_capture_height
            or width * height > self._limits.max_capture_pixels
        ):
            return self._unavailable(
                reason,
                operation_id,
                source,
                "capture_too_large",
                active=active,
            )
        try:
            normalized = self._worker.capture(bounds, self._limits)
        except VisionCaptureTimeout:
            return self._unavailable(reason, operation_id, source, "capture_timeout", active=active)
        except VisionCaptureCancelled:
            return self._unavailable(
                reason, operation_id, source, "capture_cancelled", active=active
            )
        except VisionCaptureInvalid:
            return self._unavailable(reason, operation_id, source, "capture_invalid", active=active)
        except VisionCaptureError:
            return self._unavailable(
                reason, operation_id, source, "capture_unavailable", active=active
            )

        current = self._windows.capture()
        if (
            current.identity != active.identity
            or not current.is_foreground
            or self._exclusions.blocks(current)
        ):
            normalized.clear()
            return self._unavailable(
                reason,
                operation_id,
                source,
                "stale_window",
                active=active,
                stale=True,
            )

        context_id = uuid4()
        capture_id = uuid4()
        image = VisionImage(
            capture_id=capture_id,
            context_id=context_id,
            operation_id=operation_id,
            width=normalized.width,
            height=normalized.height,
            jpeg=normalized.jpeg,
        )
        result = VisionInspectionResult(
            available=True,
            context_id=context_id,
            operation_id=operation_id,
            capture_id=capture_id,
            captured_at=datetime.now(UTC),
            source=source,
            window_identity=active.identity,
            capture_bounds=bounds,
            monitor=active.monitor,
            dpi_scale=dpi_scale,
            captured_width=normalized.original_width,
            captured_height=normalized.original_height,
            normalized_width=normalized.width,
            normalized_height=normalized.height,
            encoded_image_bytes=image.size_bytes,
            capture_ms=max(0, normalized.capture_ms),
            normalization_ms=normalized.normalization_ms,
            vision_interpretation="pending",
        )
        logger.info(
            "screenshot_captured",
            extra={
                "process_name": active.process_name,
                "source": source,
                "captured_width": normalized.original_width,
                "captured_height": normalized.original_height,
                "normalized_width": normalized.width,
                "normalized_height": normalized.height,
                "encoded_bytes": image.size_bytes,
                "capture_ms": round(result.capture_ms, 1),
                "normalization_ms": round(result.normalization_ms, 1),
            },
        )
        return VisionCaptureOutcome(result=result, image=image, preview=normalized.preview)

    @staticmethod
    def _unavailable(
        request_reason: str,
        operation_id: UUID,
        source: Literal["active_window", "screen_region"],
        reason: VisionUnavailableReason,
        *,
        active: ActiveWindowSnapshot | None = None,
        blocked: bool = False,
        stale: bool = False,
    ) -> VisionCaptureOutcome:
        del request_reason
        return VisionCaptureOutcome(
            result=VisionInspectionResult(
                available=False,
                context_id=uuid4(),
                operation_id=operation_id,
                captured_at=datetime.now(UTC),
                source=source,
                window_identity=active.identity if active is not None else None,
                monitor=active.monitor if active is not None else None,
                blocked=blocked,
                stale=stale,
                reason=reason,
            )
        )

    def cancel(self) -> None:
        self._worker.cancel_all()

    def close(self) -> None:
        self._worker.close()


class PendingVisionStore:
    """Small single-use process-memory store for user-selected visual context."""

    def __init__(self, *, max_entries: int = 4, ttl_seconds: float = 120) -> None:
        if not 1 <= max_entries <= 8 or not 5 <= ttl_seconds <= 300:
            raise ValueError("Pending vision store limits are invalid")
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._entries: OrderedDict[UUID, tuple[float, VisionCaptureOutcome]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, outcome: VisionCaptureOutcome) -> None:
        if (
            not outcome.result.available
            or outcome.result.capture_id is None
            or outcome.image is None
        ):
            raise ValueError("Only complete captures can be stored")
        with self._lock:
            self._expire_locked()
            while len(self._entries) >= self._max_entries:
                _, (_, evicted) = self._entries.popitem(last=False)
                evicted.clear()
            self._entries[outcome.result.capture_id] = (time.monotonic() + self._ttl, outcome)

    def take(self, capture_id: UUID) -> VisionCaptureOutcome | None:
        with self._lock:
            self._expire_locked()
            item = self._entries.pop(capture_id, None)
        return item[1] if item is not None else None

    def discard(self, capture_id: UUID) -> bool:
        with self._lock:
            item = self._entries.pop(capture_id, None)
        if item is None:
            return False
        item[1].clear()
        return True

    def close(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for _, outcome in entries:
            outcome.clear()

    def _expire_locked(self) -> None:
        now = time.monotonic()
        expired = [key for key, (deadline, _) in self._entries.items() if deadline <= now]
        for key in expired:
            _, outcome = self._entries.pop(key)
            outcome.clear()
