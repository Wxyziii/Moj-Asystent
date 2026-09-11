from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import psutil
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from moj_asystent_core.tools.models import (
    DeleteFileArguments,
    ListDirectoryArguments,
    MoveFileArguments,
    OpenApplicationArguments,
    PermissionLevel,
    ReadFileArguments,
    RestartProcessArguments,
    RunningProcessesArguments,
    SetApplicationVolumeArguments,
    SetVolumeArguments,
)
from moj_asystent_core.tools.platform import (
    MAX_FILE_BYTES,
    PathPolicy,
    ToolPlatformError,
    WindowsToolPlatform,
)
from moj_asystent_core.tools.policy import (
    PermissionDecision,
    PermissionPolicyStore,
    ToolPreference,
)


@given(st.integers().filter(lambda value: value < 0 or value > 100))
def test_volume_rejects_every_integer_outside_closed_range(value: int) -> None:
    with pytest.raises(ValidationError):
        SetVolumeArguments(volume=value)


@given(st.integers(min_value=0, max_value=100))
def test_volume_accepts_entire_closed_range(value: int) -> None:
    assert SetVolumeArguments(volume=value).volume == value


@given(st.sampled_from(list(ToolPreference)))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_stored_preference_can_bypass_sensitive_confirmation(
    tmp_path: Path, preference: ToolPreference
) -> None:
    policy = PermissionPolicyStore(tmp_path / "permissions.json")
    policy.set_preference("delete_file", preference)
    assert policy.evaluate("delete_file", PermissionLevel.SENSITIVE) is PermissionDecision.CONFIRM


def test_application_volume_requires_process_identity() -> None:
    with pytest.raises(ValidationError):
        SetApplicationVolumeArguments.model_validate({"pid": 123, "volume": 50})


def test_application_action_rejects_a_reused_pid_identity(tmp_path: Path) -> None:
    platform = WindowsToolPlatform(path_policy=PathPolicy((tmp_path,)))
    process = psutil.Process(os.getpid())
    with pytest.raises(ToolPlatformError, match="tożsamość"):
        platform.set_application_volume(
            SetApplicationVolumeArguments(
                pid=process.pid,
                expected_create_time=process.create_time() + 10,
                volume=50,
            )
        )


def test_restart_refuses_protected_process_before_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ProtectedProcess:
        pid = 123

        @staticmethod
        def name() -> str:
            return "svchost.exe"

    monkeypatch.setattr(
        "moj_asystent_core.tools.platform._verified_process",
        lambda _pid, _created: ProtectedProcess(),
    )
    platform = WindowsToolPlatform(path_policy=PathPolicy((tmp_path,)))
    with pytest.raises(ToolPlatformError, match="chroniony"):
        platform.restart_approved_process(
            RestartProcessArguments(
                pid=123, expected_create_time=1.0, executable_name="svchost.exe"
            )
        )


def test_restart_launches_only_the_verified_approved_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "approved.exe"
    executable.write_bytes(b"test")
    launched: list[tuple[list[str], dict[str, object]]] = []

    class ApprovedProcess:
        pid = 321

        @staticmethod
        def name() -> str:
            return "approved.exe"

        @staticmethod
        def exe() -> str:
            return str(executable)

        @staticmethod
        def cmdline() -> list[str]:
            return ["relative-unapproved.exe", "--safe-argument"]

        @staticmethod
        def cwd() -> str:
            return str(tmp_path)

        @staticmethod
        def terminate() -> None:
            pass

        @staticmethod
        def wait(timeout: int) -> None:
            assert timeout == 5

    monkeypatch.setattr(
        "moj_asystent_core.tools.platform._verified_process",
        lambda _pid, _created: ApprovedProcess(),
    )
    monkeypatch.setattr(
        "moj_asystent_core.tools.platform.subprocess.Popen",
        lambda command, **options: launched.append((list(command), options)),
    )
    platform = WindowsToolPlatform(
        path_policy=PathPolicy((tmp_path,)),
        approved_restart_paths=frozenset({executable}),
    )

    platform.restart_approved_process(
        RestartProcessArguments(pid=321, expected_create_time=1.0, executable_name="approved.exe")
    )

    command, options = launched[0]
    assert command == ["relative-unapproved.exe", "--safe-argument"]
    assert options["executable"] == str(executable.resolve())


def test_process_listing_normalizes_non_actionable_windows_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class IdleProcess:
        pid = 0
        info = {
            "pid": 0,
            "name": "System Idle Process",
            "exe": None,
            "create_time": 0.0,
        }

    class ProcessWithoutExecutable:
        pid = 42
        info = {
            "pid": 42,
            "name": "available.exe",
            "exe": "",
            "create_time": 1.0,
        }

    monkeypatch.setattr(
        "moj_asystent_core.tools.platform.psutil.process_iter",
        lambda _fields: [IdleProcess(), ProcessWithoutExecutable()],
    )
    platform = WindowsToolPlatform(path_policy=PathPolicy((tmp_path,)))

    result = platform.get_running_processes(RunningProcessesArguments(limit=5))

    assert len(result.processes) == 1
    assert result.processes[0].pid == 42
    assert result.processes[0].executable is None


@pytest.mark.skipif(os.name != "nt", reason="Windows application registry")
def test_application_registry_uses_absolute_system_executables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def record_launch(command, **kwargs) -> None:
        launched.append((tuple(command), kwargs))

    monkeypatch.setenv("MOJ_ASYSTENT_SESSION_CREDENTIAL", "session-secret")
    monkeypatch.setenv("MOJ_ASYSTENT_ACTION_CREDENTIAL", "action-secret")
    monkeypatch.setattr("moj_asystent_core.tools.platform.subprocess.Popen", record_launch)
    platform = WindowsToolPlatform(path_policy=PathPolicy((tmp_path,)))

    platform.open_application(OpenApplicationArguments(application_id="notepad"))

    command, options = launched[0]
    assert Path(command[0]).is_absolute()
    child_environment = options["env"]
    assert isinstance(child_environment, dict)
    assert "MOJ_ASYSTENT_SESSION_CREDENTIAL" not in child_environment
    assert "MOJ_ASYSTENT_ACTION_CREDENTIAL" not in child_environment


@given(st.lists(st.sampled_from(["..", ".", "folder"]), min_size=1, max_size=8))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_path_normalization_never_escapes_approved_root(tmp_path: Path, parts: list[str]) -> None:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    policy = PathPolicy((root,))
    candidate = root.joinpath(*parts, "missing.txt")
    normalized = Path(os.path.abspath(candidate))
    if root not in normalized.parents:
        with pytest.raises(ToolPlatformError):
            policy.destination_file(str(candidate))


def test_symlink_or_reparse_escape_is_rejected_where_supported(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Tworzenie dowiązań nie jest dostępne na tym koncie Windows")
    with pytest.raises(ToolPlatformError):
        PathPolicy((root,)).existing_file(str(link / "secret.txt"))


def test_read_file_limits_size_and_rejects_binary(tmp_path: Path) -> None:
    platform = WindowsToolPlatform(path_policy=PathPolicy((tmp_path,)))
    huge = tmp_path / "huge.txt"
    huge.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"text\x00binary")
    with pytest.raises(ToolPlatformError, match="256"):
        platform.read_file(ReadFileArguments(path=str(huge)))
    with pytest.raises(ToolPlatformError, match="binarny"):
        platform.read_file(ReadFileArguments(path=str(binary)))


def test_read_file_rechecks_the_bytes_read_after_a_size_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = WindowsToolPlatform(path_policy=PathPolicy((tmp_path,)))
    target = tmp_path / "changing.txt"
    target.write_bytes(b"small")
    original_open = Path.open

    class GrowingFile(BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            assert size == MAX_FILE_BYTES + 1
            return super().read(size)

    def changed_open(path: Path, mode: str = "r", *args, **kwargs):
        if path == target and mode == "rb":
            return GrowingFile(b"x" * (MAX_FILE_BYTES + 1))
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", changed_open)

    with pytest.raises(ToolPlatformError, match="256"):
        platform.read_file(ReadFileArguments(path=str(target)))


def test_directory_listing_consumes_only_limit_plus_one_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = WindowsToolPlatform(path_policy=PathPolicy((tmp_path,)))
    items = tuple(tmp_path / f"item-{index}.txt" for index in range(6))
    for item in items:
        item.write_text("x", encoding="utf-8")
    original_iterdir = Path.iterdir
    consumed = 0

    def bounded_iterdir(path: Path):
        if path != tmp_path:
            return original_iterdir(path)

        def entries():
            nonlocal consumed
            for item in items:
                consumed += 1
                if consumed > 3:
                    raise AssertionError("directory listing consumed beyond limit + 1")
                yield item

        return entries()

    monkeypatch.setattr(Path, "iterdir", bounded_iterdir)

    result = platform.list_directory(ListDirectoryArguments(path=str(tmp_path), limit=2))

    assert consumed == 3
    assert len(result.entries) == 2
    assert result.truncated is True


def test_move_collision_and_directory_delete_are_rejected(tmp_path: Path) -> None:
    platform = WindowsToolPlatform(path_policy=PathPolicy((tmp_path,)))
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("source", encoding="utf-8")
    target.write_text("target", encoding="utf-8")
    with pytest.raises(ToolPlatformError, match="nadpisywanie"):
        platform.move_file(MoveFileArguments(source=str(source), destination=str(target)))
    with pytest.raises(ToolPlatformError, match="plikiem"):
        platform.delete_file(DeleteFileArguments(path=str(tmp_path)))


def test_move_fails_closed_if_destination_appears_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = WindowsToolPlatform(path_policy=PathPolicy((tmp_path,)))
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    original_destination_file = platform.paths.destination_file

    def raced_destination(value: str) -> Path:
        checked = original_destination_file(value)
        checked.write_text("raced", encoding="utf-8")
        return checked

    monkeypatch.setattr(platform.paths, "destination_file", raced_destination)

    with pytest.raises(ToolPlatformError, match="przenieść"):
        platform.move_file(MoveFileArguments(source=str(source), destination=str(destination)))

    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "raced"


@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
@pytest.mark.parametrize("name", ["target.txt:stream", "CON", "trailing. "])
def test_windows_special_file_targets_are_rejected(tmp_path: Path, name: str) -> None:
    policy = PathPolicy((tmp_path,))
    with pytest.raises(ToolPlatformError, match="Windows"):
        policy.destination_file(str(tmp_path / name))
