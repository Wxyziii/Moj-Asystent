"""Native Windows metadata and UI Automation adapters.

The UIA types are imported lazily on the dedicated worker thread. No COM
interface leaves that thread or reaches asyncio/React.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from ctypes import wintypes
from typing import Any

import psutil

from .context import (
    ActiveWindowSnapshot,
    Bounds,
    UiaCaptureCancelled,
    UiaProviderUnavailable,
    UnavailableWindowField,
    WindowIdentity,
)

MONITOR_DEFAULTTONEAREST = 2
MAX_UIA_NATIVE_TEXT = 2_049


class _MonitorInfoExW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def _bounded_window_text(user32: Any, handle: int) -> str | None:
    try:
        length = min(max(int(user32.GetWindowTextLengthW(handle)), 0), 511)
        buffer = ctypes.create_unicode_buffer(length + 1)
        copied = int(user32.GetWindowTextW(handle, buffer, length + 1))
        return buffer.value if copied > 0 else None
    except (OSError, ValueError):
        return None


def _window_class(user32: Any, handle: int) -> str | None:
    try:
        buffer = ctypes.create_unicode_buffer(256)
        copied = int(user32.GetClassNameW(handle, buffer, len(buffer)))
        return buffer.value if copied > 0 else None
    except (OSError, ValueError):
        return None


def _window_bounds(user32: Any, handle: int) -> Bounds | None:
    try:
        rectangle = wintypes.RECT()
        if not user32.GetWindowRect(handle, ctypes.byref(rectangle)):
            return None
        return Bounds(
            left=rectangle.left,
            top=rectangle.top,
            right=rectangle.right,
            bottom=rectangle.bottom,
        )
    except (OSError, ValueError):
        return None


def _monitor_name(user32: Any, handle: int) -> str | None:
    try:
        monitor = user32.MonitorFromWindow(handle, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = _MonitorInfoExW()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return info.szDevice or None
    except (OSError, ValueError):
        return None


class Win32WindowMetadataProvider:
    """Best-effort foreground-window metadata with explicit missing fields."""

    def __init__(
        self,
        *,
        user32: Any = None,
        process_factory: Callable[[int], Any] = psutil.Process,
        platform_name: str | None = None,
    ) -> None:
        self._user32 = user32
        self._process_factory = process_factory
        self._platform_name = platform_name or sys.platform

    def capture(self) -> ActiveWindowSnapshot:
        if self._platform_name != "win32":
            return ActiveWindowSnapshot(
                available=False,
                unavailable_fields=("window",),
                reason="windows_only",
            )
        user32 = self._user32 or ctypes.windll.user32
        handle = int(user32.GetForegroundWindow())
        if handle <= 0:
            return ActiveWindowSnapshot(
                available=False,
                unavailable_fields=("window",),
                reason="foreground_window_unavailable",
            )
        raw_pid = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(handle, ctypes.byref(raw_pid)) or raw_pid.value <= 0:
            return ActiveWindowSnapshot(
                available=False,
                unavailable_fields=("identity",),
                reason="window_identity_unavailable",
            )

        missing: list[UnavailableWindowField] = []
        process_name: str | None = None
        executable: str | None = None
        process_started_at: float | None = None
        try:
            process = self._process_factory(raw_pid.value)
            try:
                process_name = process.name() or None
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                missing.append("process_name")
            try:
                executable = process.exe() or None
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                missing.append("executable")
            try:
                process_started_at = process.create_time()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                missing.append("process_started_at")
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            missing.extend(("process_name", "executable", "process_started_at"))

        title = _bounded_window_text(user32, handle)
        window_class = _window_class(user32, handle)
        monitor = _monitor_name(user32, handle)
        bounds = _window_bounds(user32, handle)
        for key, value in (
            ("title", title),
            ("window_class", window_class),
            ("monitor", monitor),
            ("bounds", bounds),
        ):
            if value is None:
                missing.append(key)
        return ActiveWindowSnapshot(
            available=True,
            identity=WindowIdentity(
                handle=handle,
                pid=raw_pid.value,
                process_started_at=process_started_at,
            ),
            process_name=process_name,
            executable=executable,
            title=title,
            window_class=window_class,
            monitor=monitor,
            bounds=bounds,
            is_foreground=int(user32.GetForegroundWindow()) == handle,
            unavailable_fields=tuple(dict.fromkeys(missing)),
        )


class _PywinautoElement:
    def __init__(self, info: Any, interface_getter: Callable[[Any, str], Any]) -> None:
        self._info = info
        self._interface_getter = interface_getter

    def read_properties(self) -> Mapping[str, object]:
        element = self._info.element

        def safe(function: Callable[[], Any]) -> Any:
            try:
                return function()
            except Exception:
                return None

        runtime = safe(lambda: self._info.runtime_id)
        runtime_id = (
            ".".join(str(part) for part in runtime)
            if isinstance(runtime, tuple | list)
            else str(runtime)
            if isinstance(runtime, int) and runtime
            else None
        )
        rectangle = safe(lambda: self._info.rectangle)
        bounds = (
            (rectangle.left, rectangle.top, rectangle.right, rectangle.bottom)
            if rectangle is not None
            else None
        )
        protected = safe(lambda: bool(element.CurrentIsPassword)) is True
        patterns: list[str] = []
        value: object | None = None
        text: object | None = None
        selected: object | None = None
        if not protected:
            value_pattern = safe(lambda: self._interface_getter(element, "Value"))
            if value_pattern is not None:
                patterns.append("Value")
                value = safe(lambda: value_pattern.CurrentValue)
            text_pattern = safe(lambda: self._interface_getter(element, "Text"))
            if text_pattern is not None:
                patterns.append("Text")
                text = safe(lambda: text_pattern.DocumentRange.GetText(MAX_UIA_NATIVE_TEXT))
                ranges = safe(text_pattern.GetSelection)
                try:
                    if ranges is not None and int(ranges.Length) > 0:
                        parts = [
                            ranges.GetElement(index).GetText(MAX_UIA_NATIVE_TEXT)
                            for index in range(min(int(ranges.Length), 8))
                        ]
                        selected = "\n".join(part for part in parts if part)
                except Exception:
                    selected = None
        return {
            "process_id": safe(lambda: self._info.process_id),
            "runtime_id": runtime_id,
            "control_type": safe(lambda: self._info.control_type),
            "name": safe(lambda: self._info.name),
            "automation_id": safe(lambda: self._info.automation_id),
            "enabled": safe(lambda: bool(self._info.enabled)),
            "focused": safe(lambda: bool(element.CurrentHasKeyboardFocus)),
            "bounds": bounds,
            "is_password": protected,
            "value": value,
            "text": text,
            "selected_text": selected,
            "patterns": tuple(patterns),
        }

    def children(self) -> Iterable[_PywinautoElement]:
        for child in self._info.iter_children():
            yield _PywinautoElement(child, self._interface_getter)


class PywinautoUiaBackend:
    """UIA backend whose entire COM lifetime belongs to one MTA worker thread."""

    def __init__(self) -> None:
        self._element_info: Any = None
        self._iuia: Any = None
        self._interface_getter: Callable[[Any, str], Any] | None = None
        self._comtypes: Any = None
        self._initialized = False

    def initialize(self) -> None:
        if sys.platform != "win32":
            raise UiaProviderUnavailable
        # pywinauto creates a process-global UIA singleton on first import. The
        # import therefore happens only here, after this worker enters the MTA.
        sys.__dict__["coinit_flags"] = 0
        import comtypes

        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            from pywinauto.uia_defines import IUIA, get_elem_interface
            from pywinauto.uia_element_info import UIAElementInfo

            self._element_info = UIAElementInfo
            self._iuia = IUIA()
            self._interface_getter = get_elem_interface
            self._comtypes = comtypes
            self._initialized = True
            self._set_rpc_timeouts()
        except Exception:
            comtypes.CoUninitialize()
            raise

    def _set_rpc_timeouts(self) -> None:
        try:
            automation2 = self._iuia.iuia.QueryInterface(
                self._iuia.ui_automation_client.IUIAutomation2
            )
            automation2.ConnectionTimeout = 1_000
            automation2.TransactionTimeout = 1_000
        except Exception:
            # Older UIAutomationCore versions do not expose IUIAutomation2.
            pass

    def element_from_handle(self, handle: int, cancel: threading.Event) -> _PywinautoElement | None:
        if cancel.is_set():
            raise UiaCaptureCancelled
        assert self._interface_getter is not None
        try:
            return _PywinautoElement(self._element_info(handle), self._interface_getter)
        except Exception:
            return None

    def focused_element(self, cancel: threading.Event) -> _PywinautoElement | None:
        if cancel.is_set():
            raise UiaCaptureCancelled
        assert self._interface_getter is not None
        try:
            raw = self._iuia.get_focused_element()
            return _PywinautoElement(self._element_info(raw), self._interface_getter)
        except Exception:
            return None

    def close(self) -> None:
        if not self._initialized:
            return
        self._element_info = None
        self._iuia = None
        self._interface_getter = None
        self._initialized = False
        self._comtypes.CoUninitialize()
        self._comtypes = None
