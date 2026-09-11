from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from ctypes import POINTER, wintypes
from ctypes import cast as ctypes_cast
from typing import Any, cast

import pytest

from moj_asystent_core.context import (
    ActiveWindowSnapshot,
    Bounds,
    ContextExclusionPolicy,
    ContextLimits,
    ContextSource,
    UiaCaptureCancelled,
    UiaCaptureTimeout,
    UiaWorker,
    WindowIdentity,
    WindowsContextService,
    context_json_size,
)
from moj_asystent_core.windows_context import Win32WindowMetadataProvider


def window(
    *,
    handle: int = 41,
    pid: int = 73,
    process_name: str | None = "notepad.exe",
    title: str | None = "Notatnik",
    executable: str | None = "C:\\Windows\\System32\\notepad.exe",
) -> ActiveWindowSnapshot:
    return ActiveWindowSnapshot(
        available=True,
        identity=WindowIdentity(handle=handle, pid=pid, process_started_at=123.5),
        process_name=process_name,
        executable=executable,
        title=title,
        window_class="Notepad",
        monitor="\\\\.\\DISPLAY1",
        bounds=Bounds(left=10, top=20, right=610, bottom=420),
        is_foreground=True,
        unavailable_fields=(),
        provenance=ContextSource.WINDOWS_METADATA,
    )


def window_identity() -> WindowIdentity:
    return cast(WindowIdentity, window().identity)


class FakeWindowProvider:
    def __init__(self, *snapshots: ActiveWindowSnapshot) -> None:
        self._snapshots = list(snapshots)

    def capture(self) -> ActiveWindowSnapshot:
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        return self._snapshots[0]


class FakeUser32:
    def GetForegroundWindow(self) -> int:
        return 41

    def GetWindowThreadProcessId(self, _handle: int, pointer: object) -> int:
        ctypes_cast(cast(Any, pointer), POINTER(wintypes.DWORD)).contents.value = 73
        return 1


class FakeProcess:
    def name(self) -> str:
        return "notepad.exe"

    def exe(self) -> str:
        return "C:\\Windows\\System32\\notepad.exe"

    def create_time(self) -> float:
        return 123.5


class FakeElement:
    def __init__(
        self,
        properties: Mapping[str, object] | None = None,
        children: tuple[FakeElement, ...] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.properties = properties or {}
        self._children = children
        self.error = error

    def read_properties(self) -> Mapping[str, object]:
        if self.error is not None:
            raise self.error
        return self.properties

    def children(self) -> tuple[FakeElement, ...]:
        return self._children


class FakeBackend:
    def __init__(
        self,
        root: FakeElement | None,
        focused: FakeElement | None = None,
        *,
        block_until_cancelled: bool = False,
        initialize_delay: float = 0,
        on_capture: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.focused = focused
        self.block_until_cancelled = block_until_cancelled
        self.initialize_delay = initialize_delay
        self.on_capture = on_capture
        self.closed = False

    def initialize(self) -> None:
        time.sleep(self.initialize_delay)

    def element_from_handle(self, handle: int, cancel: threading.Event) -> FakeElement | None:
        if self.on_capture is not None:
            self.on_capture()
        while self.block_until_cancelled and not cancel.wait(0.005):
            pass
        if cancel.is_set():
            raise UiaCaptureCancelled
        return self.root

    def focused_element(self, cancel: threading.Event) -> FakeElement | None:
        return self.focused

    def close(self) -> None:
        self.closed = True


def service(
    before: ActiveWindowSnapshot,
    backend: FakeBackend,
    *,
    after: ActiveWindowSnapshot | None = None,
    limits: ContextLimits | None = None,
    exclusions: ContextExclusionPolicy | None = None,
) -> tuple[WindowsContextService, UiaWorker]:
    worker = UiaWorker(lambda: backend)
    context = WindowsContextService(
        FakeWindowProvider(before, after or before),
        worker,
        limits=limits or ContextLimits(),
        exclusions=exclusions or ContextExclusionPolicy(),
    )
    return context, worker


def test_active_window_maps_win32_and_process_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "moj_asystent_core.windows_context._bounded_window_text",
        lambda _user32, _handle: "Notatnik",
    )
    monkeypatch.setattr(
        "moj_asystent_core.windows_context._window_class",
        lambda _user32, _handle: "Notepad",
    )
    monkeypatch.setattr(
        "moj_asystent_core.windows_context._monitor_name",
        lambda _user32, _handle: "\\\\.\\DISPLAY1",
    )
    monkeypatch.setattr(
        "moj_asystent_core.windows_context._window_bounds",
        lambda _user32, _handle: Bounds(left=10, top=20, right=610, bottom=420),
    )
    observed = Win32WindowMetadataProvider(
        user32=FakeUser32(), process_factory=lambda _pid: FakeProcess(), platform_name="win32"
    ).capture()

    assert observed.available is True
    assert observed.identity == WindowIdentity(handle=41, pid=73, process_started_at=123.5)
    assert observed.process_name == "notepad.exe"
    assert observed.title == "Notatnik"
    assert observed.monitor == "\\\\.\\DISPLAY1"


def test_active_window_keeps_identity_when_process_metadata_is_inaccessible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InaccessibleProcess:
        def name(self) -> str:
            raise PermissionError

        exe = name
        create_time = name

    monkeypatch.setattr(
        "moj_asystent_core.windows_context._bounded_window_text",
        lambda _user32, _handle: None,
    )
    monkeypatch.setattr(
        "moj_asystent_core.windows_context._window_class",
        lambda _user32, _handle: None,
    )
    monkeypatch.setattr(
        "moj_asystent_core.windows_context._monitor_name",
        lambda _user32, _handle: None,
    )
    monkeypatch.setattr(
        "moj_asystent_core.windows_context._window_bounds",
        lambda _user32, _handle: None,
    )
    observed = Win32WindowMetadataProvider(
        user32=FakeUser32(),
        process_factory=lambda _pid: InaccessibleProcess(),
        platform_name="win32",
    ).capture()

    assert observed.available is True
    assert observed.identity == WindowIdentity(handle=41, pid=73)
    assert observed.process_name is None
    assert observed.executable is None
    assert {"process_name", "executable", "process_started_at"}.issubset(
        observed.unavailable_fields
    )


def test_context_serializes_a_useful_bounded_tree_and_selected_text() -> None:
    focused = FakeElement(
        {
            "process_id": 73,
            "runtime_id": "focused-1",
            "control_type": "Edit",
            "name": "Treść",
            "automation_id": "editor",
            "enabled": True,
            "focused": True,
            "bounds": (20, 30, 500, 300),
            "value": "Ala ma kota",
            "text": "Ala ma kota",
            "selected_text": "ma kota",
            "patterns": ("Value", "Text"),
        }
    )
    root = FakeElement(
        {"process_id": 73, "runtime_id": "root", "control_type": "Window"},
        (focused,),
    )
    context, worker = service(window(), FakeBackend(root, focused))
    try:
        result = context.capture("Co jest zaznaczone?")
    finally:
        worker.close()

    assert result.available is True
    assert result.context_id
    assert result.active_window.identity is not None
    assert result.active_window.identity.handle == 41
    assert [node.path for node in result.ui_tree.nodes] == [(), (0,)]
    assert result.focused_control is not None
    assert result.focused_control.automation_id == "editor"
    assert result.selected_text.available is True
    assert result.selected_text.value == "ma kota"
    assert result.selected_text.provenance is ContextSource.UI_AUTOMATION
    assert result.screenshot.available is False
    assert result.screenshot.planned_milestone == 8
    assert result.timings.total_ms >= 0


@pytest.mark.parametrize(
    ("limits", "expected_nodes", "flag"),
    [
        (ContextLimits(max_nodes=2), 2, "node_limit"),
        (ContextLimits(max_depth=1), 3, "depth_limit"),
    ],
)
def test_tree_bounds_are_explicit(limits: ContextLimits, expected_nodes: int, flag: str) -> None:
    grandchild = FakeElement({"process_id": 73, "control_type": "Text", "name": "3"})
    child = FakeElement({"process_id": 73, "control_type": "Pane", "name": "2"}, (grandchild,))
    sibling = FakeElement({"process_id": 73, "control_type": "Button", "name": "4"})
    root = FakeElement({"process_id": 73, "control_type": "Window", "name": "1"}, (child, sibling))
    context, worker = service(window(), FakeBackend(root), limits=limits)
    try:
        result = context.capture("Co jest w oknie?")
    finally:
        worker.close()

    assert len(result.ui_tree.nodes) == expected_nodes
    assert result.ui_tree.truncated is True
    assert flag in result.ui_tree.truncation_reasons


def test_text_and_aggregate_payload_are_bounded() -> None:
    children = tuple(
        FakeElement(
            {
                "process_id": 73,
                "control_type": "Text",
                "name": f"node-{index}-" + "x" * 500,
                "text": "y" * 500,
            }
        )
        for index in range(20)
    )
    root = FakeElement({"process_id": 73, "control_type": "Window"}, children)
    limits = ContextLimits(max_text_length=24, max_payload_bytes=900)
    context, worker = service(window(), FakeBackend(root), limits=limits)
    try:
        result = context.capture("Odczytaj interfejs")
    finally:
        worker.close()

    assert all(
        len(value) <= 24
        for node in result.ui_tree.nodes
        for value in (node.name, node.value, node.text)
        if value is not None
    )
    assert result.ui_tree.payload_bytes <= 900
    assert result.ui_tree.truncated is True
    assert {"text_limit", "payload_limit"}.issubset(result.ui_tree.truncation_reasons)


def test_complete_context_snapshot_fits_the_model_tool_result_budget() -> None:
    focused = FakeElement(
        {
            "process_id": 73,
            "runtime_id": "focused",
            "control_type": "Edit",
            "focused": True,
            "name": "n" * 2_000,
            "automation_id": "a" * 2_000,
            "value": "v" * 2_000,
            "text": "t" * 2_000,
            "selected_text": "s" * 2_000,
        }
    )
    children = (focused,) + tuple(
        FakeElement({"process_id": 73, "control_type": "Text", "name": f"{index}-" + "x" * 500})
        for index in range(50)
    )
    root = FakeElement({"process_id": 73, "control_type": "Window"}, children)
    context, worker = service(
        window(),
        FakeBackend(root, focused),
        limits=ContextLimits(max_text_length=512, max_payload_bytes=6_000),
    )
    try:
        result = context.capture("Co jest w oknie?")
    finally:
        worker.close()

    assert context_json_size(result) <= 7_000
    assert result.ui_tree.truncated is True
    assert "payload_limit" in result.ui_tree.truncation_reasons


def test_malformed_properties_are_ignored_without_losing_the_tree() -> None:
    root = FakeElement(
        {
            "process_id": "not-an-int",
            "control_type": object(),
            "name": 12,
            "enabled": "yes",
            "bounds": (0, 1, "bad", 3),
            "patterns": ("Value", object()),
        },
        (FakeElement(error=RuntimeError("provider rejected property")),),
    )
    context, worker = service(window(), FakeBackend(root))
    try:
        result = context.capture("Sprawdź okno")
    finally:
        worker.close()

    assert result.available is True
    assert result.ui_tree.nodes[0].control_type == "Unknown"
    assert result.ui_tree.nodes[0].name is None
    assert result.ui_tree.nodes[0].bounds is None
    assert result.ui_tree.skipped_nodes == 1


def test_password_control_never_exposes_value_text_or_selection() -> None:
    secret = FakeElement(
        {
            "process_id": 73,
            "control_type": "Edit",
            "focused": True,
            "is_password": True,
            "name": "Hasło",
            "value": "hunter2",
            "text": "hunter2",
            "selected_text": "hunter2",
        }
    )
    context, worker = service(window(), FakeBackend(secret, secret))
    try:
        result = context.capture("Co zaznaczyłem?")
    finally:
        worker.close()

    assert result.ui_tree.nodes[0].value is None
    assert result.ui_tree.nodes[0].text is None
    assert result.selected_text.available is False
    assert result.selected_text.reason == "protected_control"


def test_selected_text_is_explicitly_unavailable_when_pattern_has_no_selection() -> None:
    focused = FakeElement(
        {"process_id": 73, "control_type": "Button", "focused": True, "name": "Zapisz"}
    )
    context, worker = service(window(), FakeBackend(focused, focused))
    try:
        result = context.capture("Co jest zaznaczone?")
    finally:
        worker.close()

    assert result.selected_text.available is False
    assert result.selected_text.value is None
    assert result.selected_text.reason == "selection_unavailable"


def test_focused_control_survives_a_tree_bound_when_it_belongs_to_active_process() -> None:
    root = FakeElement({"process_id": 73, "control_type": "Window", "runtime_id": "root"})
    focused = FakeElement(
        {
            "process_id": 73,
            "control_type": "Edit",
            "runtime_id": "focused",
            "focused": True,
            "value": "treść pola",
        }
    )
    context, worker = service(
        window(),
        FakeBackend(root, focused),
        limits=ContextLimits(max_nodes=1),
    )
    try:
        result = context.capture("Co jest w aktywnym polu?")
    finally:
        worker.close()

    assert result.ui_tree.node_count == 1
    assert result.focused_control is not None
    assert result.focused_control.value == "treść pola"


def test_focused_text_from_another_process_is_never_attached() -> None:
    foreign = FakeElement(
        {
            "process_id": 999,
            "control_type": "Edit",
            "focused": True,
            "value": "sekret z innej aplikacji",
            "selected_text": "obcy sekret",
        }
    )
    context, worker = service(
        window(),
        FakeBackend(FakeElement({"process_id": 73, "control_type": "Window"}), foreign),
    )
    try:
        result = context.capture("Co jest zaznaczone?")
    finally:
        worker.close()

    assert result.focused_control is None
    assert result.selected_text.available is False
    assert result.selected_text.reason == "focused_control_unavailable"
    assert "obcy sekret" not in result.model_dump_json()


def test_excluded_application_is_blocked_before_uia_collection() -> None:
    touched = False

    def mark_touched() -> None:
        nonlocal touched
        touched = True

    backend = FakeBackend(FakeElement(), on_capture=mark_touched)
    context, worker = service(
        window(process_name="keepassxc.exe"),
        backend,
        exclusions=ContextExclusionPolicy(excluded_processes=frozenset({"KeePassXC.exe"})),
    )
    try:
        result = context.capture("Co jest w tym oknie?")
    finally:
        worker.close()

    assert result.available is False
    assert result.blocked is True
    assert result.ui_tree.nodes == ()
    assert result.active_window.title is None
    assert result.active_window.executable is None
    assert result.active_window.bounds is None
    assert touched is False


def test_active_window_exclusion_redacts_sensitive_fields_without_starting_worker() -> None:
    backend = FakeBackend(FakeElement())
    context, worker = service(
        window(process_name="keepassxc.exe"),
        backend,
        exclusions=ContextExclusionPolicy(excluded_processes=frozenset({"KeePassXC.exe"})),
    )
    try:
        result = context.active_window()
    finally:
        worker.close()

    assert result.available is False
    assert result.reason == "context_excluded"
    assert result.title is None
    assert result.executable is None
    assert result.bounds is None


def test_disabled_context_returns_no_window_or_uia_content() -> None:
    touched = False

    def mark_touched() -> None:
        nonlocal touched
        touched = True

    backend = FakeBackend(FakeElement(), on_capture=mark_touched)
    worker = UiaWorker(lambda: backend)
    context = WindowsContextService(FakeWindowProvider(window()), worker, enabled=False)
    try:
        active = context.active_window()
        result = context.capture("Co jest otwarte?")
    finally:
        worker.close()

    assert active.available is False
    assert active.reason == "context_disabled"
    assert result.available is False
    assert result.reason == "context_disabled"
    assert result.ui_tree.nodes == ()
    assert touched is False


def test_window_title_exclusion_is_case_insensitive_and_not_returned() -> None:
    context, worker = service(
        window(title="Karta InPrivate — przeglądarka"),
        FakeBackend(FakeElement()),
        exclusions=ContextExclusionPolicy(excluded_title_fragments=("INPRIVATE",)),
    )
    try:
        result = context.capture("Co jest otwarte?")
    finally:
        worker.close()

    assert result.blocked is True
    assert result.active_window.title is None


def test_focus_change_during_collection_discards_the_old_window_content() -> None:
    first = window(handle=41, pid=73)
    second = window(handle=42, pid=74, title="Kalkulator", process_name="CalculatorApp.exe")
    root = FakeElement({"process_id": 73, "control_type": "Text", "text": "stary prywatny tekst"})
    context, worker = service(first, FakeBackend(root), after=second)
    try:
        result = context.capture("Co jest w oknie?")
    finally:
        worker.close()

    assert result.available is False
    assert result.stale is True
    assert result.active_window.identity == first.identity
    assert result.ui_tree.nodes == ()
    assert result.selected_text.value is None


def test_provider_exception_returns_no_context_without_private_error_text() -> None:
    class FailingBackend(FakeBackend):
        def element_from_handle(self, handle: int, cancel: threading.Event) -> FakeElement | None:
            del handle, cancel
            raise RuntimeError("secret provider details")

    backend = FailingBackend(None)
    context, worker = service(window(), backend)
    try:
        result = context.capture("Sprawdź okno")
    finally:
        worker.close()

    assert result.available is False
    assert "secret" not in (result.reason or "")
    assert result.ui_tree.nodes == ()


def test_context_logs_only_operational_metadata(caplog: pytest.LogCaptureFixture) -> None:
    focused = FakeElement(
        {
            "process_id": 73,
            "control_type": "Edit",
            "focused": True,
            "text": "bardzo prywatna treść",
            "selected_text": "sekret użytkownika",
        }
    )
    context, worker = service(
        window(title="Prywatny tytuł dokumentu"), FakeBackend(focused, focused)
    )
    try:
        with caplog.at_level("INFO", logger="moj_asystent_core.context"):
            context.capture("Co jest zaznaczone?")
    finally:
        worker.close()

    assert "bardzo prywatna" not in caplog.text
    assert "sekret użytkownika" not in caplog.text
    assert "Prywatny tytuł" not in caplog.text
    record = next(item for item in caplog.records if item.message == "context_collected")
    assert record.__dict__["node_count"] == 1
    assert not hasattr(record, "selected_text")


def test_worker_timeout_cancels_the_request_without_freezing_the_caller() -> None:
    backend = FakeBackend(FakeElement(), block_until_cancelled=True)
    worker = UiaWorker(lambda: backend)
    started = time.monotonic()
    with pytest.raises(UiaCaptureTimeout):
        worker.capture(window_identity(), ContextLimits(traversal_timeout_ms=30))
    assert time.monotonic() - started < 0.5
    worker.close()


def test_worker_initialization_does_not_consume_the_traversal_budget() -> None:
    backend = FakeBackend(
        FakeElement({"process_id": 73, "control_type": "Window"}),
        initialize_delay=0.05,
    )
    worker = UiaWorker(lambda: backend)
    try:
        result = worker.capture(window_identity(), ContextLimits(traversal_timeout_ms=20))
    finally:
        worker.close()

    assert result.tree.node_count == 1


def test_worker_honors_cancellation_and_shuts_down_its_backend() -> None:
    backend = FakeBackend(FakeElement(), block_until_cancelled=True)
    worker = UiaWorker(lambda: backend)
    cancellation = threading.Event()
    result: list[BaseException] = []

    def run() -> None:
        try:
            worker.capture(
                window_identity(),
                ContextLimits(traversal_timeout_ms=1_000),
                cancellation=cancellation,
            )
        except BaseException as error:
            result.append(error)

    caller = threading.Thread(target=run)
    caller.start()
    time.sleep(0.03)
    cancellation.set()
    caller.join(timeout=0.5)
    worker.close()

    assert not caller.is_alive()
    assert len(result) == 1
    assert isinstance(result[0], UiaCaptureCancelled)
    assert backend.closed is True


def test_unavailable_window_returns_an_honest_no_context_fallback() -> None:
    unavailable = ActiveWindowSnapshot(
        available=False,
        reason="secure_desktop",
        unavailable_fields=("window",),
        provenance=ContextSource.WINDOWS_METADATA,
    )
    context, worker = service(unavailable, FakeBackend(FakeElement()))
    try:
        result = context.capture("Co jest otwarte?")
    finally:
        worker.close()

    assert result.available is False
    assert result.reason == "secure_desktop"
    assert result.ui_tree.nodes == ()
    assert result.screenshot.available is False


def test_context_model_rejects_unbounded_limit_configuration() -> None:
    with pytest.raises(ValueError):
        ContextLimits(max_nodes=10_000)
    with pytest.raises(ValueError):
        ContextLimits(traversal_timeout_ms=0)
