from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ConfigDict, Field

from moj_asystent_core.runtime import CoreRuntime
from moj_asystent_core.telemetry import TelemetrySnapshot
from moj_asystent_core.tools.confirmations import (
    ConfirmationManager,
    ConfirmationRejected,
    arguments_digest,
)
from moj_asystent_core.tools.engine import (
    ConfirmationPresentation,
    ToolDefinition,
    ToolEngine,
    ToolRegistry,
    build_tool_engine,
)
from moj_asystent_core.tools.models import (
    ConfirmationDecision,
    PermissionLevel,
    ToolExecutionResult,
    ToolStatus,
)
from moj_asystent_core.tools.policy import PermissionDecision, PermissionPolicyStore, ToolPreference


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: int = Field(ge=0, le=100)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    doubled: int


class Events:
    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.confirmations = []
        self.resolutions: list[str] = []
        self.results: list[ToolExecutionResult] = []

    @staticmethod
    def can_request_confirmation() -> bool:
        return True

    def publish_tool_status(
        self, operation_id: UUID, call_id: UUID, tool_name: str, status: str
    ) -> None:
        self.statuses.append(status)

    def publish_confirmation_requested(self, request) -> None:
        self.confirmations.append(request)

    def publish_confirmation_resolved(self, request, decision: str) -> None:
        self.resolutions.append(decision)

    def publish_tool_result(
        self, operation_id: UUID, call_id: UUID, result: ToolExecutionResult
    ) -> None:
        self.results.append(result)


def definition(
    permission: PermissionLevel,
    implementation=lambda value: Output(doubled=cast(Arguments, value).value * 2),
    *,
    persistent: bool = False,
    timeout: float = 1,
) -> ToolDefinition:
    return ToolDefinition(
        name="test_tool",
        description="Test",
        input_model=Arguments,
        output_model=Output,
        permission=permission,
        implementation=implementation,
        timeout_seconds=timeout,
        cancellation="cooperative_before_effect",
        persistent_approval=persistent,
        audit_category="test",
        confirmation=lambda value: ConfirmationPresentation(
            action="Wykonać test?",
            target=str(cast(Arguments, value).value),
            details=(),
            risk="Test",
        ),
    )


def engine_for(
    tmp_path: Path,
    permission: PermissionLevel,
    implementation=lambda value: Output(doubled=cast(Arguments, value).value * 2),
    *,
    persistent: bool = False,
    timeout: float = 1,
) -> tuple[ToolEngine, Events]:
    events = Events()
    engine = ToolEngine(
        ToolRegistry(
            (definition(permission, implementation, persistent=persistent, timeout=timeout),)
        ),
        PermissionPolicyStore(tmp_path / "policy.json"),
        ConfirmationManager(),
        events,
    )
    return engine, events


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool_and_strict_arguments(tmp_path: Path) -> None:
    engine, _ = engine_for(tmp_path, PermissionLevel.READ)
    unknown = await engine.execute(
        operation_id=uuid4(), call_id=uuid4(), tool_name="missing_tool", arguments={}
    )
    extra = await engine.execute(
        operation_id=uuid4(),
        call_id=uuid4(),
        tool_name="test_tool",
        arguments={"value": 2, "extra": True},
    )
    assert unknown.status is ToolStatus.FAILURE
    assert extra.status is ToolStatus.FAILURE


@pytest.mark.asyncio
async def test_invalid_non_ascii_tool_name_fails_before_event_publication(tmp_path: Path) -> None:
    engine = ToolEngine(
        ToolRegistry((definition(PermissionLevel.READ),)),
        PermissionPolicyStore(tmp_path / "policy.json"),
        ConfirmationManager(),
        CoreRuntime(),
    )

    result = await engine.execute(
        operation_id=uuid4(), call_id=uuid4(), tool_name="éé", arguments={}
    )

    assert result.tool_name == "invalid_tool"
    assert result.status is ToolStatus.FAILURE


@pytest.mark.asyncio
async def test_confirmation_required_tool_fails_closed_without_a_ui_subscriber(
    tmp_path: Path,
) -> None:
    executed = False

    def implementation(value: BaseModel) -> Output:
        nonlocal executed
        executed = True
        return Output(doubled=cast(Arguments, value).value * 2)

    engine = ToolEngine(
        ToolRegistry((definition(PermissionLevel.SENSITIVE, implementation),)),
        PermissionPolicyStore(tmp_path / "policy.json"),
        ConfirmationManager(),
        CoreRuntime(),
    )

    result = await asyncio.wait_for(
        engine.execute(
            operation_id=uuid4(),
            call_id=uuid4(),
            tool_name="test_tool",
            arguments={"value": 1},
        ),
        0.05,
    )

    assert result.status is ToolStatus.CANCELLED
    assert executed is False


@pytest.mark.asyncio
async def test_read_tool_auto_allows_and_returns_validated_output(tmp_path: Path) -> None:
    engine, events = engine_for(tmp_path, PermissionLevel.READ)
    result = await engine.execute(
        operation_id=uuid4(),
        call_id=uuid4(),
        tool_name="test_tool",
        arguments={"value": 21},
    )
    assert result == ToolExecutionResult(
        tool_name="test_tool",
        status="success",
        message="Narzędzie zakończyło działanie pomyślnie.",
        output={"doubled": 42},
    )
    assert events.confirmations == []


@pytest.mark.asyncio
async def test_write_safe_follows_policy_and_can_persist_allow(tmp_path: Path) -> None:
    engine, events = engine_for(tmp_path, PermissionLevel.WRITE_SAFE, persistent=True)
    operation = uuid4()
    task = asyncio.create_task(
        engine.execute(
            operation_id=operation,
            call_id=uuid4(),
            tool_name="test_tool",
            arguments={"value": 4},
        )
    )
    await asyncio.sleep(0)
    request = events.confirmations[0]
    engine.resolve_confirmation(
        ConfirmationDecision(
            confirmation_id=request.confirmation_id,
            operation_id=str(operation),
            call_id=str(request.call_id),
            tool_name="test_tool",
            arguments_digest=request.arguments_digest,
            decision="always_allow",
        )
    )
    assert (await task).status is ToolStatus.SUCCESS
    assert engine.policy.preference("test_tool") is ToolPreference.ALLOW
    second = await engine.execute(
        operation_id=uuid4(),
        call_id=uuid4(),
        tool_name="test_tool",
        arguments={"value": 5},
    )
    assert second.status is ToolStatus.SUCCESS
    assert len(events.confirmations) == 1


@pytest.mark.asyncio
async def test_low_confidence_voice_forces_confirmation_for_allowed_write_safe_tool(
    tmp_path: Path,
) -> None:
    engine, events = engine_for(tmp_path, PermissionLevel.WRITE_SAFE, persistent=True)
    engine.policy.set_preference("test_tool", ToolPreference.ALLOW)
    operation = uuid4()
    task = asyncio.create_task(
        engine.execute(
            operation_id=operation,
            call_id=uuid4(),
            tool_name="test_tool",
            arguments={"value": 9},
            speech_confidence="low",
        )
    )
    await asyncio.sleep(0)

    assert task.done() is False
    assert len(events.confirmations) == 1
    assert events.confirmations[0].persistent_allowed is False
    engine.resolve_confirmation(
        ConfirmationDecision(
            confirmation_id=events.confirmations[0].confirmation_id,
            operation_id=str(operation),
            call_id=str(events.confirmations[0].call_id),
            tool_name="test_tool",
            arguments_digest=events.confirmations[0].arguments_digest,
            decision="allow",
        )
    )
    assert (await task).status is ToolStatus.SUCCESS


@pytest.mark.asyncio
async def test_failed_persistent_policy_write_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executed = False

    def implementation(value: BaseModel) -> Output:
        nonlocal executed
        executed = True
        return Output(doubled=cast(Arguments, value).value * 2)

    engine, events = engine_for(
        tmp_path, PermissionLevel.WRITE_SAFE, implementation, persistent=True
    )
    operation = uuid4()
    task = asyncio.create_task(
        engine.execute(
            operation_id=operation,
            call_id=uuid4(),
            tool_name="test_tool",
            arguments={"value": 4},
        )
    )
    await asyncio.sleep(0)
    request = events.confirmations[0]
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    engine.resolve_confirmation(
        ConfirmationDecision(
            confirmation_id=request.confirmation_id,
            operation_id=str(operation),
            call_id=str(request.call_id),
            tool_name="test_tool",
            arguments_digest=request.arguments_digest,
            decision="always_allow",
        )
    )

    result = await task

    assert result.status is ToolStatus.FAILURE
    assert engine.policy.preference("test_tool") is ToolPreference.ASK
    assert executed is False


@pytest.mark.asyncio
async def test_sensitive_confirmation_is_single_use_and_never_persistent(tmp_path: Path) -> None:
    engine, events = engine_for(tmp_path, PermissionLevel.SENSITIVE)
    operation = uuid4()
    task = asyncio.create_task(
        engine.execute(
            operation_id=operation,
            call_id=uuid4(),
            tool_name="test_tool",
            arguments={"value": 8},
        )
    )
    await asyncio.sleep(0)
    request = events.confirmations[0]
    decision = ConfirmationDecision(
        confirmation_id=request.confirmation_id,
        operation_id=str(operation),
        call_id=str(request.call_id),
        tool_name="test_tool",
        arguments_digest=request.arguments_digest,
        decision="allow",
    )
    engine.resolve_confirmation(decision)
    assert (await task).status is ToolStatus.SUCCESS
    with pytest.raises(ConfirmationRejected):
        engine.resolve_confirmation(decision)
    assert engine.policy.preference("test_tool") is ToolPreference.ASK


@pytest.mark.asyncio
async def test_confirmation_rejects_expiry_modified_arguments_and_stale_operation() -> None:
    now = [datetime(2030, 1, 1, tzinfo=UTC)]

    def clock() -> datetime:
        return now[0]

    manager = ConfirmationManager(ttl_seconds=5, clock=clock)
    operation = uuid4()
    request = manager.create(
        operation_id=operation,
        call_id=uuid4(),
        tool_name="delete_file",
        arguments={"path": "C:/safe/a.txt"},
        action="Usunąć?",
        target="a.txt",
        details=(),
        risk="Usunięcie",
        persistent_allowed=False,
    )
    with pytest.raises(ConfirmationRejected):
        manager.resolve(
            ConfirmationDecision(
                confirmation_id=request.confirmation_id,
                operation_id=str(operation),
                call_id=str(request.call_id),
                tool_name="delete_file",
                arguments_digest=arguments_digest({"path": "C:/safe/b.txt"}),
                decision="allow",
            )
        )
    for changed in (
        {"operation_id": str(uuid4())},
        {"call_id": str(uuid4())},
        {"tool_name": "move_file"},
    ):
        values = {
            "confirmation_id": request.confirmation_id,
            "operation_id": str(operation),
            "call_id": str(request.call_id),
            "tool_name": "delete_file",
            "arguments_digest": request.arguments_digest,
            "decision": "allow",
            **changed,
        }
        with pytest.raises(ConfirmationRejected):
            manager.resolve(ConfirmationDecision.model_validate(values))
    now[0] += timedelta(seconds=5)
    with pytest.raises(ConfirmationRejected):
        manager.resolve(
            ConfirmationDecision(
                confirmation_id=request.confirmation_id,
                operation_id=str(operation),
                call_id=str(request.call_id),
                tool_name="delete_file",
                arguments_digest=request.arguments_digest,
                decision="allow",
            )
        )
    manager.cancel_operation(operation)
    assert manager.pending() == ()


@pytest.mark.asyncio
async def test_only_one_of_two_concurrent_confirmation_resolutions_succeeds() -> None:
    manager = ConfirmationManager()
    operation = uuid4()
    request = manager.create(
        operation_id=operation,
        call_id=uuid4(),
        tool_name="delete_file",
        arguments={"path": "C:/safe/a.txt"},
        action="Usunąć?",
        target="a.txt",
        details=(),
        risk="Usunięcie",
        persistent_allowed=False,
    )
    decision = ConfirmationDecision(
        confirmation_id=request.confirmation_id,
        operation_id=str(operation),
        call_id=str(request.call_id),
        tool_name=request.tool_name,
        arguments_digest=request.arguments_digest,
        decision="allow",
    )

    async def resolve() -> bool:
        await asyncio.sleep(0)
        try:
            manager.resolve(decision)
        except ConfirmationRejected:
            return False
        return True

    assert sorted(await asyncio.gather(resolve(), resolve())) == [False, True]


@pytest.mark.asyncio
async def test_cancellation_before_confirmation_prevents_execution(tmp_path: Path) -> None:
    executed = False

    def implementation(value: BaseModel) -> Output:
        nonlocal executed
        executed = True
        return Output(doubled=1)

    engine, _ = engine_for(tmp_path, PermissionLevel.SENSITIVE, implementation)
    operation = uuid4()
    task = asyncio.create_task(
        engine.execute(
            operation_id=operation,
            call_id=uuid4(),
            tool_name="test_tool",
            arguments={"value": 1},
        )
    )
    await asyncio.sleep(0)
    engine.cancel_operation(operation)
    result = await task
    assert result.status is ToolStatus.CANCELLED
    assert executed is False


@pytest.mark.asyncio
async def test_timeout_and_failure_never_become_success(tmp_path: Path) -> None:
    slow, _ = engine_for(
        tmp_path,
        PermissionLevel.READ,
        lambda _: (time.sleep(0.05), Output(doubled=1))[1],
        timeout=0.001,
    )
    timed_out = await slow.execute(
        operation_id=uuid4(),
        call_id=uuid4(),
        tool_name="test_tool",
        arguments={"value": 1},
    )
    broken, _ = engine_for(
        tmp_path,
        PermissionLevel.READ,
        lambda _: (_ for _ in ()).throw(RuntimeError("secret trace")),
    )
    failed = await broken.execute(
        operation_id=uuid4(),
        call_id=uuid4(),
        tool_name="test_tool",
        arguments={"value": 1},
    )
    assert timed_out.status is ToolStatus.TIMEOUT
    assert failed.status is ToolStatus.FAILURE
    assert "secret trace" not in failed.message


def test_builtin_registry_has_only_documented_tools_and_permissions(tmp_path: Path) -> None:
    engine = build_tool_engine(
        Events(), policy_path=tmp_path / "policy.json", path_roots=(tmp_path,)
    )
    levels = {item.name: item.permission for item in engine.registry.definitions()}
    assert set(levels) == {
        "get_system_stats",
        "get_running_processes",
        "get_active_window",
        "read_ui_tree",
        "inspect_screen",
        "list_directory",
        "read_file",
        "find_process_using_port",
        "open_application",
        "focus_window",
        "open_folder",
        "set_volume",
        "set_application_volume",
        "restart_approved_process",
        "move_file",
        "delete_file",
        "restart_pc",
        "shutdown_pc",
    }
    assert levels["read_file"] is PermissionLevel.READ
    assert levels["set_volume"] is PermissionLevel.WRITE_SAFE
    assert levels["delete_file"] is PermissionLevel.SENSITIVE
    assert all(
        not item.persistent_approval
        for item in engine.registry.definitions()
        if item.permission is PermissionLevel.SENSITIVE
    )
    definition = engine.registry.get("get_system_stats")
    assert definition is not None
    assert definition.output_model is TelemetrySnapshot


def test_corrupt_policy_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text('{"version": 1, "tools": {"test_tool": "allow"}, "extra": true}')
    policy = PermissionPolicyStore(path)
    assert policy.evaluate("test_tool", PermissionLevel.WRITE_SAFE) is PermissionDecision.CONFIRM
    assert policy.evaluate("test_tool", PermissionLevel.SENSITIVE) is PermissionDecision.CONFIRM


@pytest.mark.parametrize("tool_name", ["restart_pc", "shutdown_pc"])
@pytest.mark.asyncio
async def test_power_actions_always_stop_for_confirmation(tmp_path: Path, tool_name: str) -> None:
    events = Events()
    engine = build_tool_engine(events, policy_path=tmp_path / "policy.json", path_roots=(tmp_path,))
    operation = uuid4()
    task = asyncio.create_task(
        engine.execute(
            operation_id=operation,
            call_id=uuid4(),
            tool_name=tool_name,
            arguments={"delay_seconds": 0},
        )
    )
    await asyncio.sleep(0)
    request = events.confirmations[0]
    assert request.persistent_allowed is False
    engine.cancel_operation(operation)
    assert (await task).status is ToolStatus.CANCELLED


@pytest.mark.asyncio
async def test_destructive_file_confirmation_binds_the_canonical_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("test", encoding="utf-8")
    spelled = str(target).replace("\\", "/")
    events = Events()
    engine = build_tool_engine(events, policy_path=tmp_path / "policy.json", path_roots=(tmp_path,))
    operation = uuid4()
    task = asyncio.create_task(
        engine.execute(
            operation_id=operation,
            call_id=uuid4(),
            tool_name="delete_file",
            arguments={"path": spelled},
        )
    )
    await asyncio.sleep(0)

    request = events.confirmations[0]
    canonical = str(target.resolve())
    assert request.target == canonical
    assert request.arguments_digest == arguments_digest({"path": canonical})

    engine.cancel_operation(operation)
    assert (await task).status is ToolStatus.CANCELLED
