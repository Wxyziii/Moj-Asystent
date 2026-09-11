"""Strict public models for the Milestone 6 tool boundary."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PermissionLevel(StrEnum):
    READ = "read"
    WRITE_SAFE = "write.safe"
    SENSITIVE = "sensitive"


class ToolStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    CONFIRMATION_REQUIRED = "confirmation_required"


class ToolExecutionResult(StrictModel):
    tool_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    status: ToolStatus
    message: Annotated[str, Field(min_length=1, max_length=512)]
    output: dict[str, JsonValue] | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is ToolStatus.SUCCESS


class NoArguments(StrictModel):
    pass


class SystemStatsOutput(StrictModel):
    cpu_percent: Annotated[float, Field(ge=0, le=100)]
    memory_percent: Annotated[float, Field(ge=0, le=100)]
    memory_used_bytes: Annotated[StrictInt, Field(ge=0)]
    memory_total_bytes: Annotated[StrictInt, Field(gt=0)]
    disk_percent: Annotated[float, Field(ge=0, le=100)]
    disk_free_bytes: Annotated[StrictInt, Field(ge=0)]


class ProcessSummary(StrictModel):
    pid: Annotated[StrictInt, Field(gt=0)]
    name: Annotated[str, Field(min_length=1, max_length=260)]
    executable: Annotated[str, Field(min_length=1, max_length=1_024)] | None = None
    create_time: Annotated[float, Field(gt=0)] | None = None


class RunningProcessesArguments(StrictModel):
    limit: Annotated[StrictInt, Field(ge=1, le=200)] = 50


class RunningProcessesOutput(StrictModel):
    processes: tuple[ProcessSummary, ...]
    truncated: bool


class ContextProviderArguments(StrictModel):
    reason: Annotated[str, Field(min_length=1, max_length=256)]


class PathArguments(StrictModel):
    path: Annotated[str, Field(min_length=1, max_length=1_024)]


class ListDirectoryArguments(PathArguments):
    limit: Annotated[StrictInt, Field(ge=1, le=200)] = 100


class DirectoryEntry(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=260)]
    kind: Literal["file", "directory", "other"]
    size_bytes: Annotated[StrictInt, Field(ge=0)] | None = None


class ListDirectoryOutput(StrictModel):
    path: str
    entries: tuple[DirectoryEntry, ...]
    truncated: bool


class ReadFileArguments(PathArguments):
    encoding: Literal["utf-8"] = "utf-8"


class ReadFileOutput(StrictModel):
    path: str
    content: Annotated[str, Field(max_length=262_144)]
    encoding: Literal["utf-8"]
    size_bytes: Annotated[StrictInt, Field(ge=0, le=262_144)]


class FindProcessByPortArguments(StrictModel):
    port: Annotated[StrictInt, Field(ge=1, le=65_535)]
    protocol: Literal["tcp", "udp"] = "tcp"


class FindProcessByPortOutput(StrictModel):
    found: bool
    port: Annotated[StrictInt, Field(ge=1, le=65_535)]
    protocol: Literal["tcp", "udp"]
    process: ProcessSummary | None = None


class OpenApplicationArguments(StrictModel):
    application_id: Literal["notepad", "calculator", "explorer", "settings"]


class FocusWindowArguments(StrictModel):
    handle: Annotated[StrictInt, Field(gt=0)]
    expected_pid: Annotated[StrictInt, Field(gt=0)]


class SetVolumeArguments(StrictModel):
    volume: Annotated[StrictInt, Field(ge=0, le=100)]


class SetApplicationVolumeArguments(SetVolumeArguments):
    pid: Annotated[StrictInt, Field(gt=0)]
    expected_create_time: Annotated[float, Field(gt=0)]


class ActionOutput(StrictModel):
    changed: bool
    target: Annotated[str, Field(min_length=1, max_length=1_024)]
    previous_value: JsonScalar = None
    current_value: JsonScalar = None


class RestartProcessArguments(StrictModel):
    pid: Annotated[StrictInt, Field(gt=0)]
    expected_create_time: Annotated[float, Field(gt=0)]
    executable_name: Annotated[str, Field(min_length=1, max_length=260)]

    @field_validator("executable_name")
    @classmethod
    def basename_only(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("Executable name must be a basename")
        return value


class MoveFileArguments(StrictModel):
    source: Annotated[str, Field(min_length=1, max_length=1_024)]
    destination: Annotated[str, Field(min_length=1, max_length=1_024)]
    overwrite: Literal[False] = False


class DeleteFileArguments(PathArguments):
    pass


class PowerActionArguments(StrictModel):
    delay_seconds: Annotated[StrictInt, Field(ge=0, le=300)] = 0


class PowerActionOutput(StrictModel):
    scheduled: bool
    action: Literal["restart", "shutdown"]
    delay_seconds: Annotated[StrictInt, Field(ge=0, le=300)]


class ModelToolCall(StrictModel):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    arguments: dict[str, JsonValue]


class ModelToolDefinition(StrictModel):
    type: Literal["function"] = "function"
    function: dict[str, JsonValue]


class ConfirmationDecision(StrictModel):
    confirmation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")]
    operation_id: str
    call_id: str
    tool_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    arguments_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    decision: Literal["allow", "cancel", "always_allow"]
