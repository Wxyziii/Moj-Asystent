from __future__ import annotations

import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest

from moj_asystent_core.context import (
    ActiveWindowSnapshot,
    Bounds,
    ContextExclusionPolicy,
    ContextSource,
    WindowIdentity,
)
from moj_asystent_core.vision import (
    PendingVisionStore,
    RawScreenCapture,
    ScreenshotWorker,
    VisionCaptureService,
    VisionLimits,
)


def window(
    *,
    handle: int = 41,
    pid: int = 73,
    process_name: str | None = "notepad.exe",
    executable: str | None = "C:\\Windows\\System32\\notepad.exe",
    bounds: Bounds | None = None,
) -> ActiveWindowSnapshot:
    return ActiveWindowSnapshot(
        available=True,
        identity=WindowIdentity(handle=handle, pid=pid, process_started_at=123.5),
        process_name=process_name,
        executable=executable,
        title="Notatnik",
        window_class="Notepad",
        monitor="\\\\.\\DISPLAY1",
        bounds=bounds or Bounds(left=10, top=20, right=610, bottom=420),
        is_foreground=True,
    )


class FakeWindowProvider:
    def __init__(self, *snapshots: ActiveWindowSnapshot) -> None:
        self._snapshots = list(snapshots)

    def capture(self) -> ActiveWindowSnapshot:
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        return self._snapshots[0]


def pixels(width: int, height: int, *, noisy: bool = False) -> bytearray:
    if noisy:
        return bytearray((index * 73 + index // 11) % 256 for index in range(width * height * 4))
    return bytearray(b"\x10\x40\x80\xff" * (width * height))


class FakeScreenshotBackend:
    def __init__(
        self,
        capture: RawScreenCapture | None = None,
        *,
        delay: float = 0,
        malformed: bool = False,
    ) -> None:
        self.result = capture
        self.delay = delay
        self.malformed = malformed
        self.requests: list[Bounds] = []
        self.closed = False

    def initialize(self) -> None:
        pass

    def capture(self, bounds: Bounds, cancel: threading.Event) -> RawScreenCapture:
        self.requests.append(bounds)
        if self.delay:
            time.sleep(self.delay)
        if self.malformed:
            return RawScreenCapture.unsafe_for_test(bounds.right - bounds.left, 1, bytearray(3))
        if self.result is not None:
            return self.result
        if cancel.is_set():
            raise RuntimeError("cancelled")
        width = bounds.right - bounds.left
        height = bounds.bottom - bounds.top
        return RawScreenCapture(width, height, pixels(width, height))

    def close(self) -> None:
        self.closed = True


def service(
    backend: FakeScreenshotBackend,
    *,
    before=None,
    after=None,
    limits: VisionLimits | None = None,
    exclusions: ContextExclusionPolicy | None = None,
) -> tuple[VisionCaptureService, ScreenshotWorker]:
    active = before or window()
    worker = ScreenshotWorker(lambda: backend)
    capture = VisionCaptureService(
        FakeWindowProvider(active, after or active),
        worker,
        limits=limits or VisionLimits(),
        exclusions=exclusions or ContextExclusionPolicy(),
    )
    return capture, worker


def test_active_window_capture_returns_bounded_metadata_and_memory_image() -> None:
    backend = FakeScreenshotBackend()
    capture, worker = service(backend)
    operation_id = uuid4()
    try:
        outcome = capture.capture_active("Co jest na ekranie?", operation_id)
    finally:
        worker.close()

    assert outcome.result.available is True
    assert outcome.result.operation_id == operation_id
    assert outcome.result.source == "active_window"
    assert outcome.result.provenance == ContextSource.SCREENSHOT
    assert outcome.result.window_identity == window().identity
    assert outcome.result.capture_bounds == window().bounds
    assert outcome.result.monitor == "\\\\.\\DISPLAY1"
    assert outcome.result.vision_interpretation == "pending"
    assert outcome.image is not None
    assert outcome.image.media_type == "image/jpeg"
    assert outcome.image.size_bytes == outcome.result.encoded_image_bytes
    assert backend.requests == [window().bounds]


def test_excluded_window_is_rejected_before_any_pixels_are_captured() -> None:
    backend = FakeScreenshotBackend()
    capture, worker = service(
        backend,
        before=window(process_name="KeePassXC.exe"),
        exclusions=ContextExclusionPolicy(excluded_processes=frozenset({"keepassxc.exe"})),
    )
    try:
        outcome = capture.capture_active("Co tu jest?", uuid4())
    finally:
        worker.close()

    assert outcome.result.available is False
    assert outcome.result.blocked is True
    assert outcome.result.reason == "context_excluded"
    assert outcome.result.capture_bounds is None
    assert outcome.image is None
    assert backend.requests == []


def test_focus_change_discards_and_clears_captured_image() -> None:
    backend = FakeScreenshotBackend()
    capture, worker = service(backend, after=window(handle=99, pid=100))
    try:
        outcome = capture.capture_active("Co tu jest?", uuid4())
    finally:
        worker.close()

    assert outcome.result.available is False
    assert outcome.result.stale is True
    assert outcome.result.reason == "stale_window"
    assert outcome.image is None


def test_region_with_negative_monitor_coordinates_is_valid() -> None:
    backend = FakeScreenshotBackend()
    region = Bounds(left=-1800, top=120, right=-900, bottom=720)
    monitor = Bounds(left=-1920, top=0, right=0, bottom=1080)
    active = window(bounds=Bounds(left=-1850, top=80, right=-800, bottom=800))
    capture, worker = service(backend, before=active, after=active)
    try:
        outcome = capture.capture_region(
            "Wybrany fragment",
            uuid4(),
            region=region,
            monitor_bounds=monitor,
            dpi_scale=1.5,
        )
    finally:
        worker.close()

    assert outcome.result.available is True
    assert outcome.result.source == "screen_region"
    assert outcome.result.capture_bounds == region
    assert outcome.result.dpi_scale == 1.5
    assert backend.requests == [region]
    assert outcome.image is not None
    outcome.image.clear()


@pytest.mark.parametrize(
    "region",
    (
        Bounds(left=-1930, top=0, right=-1800, bottom=100),
        Bounds(left=-100, top=100, right=10, bottom=200),
        Bounds(left=-100, top=100, right=-100, bottom=200),
    ),
)
def test_region_must_be_nonempty_and_inside_one_monitor(region: Bounds) -> None:
    capture, worker = service(FakeScreenshotBackend())
    try:
        with pytest.raises(ValueError):
            capture.capture_region(
                "Fragment",
                uuid4(),
                region=region,
                monitor_bounds=Bounds(left=-1920, top=0, right=0, bottom=1080),
                dpi_scale=1.25,
            )
    finally:
        worker.close()


def test_region_outside_active_window_is_rejected_before_capture() -> None:
    backend = FakeScreenshotBackend()
    active = window(bounds=Bounds(left=-900, top=50, right=-100, bottom=650))
    capture, worker = service(backend, before=active, after=active)
    try:
        with pytest.raises(ValueError, match="active window"):
            capture.capture_region(
                "Fragment",
                uuid4(),
                region=Bounds(left=-1000, top=0, right=-200, bottom=500),
                monitor_bounds=Bounds(left=-1600, top=0, right=0, bottom=900),
                dpi_scale=1.5,
            )
    finally:
        worker.close()
    assert backend.requests == []


def test_pixel_limit_rejects_before_backend_allocation() -> None:
    backend = FakeScreenshotBackend()
    capture, worker = service(
        backend,
        before=window(executable=None).model_copy(
            update={"bounds": Bounds(left=0, top=0, right=5000, bottom=3000)}
        ),
        limits=VisionLimits(max_capture_width=4096, max_capture_height=4096),
    )
    try:
        outcome = capture.capture_active("Obraz", uuid4())
    finally:
        worker.close()

    assert outcome.result.available is False
    assert outcome.result.reason == "capture_too_large"
    assert backend.requests == []


def test_oversized_capture_is_downscaled_and_encoded_within_budget() -> None:
    width, height = 1600, 900
    raw = RawScreenCapture(width, height, pixels(width, height, noisy=True))
    backend = FakeScreenshotBackend(raw)
    capture, worker = service(
        backend,
        before=window().model_copy(
            update={"bounds": Bounds(left=0, top=0, right=width, bottom=height)}
        ),
        limits=VisionLimits(max_normalized_dimension=640, max_encoded_image_bytes=180_000),
    )
    try:
        outcome = capture.capture_active("Wykres", uuid4())
    finally:
        worker.close()

    assert outcome.result.available is True
    assert outcome.result.normalized_width == 640
    assert outcome.result.normalized_height == 360
    assert outcome.result.encoded_image_bytes <= 180_000
    assert raw.cleared is True
    assert outcome.image is not None
    outcome.image.clear()


def test_capture_timeout_and_cancellation_clear_late_pixels() -> None:
    raw = RawScreenCapture(600, 400, pixels(600, 400))
    backend = FakeScreenshotBackend(raw, delay=0.2)
    capture, worker = service(
        backend,
        limits=VisionLimits(capture_timeout_ms=50),
    )
    try:
        outcome = capture.capture_active("Obraz", uuid4())
        time.sleep(0.25)
    finally:
        worker.close()

    assert outcome.result.available is False
    assert outcome.result.reason == "capture_timeout"
    assert outcome.image is None
    assert raw.cleared is True


def test_malformed_raw_capture_fails_closed() -> None:
    backend = FakeScreenshotBackend(malformed=True)
    capture, worker = service(backend)
    try:
        outcome = capture.capture_active("Obraz", uuid4())
    finally:
        worker.close()

    assert outcome.result.available is False
    assert outcome.result.reason == "capture_invalid"
    assert outcome.image is None


def test_capture_never_creates_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    capture, worker = service(FakeScreenshotBackend())
    try:
        outcome = capture.capture_active("Obraz", uuid4())
    finally:
        worker.close()

    assert outcome.result.available is True
    assert list(tmp_path.iterdir()) == []
    assert outcome.image is not None
    outcome.image.clear()


def test_pending_store_is_single_use_and_clears_replaced_or_removed_images() -> None:
    capture, worker = service(FakeScreenshotBackend())
    store = PendingVisionStore(max_entries=1)
    try:
        first = capture.capture_active("Pierwszy", uuid4())
        second = capture.capture_active("Drugi", uuid4())
        assert first.image is not None and second.image is not None
        first_id = first.result.capture_id
        second_id = second.result.capture_id
        assert first_id is not None and second_id is not None
        store.put(first)
        store.put(second)
        assert first.image.cleared is True
        assert store.take(first_id) is None
        claimed = store.take(second_id)
        assert claimed is not None
        assert store.take(second_id) is None
        claimed.image.clear()
    finally:
        store.close()
        worker.close()


def test_worker_shutdown_closes_backend() -> None:
    backend = FakeScreenshotBackend()
    worker = ScreenshotWorker(lambda: backend)
    worker.close()
    assert backend.closed is False

    worker = ScreenshotWorker(lambda: backend)
    capture = VisionCaptureService(FakeWindowProvider(window()), worker)
    outcome = capture.capture_active("Obraz", uuid4())
    assert outcome.image is not None
    outcome.image.clear()
    worker.close()
    assert backend.closed is True
