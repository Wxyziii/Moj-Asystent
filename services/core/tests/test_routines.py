from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from moj_asystent_core.memory import RoutineStep, SQLiteMemoryStore
from moj_asystent_core.routines import RoutineCreateRequest, RoutineService, RoutineValidationError
from moj_asystent_core.tools.models import ToolExecutionResult, ToolStatus


class Registry:
    def validate(self, tool_name, arguments):
        if tool_name not in {"first", "second", "slow"}:
            raise ValueError("unknown")
        return (None, arguments)


class Engine:
    registry = Registry()

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.cancelled: set = set()
        self.release = asyncio.Event()

    async def execute(self, *, operation_id, call_id, tool_name, arguments):
        if tool_name == "slow":
            await self.release.wait()
        status = (
            ToolStatus.CANCELLED
            if operation_id in self.cancelled
            else ToolStatus.FAILURE
            if self.fail
            else ToolStatus.SUCCESS
        )
        return ToolExecutionResult(
            tool_name=tool_name,
            status=status,
            message="nie" if self.fail else "gotowe",
        )

    def cancel_operation(self, operation_id) -> None:
        self.cancelled.add(operation_id)
        self.release.set()


@pytest.mark.asyncio
async def test_routine_validation_and_stop_on_first_failure(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    engine = Engine(fail=True)
    service = RoutineService(store, engine)
    with pytest.raises(RoutineValidationError):
        await service.create(
            RoutineCreateRequest("Zła", (RoutineStep(tool_name="shell", arguments={}),))
        )
    routine = await service.create(
        RoutineCreateRequest(
            "Sekwencja",
            (
                RoutineStep(tool_name="first", arguments={}),
                RoutineStep(tool_name="second", arguments={}),
            ),
        )
    )
    result = await service.run(routine.routine_id)
    assert result.status == "failure"
    assert len(result.steps) == 1


@pytest.mark.asyncio
async def test_running_routine_cannot_be_deleted_and_can_be_cancelled(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    engine = Engine()
    service = RoutineService(store, engine)
    routine = await service.create(
        RoutineCreateRequest("Powolna", (RoutineStep(tool_name="slow", arguments={}),))
    )
    operation = uuid4()
    task = asyncio.create_task(service.run(routine.routine_id, operation))
    await asyncio.sleep(0)
    with pytest.raises(RoutineValidationError):
        await service.delete(routine.routine_id)
    assert await service.cancel(operation)
    result = await task
    assert result.status == "cancelled"
    assert await service.delete(routine.routine_id)


@pytest.mark.asyncio
async def test_shutdown_waits_for_active_routines(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    engine = Engine()
    service = RoutineService(store, engine)
    routine = await service.create(
        RoutineCreateRequest("Zamykanie", (RoutineStep(tool_name="slow", arguments={}),))
    )
    task = asyncio.create_task(service.run(routine.routine_id))
    await asyncio.sleep(0)
    await service.shutdown()
    assert task.done()
    assert (await task).status == "cancelled"
