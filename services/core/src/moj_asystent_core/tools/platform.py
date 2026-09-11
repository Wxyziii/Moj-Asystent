"""Deterministic Windows adapters used only after validation and authorization."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from itertools import islice
from pathlib import Path
from typing import Final

import psutil

from ..context import (
    ActiveWindowSnapshot,
    ContextSettings,
    DesktopContextSnapshot,
    UiaWorker,
    WindowsContextService,
)
from ..windows_context import PywinautoUiaBackend, Win32WindowMetadataProvider
from .models import (
    ActionOutput,
    ContextProviderArguments,
    DeleteFileArguments,
    DirectoryEntry,
    FindProcessByPortArguments,
    FindProcessByPortOutput,
    FocusWindowArguments,
    ListDirectoryArguments,
    ListDirectoryOutput,
    MoveFileArguments,
    OpenApplicationArguments,
    PathArguments,
    PowerActionArguments,
    PowerActionOutput,
    ProcessSummary,
    ReadFileArguments,
    ReadFileOutput,
    RestartProcessArguments,
    RunningProcessesArguments,
    RunningProcessesOutput,
    ScreenInspectionUnavailableOutput,
    SetApplicationVolumeArguments,
    SetVolumeArguments,
    SystemStatsOutput,
)

MAX_FILE_BYTES: Final = 262_144
REPARSE_POINT: Final = 0x400
PROTECTED_PROCESS_NAMES: Final = frozenset(
    {
        "csrss.exe",
        "dwm.exe",
        "explorer.exe",
        "lsass.exe",
        "services.exe",
        "smss.exe",
        "svchost.exe",
        "system",
        "wininit.exe",
        "winlogon.exe",
        "moj-asystent.exe",
        "moj-asystent-core.exe",
    }
)
WINDOWS_RESERVED_NAMES: Final = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
CORE_CREDENTIAL_ENVIRONMENT_KEYS: Final = (
    "MOJ_ASYSTENT_SESSION_CREDENTIAL",
    "MOJ_ASYSTENT_ACTION_CREDENTIAL",
)


class ToolPlatformError(RuntimeError):
    """Expected user-safe platform failure."""


class ToolUnavailableError(ToolPlatformError):
    pass


class PathPolicy:
    """Canonical user-root policy with symlink/reparse escape rejection."""

    def __init__(self, roots: tuple[Path, ...] | None = None) -> None:
        selected = roots or (Path.home(),)
        resolved = tuple(root.expanduser().resolve(strict=True) for root in selected)
        if not resolved or any(not root.is_dir() for root in resolved):
            raise ValueError("Path policy requires existing directory roots")
        self.roots = resolved

    def existing_file(self, value: str) -> Path:
        path = self._resolve_existing(value)
        if not path.is_file():
            raise ToolPlatformError("Wskazany cel nie jest zwykłym plikiem.")
        return path

    def existing_directory(self, value: str) -> Path:
        path = self._resolve_existing(value)
        if not path.is_dir():
            raise ToolPlatformError("Wskazany cel nie jest folderem.")
        return path

    def destination_file(self, value: str) -> Path:
        lexical = self._absolute(value)
        parent = self._resolve_existing(str(lexical.parent))
        destination = parent / lexical.name
        if not self._inside_roots(destination):
            raise ToolPlatformError("Ścieżka wykracza poza dozwolony obszar użytkownika.")
        if destination.exists() or destination.is_symlink():
            raise ToolPlatformError("Plik docelowy już istnieje; nadpisywanie jest wyłączone.")
        return destination

    def _resolve_existing(self, value: str) -> Path:
        lexical = self._absolute(value)
        root = self._matching_root(lexical)
        self._reject_links(root, lexical)
        try:
            resolved = lexical.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ToolPlatformError("Ścieżka nie istnieje lub nie jest dostępna.") from error
        if not self._inside_roots(resolved):
            raise ToolPlatformError("Ścieżka wykracza poza dozwolony obszar użytkownika.")
        return resolved

    def _absolute(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute() or "\x00" in value:
            raise ToolPlatformError("Wymagana jest bezwzględna ścieżka lokalna.")
        if sys.platform == "win32":
            raw = value.replace("/", "\\")
            if raw.startswith(("\\\\?\\", "\\\\.\\")):
                raise ToolPlatformError("Specjalne ścieżki urządzeń Windows są niedozwolone.")
            for part in path.parts[1:]:
                normalized = part.rstrip(" .")
                basename = normalized.split(".", 1)[0].upper()
                if ":" in part or normalized != part or basename in WINDOWS_RESERVED_NAMES:
                    raise ToolPlatformError("Specjalna nazwa ścieżki Windows jest niedozwolona.")
        return Path(os.path.abspath(path))

    def _matching_root(self, path: Path) -> Path:
        for root in self.roots:
            try:
                if os.path.commonpath(
                    (os.path.normcase(root), os.path.normcase(path))
                ) == os.path.normcase(root):
                    return root
            except ValueError:
                continue
        raise ToolPlatformError("Ścieżka wykracza poza dozwolony obszar użytkownika.")

    def _inside_roots(self, path: Path) -> bool:
        return any(path == root or root in path.parents for root in self.roots)

    @staticmethod
    def _reject_links(root: Path, target: Path) -> None:
        current = root
        relative = target.relative_to(root)
        for part in relative.parts:
            current /= part
            if not current.exists() and not current.is_symlink():
                break
            try:
                stat = current.lstat()
            except OSError as error:
                raise ToolPlatformError("Nie można bezpiecznie sprawdzić ścieżki.") from error
            attributes = getattr(stat, "st_file_attributes", 0)
            if current.is_symlink() or attributes & REPARSE_POINT:
                raise ToolPlatformError("Dowiązania i punkty ponownej analizy są niedozwolone.")


class WindowsToolPlatform:
    def __init__(
        self,
        *,
        path_policy: PathPolicy | None = None,
        approved_restart_paths: frozenset[Path] | None = None,
        context_service: WindowsContextService | None = None,
        context_settings: ContextSettings | None = None,
    ) -> None:
        self.paths = path_policy or PathPolicy()
        self.approved_restart_paths = approved_restart_paths or frozenset()
        settings = context_settings or ContextSettings()
        self.context = context_service or WindowsContextService(
            Win32WindowMetadataProvider(),
            UiaWorker(PywinautoUiaBackend),
            limits=settings.limits,
            exclusions=settings.exclusions,
            enabled=settings.enabled,
        )

    def get_system_stats(self) -> SystemStatsOutput:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(Path.home().anchor)
        return SystemStatsOutput(
            cpu_percent=psutil.cpu_percent(interval=0.1),
            memory_percent=memory.percent,
            memory_used_bytes=memory.used,
            memory_total_bytes=memory.total,
            disk_percent=disk.percent,
            disk_free_bytes=disk.free,
        )

    def get_running_processes(self, args: RunningProcessesArguments) -> RunningProcessesOutput:
        processes: list[ProcessSummary] = []
        for process in psutil.process_iter(["pid", "name", "exe", "create_time"]):
            try:
                info = process.info
                name = info.get("name")
                if process.pid <= 0 or not isinstance(name, str) or not name:
                    continue
                raw_create_time = info.get("create_time")
                raw_executable = info.get("exe")
                create_time = (
                    float(raw_create_time)
                    if isinstance(raw_create_time, int | float) and raw_create_time > 0
                    else None
                )
                processes.append(
                    ProcessSummary(
                        pid=process.pid,
                        name=name,
                        executable=(
                            raw_executable
                            if isinstance(raw_executable, str) and raw_executable
                            else None
                        ),
                        create_time=create_time,
                    )
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        processes.sort(key=lambda item: (item.name.casefold(), item.pid))
        return RunningProcessesOutput(
            processes=tuple(processes[: args.limit]), truncated=len(processes) > args.limit
        )

    def get_active_window(self) -> ActiveWindowSnapshot:
        return self.context.active_window()

    def read_ui_tree(self, args: ContextProviderArguments) -> DesktopContextSnapshot:
        return self.context.capture(args.reason)

    def inspect_screen(self, _args: ContextProviderArguments) -> ScreenInspectionUnavailableOutput:
        return ScreenInspectionUnavailableOutput()

    def cancel_context(self) -> None:
        self.context.cancel()

    def close(self) -> None:
        self.context.close()

    def list_directory(self, args: ListDirectoryArguments) -> ListDirectoryOutput:
        directory = self.paths.existing_directory(args.path)
        candidates = list(islice(directory.iterdir(), args.limit + 1))
        truncated = len(candidates) > args.limit
        entries: list[DirectoryEntry] = []
        for item in sorted(candidates, key=lambda path: path.name.casefold()):
            if len(entries) >= args.limit:
                break
            try:
                if (
                    item.is_symlink()
                    or getattr(item.lstat(), "st_file_attributes", 0) & REPARSE_POINT
                ):
                    kind = "other"
                    size = None
                elif item.is_file():
                    kind = "file"
                    size = item.stat().st_size
                elif item.is_dir():
                    kind = "directory"
                    size = None
                else:
                    kind = "other"
                    size = None
                entries.append(DirectoryEntry(name=item.name, kind=kind, size_bytes=size))
            except OSError:
                continue
        return ListDirectoryOutput(path=str(directory), entries=tuple(entries), truncated=truncated)

    def read_file(self, args: ReadFileArguments) -> ReadFileOutput:
        path = self.paths.existing_file(args.path)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ToolPlatformError("Plik przekracza limit 256 KiB.")
        try:
            with path.open("rb") as source:
                raw = source.read(MAX_FILE_BYTES + 1)
        except OSError as error:
            raise ToolPlatformError("Nie można odczytać pliku.") from error
        if len(raw) > MAX_FILE_BYTES:
            raise ToolPlatformError("Plik przekracza limit 256 KiB.")
        if b"\x00" in raw:
            raise ToolPlatformError("Plik binarny nie może zostać przekazany do rozmowy.")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ToolPlatformError("Plik nie jest poprawnym tekstem UTF-8.") from error
        return ReadFileOutput(
            path=str(path), content=content, encoding="utf-8", size_bytes=len(raw)
        )

    def find_process_using_port(self, args: FindProcessByPortArguments) -> FindProcessByPortOutput:
        kind = "tcp" if args.protocol == "tcp" else "udp"
        for connection in psutil.net_connections(kind=kind):
            if not connection.laddr or connection.laddr.port != args.port or connection.pid is None:
                continue
            try:
                process = psutil.Process(connection.pid)
                summary = ProcessSummary(
                    pid=process.pid,
                    name=process.name(),
                    executable=_safe_process_value(process.exe),
                    create_time=_safe_process_value(process.create_time),
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
            return FindProcessByPortOutput(
                found=True, port=args.port, protocol=args.protocol, process=summary
            )
        return FindProcessByPortOutput(found=False, port=args.port, protocol=args.protocol)

    def open_application(self, args: OpenApplicationArguments) -> ActionOutput:
        if sys.platform != "win32":
            raise ToolUnavailableError("Uruchamianie aplikacji jest dostępne tylko w Windows.")
        if args.application_id == "settings":
            os.startfile("ms-settings:")  # type: ignore[attr-defined]
        else:
            windows = _windows_directory()
            commands = {
                "notepad": windows / "System32" / "notepad.exe",
                "calculator": windows / "System32" / "calc.exe",
                "explorer": windows / "explorer.exe",
            }
            executable = commands[args.application_id]
            if not executable.is_file():
                raise ToolUnavailableError("Aplikacja systemowa nie jest dostępna.")
            subprocess.Popen(
                (str(executable),),
                cwd=str(executable.parent),
                close_fds=True,
                env=_sanitized_child_environment(),
            )
        return ActionOutput(changed=True, target=args.application_id, current_value="opened")

    def focus_window(self, args: FocusWindowArguments) -> ActionOutput:
        if sys.platform != "win32":
            raise ToolUnavailableError("Sterowanie oknem jest dostępne tylko w Windows.")
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(args.handle, ctypes.byref(pid))
        if pid.value != args.expected_pid:
            raise ToolPlatformError("Tożsamość okna zmieniła się przed wykonaniem.")
        changed = bool(ctypes.windll.user32.SetForegroundWindow(args.handle))
        if not changed:
            raise ToolPlatformError("Windows nie zezwolił na przełączenie aktywnego okna.")
        return ActionOutput(changed=True, target=str(args.handle), current_value="focused")

    def open_folder(self, args: PathArguments) -> ActionOutput:
        folder = self.paths.existing_directory(args.path)
        if sys.platform != "win32":
            raise ToolUnavailableError("Otwieranie folderu jest dostępne tylko w Windows.")
        os.startfile(folder)  # type: ignore[attr-defined]
        return ActionOutput(changed=True, target=str(folder), current_value="opened")

    def set_volume(self, args: SetVolumeArguments) -> ActionOutput:
        try:
            from pycaw.pycaw import AudioUtilities

            endpoint = AudioUtilities.GetSpeakers().EndpointVolume
            previous = round(float(endpoint.GetMasterVolumeLevelScalar()) * 100)
            endpoint.SetMasterVolumeLevelScalar(args.volume / 100, None)
        except Exception as error:
            raise ToolPlatformError("Nie można zmienić głośności systemowej.") from error
        return ActionOutput(
            changed=previous != args.volume,
            target="system_volume",
            previous_value=previous,
            current_value=args.volume,
        )

    def set_application_volume(self, args: SetApplicationVolumeArguments) -> ActionOutput:
        process = _verified_process(args.pid, args.expected_create_time)
        try:
            from pycaw.pycaw import AudioUtilities

            session = next(
                (
                    item
                    for item in AudioUtilities.GetAllSessions()
                    if item.Process and item.Process.pid == process.pid
                ),
                None,
            )
            if session is None:
                raise ToolPlatformError("Aplikacja nie ma aktywnej sesji dźwięku.")
            previous = round(float(session.SimpleAudioVolume.GetMasterVolume()) * 100)
            session.SimpleAudioVolume.SetMasterVolume(args.volume / 100, None)
        except ToolPlatformError:
            raise
        except Exception as error:
            raise ToolPlatformError("Nie można zmienić głośności aplikacji.") from error
        return ActionOutput(
            changed=previous != args.volume,
            target=f"pid:{process.pid}",
            previous_value=previous,
            current_value=args.volume,
        )

    def restart_approved_process(self, args: RestartProcessArguments) -> ActionOutput:
        process = _verified_process(args.pid, args.expected_create_time)
        name = process.name()
        if name.casefold() != args.executable_name.casefold():
            raise ToolPlatformError("Nazwa procesu nie zgadza się z zatwierdzoną tożsamością.")
        if process.pid == os.getpid() or name.casefold() in PROTECTED_PROCESS_NAMES:
            raise ToolPlatformError(
                "Ten proces jest chroniony i nie może zostać uruchomiony ponownie."
            )
        try:
            executable = Path(process.exe()).resolve(strict=True)
            command = process.cmdline()
            working_directory = process.cwd()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError) as error:
            raise ToolPlatformError("Nie można potwierdzić tożsamości procesu.") from error
        approved = {path.resolve(strict=False) for path in self.approved_restart_paths}
        if executable not in approved:
            raise ToolPlatformError(
                "Plik wykonywalny nie znajduje się na liście zatwierdzonych procesów."
            )
        process.terminate()
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired as error:
            raise ToolPlatformError("Proces nie zakończył się w bezpiecznym czasie.") from error
        subprocess.Popen(
            command or [str(executable)],
            executable=str(executable),
            cwd=working_directory,
            close_fds=True,
            env=_sanitized_child_environment(),
        )
        return ActionOutput(
            changed=True, target=f"{name} (PID {args.pid})", current_value="restarted"
        )

    def move_file(self, args: MoveFileArguments) -> ActionOutput:
        source = self.paths.existing_file(args.source)
        destination = self.paths.destination_file(args.destination)
        try:
            _move_file_no_replace(source, destination)
        except OSError as error:
            raise ToolPlatformError("Nie udało się przenieść pliku.") from error
        return ActionOutput(
            changed=True,
            target=str(destination),
            previous_value=str(source),
            current_value=str(destination),
        )

    def delete_file(self, args: DeleteFileArguments) -> ActionOutput:
        path = self.paths.existing_file(args.path)
        try:
            path.unlink()
        except OSError as error:
            raise ToolPlatformError("Nie udało się usunąć pliku.") from error
        return ActionOutput(changed=True, target=str(path), current_value="deleted")

    def power_action(self, args: PowerActionArguments, *, restart: bool) -> PowerActionOutput:
        if sys.platform != "win32":
            raise ToolUnavailableError("Sterowanie zasilaniem jest dostępne tylko w Windows.")
        message = "Akcja zatwierdzona w Mój Asystent"
        result = ctypes.windll.advapi32.InitiateSystemShutdownExW(
            None, message, args.delay_seconds, True, restart, 0x80000000
        )
        if not result:
            raise ToolPlatformError("Windows odrzucił żądanie zasilania.")
        return PowerActionOutput(
            scheduled=True,
            action="restart" if restart else "shutdown",
            delay_seconds=args.delay_seconds,
        )


def _verified_process(pid: int, expected_create_time: float) -> psutil.Process:
    try:
        process = psutil.Process(pid)
        if abs(process.create_time() - expected_create_time) > 0.01:
            raise ToolPlatformError("Proces zmienił tożsamość przed wykonaniem.")
        return process
    except psutil.NoSuchProcess as error:
        raise ToolPlatformError("Proces już nie istnieje.") from error
    except psutil.AccessDenied as error:
        raise ToolPlatformError("Brak dostępu do procesu.") from error


def _move_file_no_replace(source: Path, destination: Path) -> None:
    """Move one file without any overwrite fallback."""
    if sys.platform == "win32":
        # Windows rename is atomic and rejects an existing destination. In
        # particular, do not use shutil.move: its copy fallback may overwrite a
        # destination that appears after policy validation.
        os.rename(source, destination)
        return
    os.link(source, destination, follow_symlinks=False)
    try:
        source.unlink()
    except OSError:
        destination.unlink(missing_ok=True)
        raise


def _windows_directory() -> Path:
    buffer = ctypes.create_unicode_buffer(32_768)
    length = int(ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        raise ToolUnavailableError("Nie można ustalić katalogu systemowego Windows.")
    path = Path(buffer.value)
    if not path.is_absolute() or not path.is_dir():
        raise ToolUnavailableError("Katalog systemowy Windows jest niedostępny.")
    return path


def _sanitized_child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in CORE_CREDENTIAL_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    return environment


def _safe_process_value(function):
    try:
        return function()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None
