from __future__ import annotations

import os
from pathlib import Path

import psutil
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from moj_asystent_core.tools.models import (
    DeleteFileArguments,
    MoveFileArguments,
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


@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
@pytest.mark.parametrize("name", ["target.txt:stream", "CON", "trailing. "])
def test_windows_special_file_targets_are_rejected(tmp_path: Path, name: str) -> None:
    policy = PathPolicy((tmp_path,))
    with pytest.raises(ToolPlatformError, match="Windows"):
        policy.destination_file(str(tmp_path / name))
