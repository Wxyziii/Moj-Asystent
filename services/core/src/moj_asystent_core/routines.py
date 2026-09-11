"""Bounded execution of persisted, typed tool routines."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .memory import (
    MAX_ROUTINE_STEPS,
    RoutineRecord,
    RoutineStep,
    RoutineSummary,
    SQLiteMemoryStore,
)
from .tools.models import JsonValue, ToolExecutionResult, ToolStatus


class RoutineToolRegistry(Protocol):
    def validate(
        self, name: str, arguments: dict[str, JsonValue], /
    ) -> tuple[object, BaseModel]: ...


class RoutineToolEngine(Protocol):
    @property
    def registry(self) -> RoutineToolRegistry: ...

    async def execute(
        self,
        *,
        operation_id: UUID,
        call_id: UUID,
        tool_name: str,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult: ...

    def cancel_operation(self, operation_id: UUID) -> None: ...


class RoutineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RoutineRunStep(RoutineModel):
    index: int = Field(ge=0, lt=MAX_ROUTINE_STEPS)
    tool_name: str = Field(min_length=2, max_length=64)
    status: ToolStatus
    message: str = Field(min_length=1, max_length=512)


class RoutineRunResult(RoutineModel):
    operation_id: UUID
    routine_id: UUID
    routine_name: str = Field(min_length=1, max_length=96)
    status: Literal["success", "failure", "cancelled"]
    steps: tuple[RoutineRunStep, ...] = Field(max_length=MAX_ROUTINE_STEPS)


class RoutineValidationError(ValueError):
    pass


@dataclass(frozen=True)
class RoutineCreateRequest:
    name: str
    steps: tuple[RoutineStep, ...]
    description: str | None = None


class RoutineService:
    def __init__(self, store: SQLiteMemoryStore, engine: RoutineToolEngine) -> None:
        self._store = store
        self._engine = engine
        self._active: dict[UUID, asyncio.Event] = {}
        self._active_routines: dict[UUID, UUID] = {}
        self._tasks: set[asyncio.Task[object]] = set()
        self._lock = asyncio.Lock()

    async def create(self, request: RoutineCreateRequest) -> RoutineRecord:
        self._validate_steps(request.steps)
        return await asyncio.to_thread(
            self._store.create_routine,
            request.name,
            request.steps,
            description=request.description,
        )

    async def list(self) -> tuple[RoutineSummary, ...]:
        return await asyncio.to_thread(self._store.list_routines)

    async def inspect(self, routine_id: UUID) -> RoutineRecord | None:
        return await asyncio.to_thread(self._store.get_routine, routine_id)

    async def rename(self, routine_id: UUID, name: str) -> RoutineRecord:
        return await asyncio.to_thread(self._store.rename_routine, routine_id, name)

    async def delete(self, routine_id: UUID) -> bool:
        async with self._lock:
            if routine_id in self._active_routines.values():
                raise RoutineValidationError("Nie można usunąć uruchomionej rutyny.")
        return await asyncio.to_thread(self._store.delete_routine, routine_id)

    async def run(self, routine_id: UUID, operation_id: UUID | None = None) -> RoutineRunResult:
        operation = operation_id or uuid4()
        task = asyncio.current_task()
        async with self._lock:
            if operation in self._active:
                raise RoutineValidationError("Ta operacja rutyny już działa.")
            cancellation = asyncio.Event()
            self._active[operation] = cancellation
            self._active_routines[operation] = routine_id
            if task is not None:
                self._tasks.add(task)
        try:
            routine = await asyncio.to_thread(self._store.get_routine, routine_id)
            if routine is None:
                raise RoutineValidationError("Rutyna nie istnieje.")
            self._validate_steps(routine.steps)
            completed: list[RoutineRunStep] = []
            for index, step in enumerate(routine.steps):
                if cancellation.is_set():
                    return RoutineRunResult(
                        operation_id=operation,
                        routine_id=routine_id,
                        routine_name=routine.name,
                        status="cancelled",
                        steps=tuple(completed),
                    )
                result = await self._engine.execute(
                    operation_id=operation,
                    call_id=uuid4(),
                    tool_name=step.tool_name,
                    arguments=step.arguments,
                )
                completed.append(
                    RoutineRunStep(
                        index=index,
                        tool_name=step.tool_name,
                        status=result.status,
                        message=result.message,
                    )
                )
                if result.status is not ToolStatus.SUCCESS:
                    return RoutineRunResult(
                        operation_id=operation,
                        routine_id=routine_id,
                        routine_name=routine.name,
                        status="cancelled" if result.status is ToolStatus.CANCELLED else "failure",
                        steps=tuple(completed),
                    )
            return RoutineRunResult(
                operation_id=operation,
                routine_id=routine_id,
                routine_name=routine.name,
                status="success",
                steps=tuple(completed),
            )
        finally:
            async with self._lock:
                self._active.pop(operation, None)
                self._active_routines.pop(operation, None)
                if task is not None:
                    self._tasks.discard(task)

    async def cancel(self, operation_id: UUID) -> bool:
        async with self._lock:
            cancellation = self._active.get(operation_id)
            if cancellation is None:
                return False
            cancellation.set()
        self._engine.cancel_operation(operation_id)
        return True

    async def shutdown(self) -> None:
        current = asyncio.current_task()
        async with self._lock:
            operations = tuple(self._active)
            for cancellation in self._active.values():
                cancellation.set()
            tasks = tuple(task for task in self._tasks if task is not current)
        for operation in operations:
            self._engine.cancel_operation(operation)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _validate_steps(self, steps: tuple[RoutineStep, ...]) -> None:
        if not 1 <= len(steps) <= MAX_ROUTINE_STEPS:
            raise RoutineValidationError("Rutyna musi mieć od 1 do 16 kroków.")
        for step in steps:
            try:
                self._engine.registry.validate(step.tool_name, step.arguments)
            except Exception as error:
                raise RoutineValidationError(
                    f"Krok rutyny odwołuje się do nieprawidłowego narzędzia: {step.tool_name}."
                ) from error
