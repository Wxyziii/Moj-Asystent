from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from moj_asystent_core.memory import SQLiteMemoryStore
from moj_asystent_core.runtime import CoreRuntime
from moj_asystent_core.tools.confirmations import ConfirmationRequest
from moj_asystent_core.tools.engine import build_tool_engine
from moj_asystent_core.tools.models import ToolExecutionResult, ToolStatus


class Events:
    def can_request_confirmation(self) -> bool:
        return False

    def publish_tool_status(self, operation_id, call_id, tool_name, status) -> None:
        pass

    def publish_confirmation_requested(self, request: ConfirmationRequest) -> None:
        pass

    def publish_confirmation_resolved(self, request: ConfirmationRequest, decision: str) -> None:
        pass

    def publish_tool_result(self, operation_id, call_id, result: ToolExecutionResult) -> None:
        pass


@pytest.mark.asyncio
async def test_memory_tools_are_typed_and_writes_fail_closed_without_ui_confirmation(
    tmp_path: Path,
) -> None:
    runtime = CoreRuntime()
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    engine = build_tool_engine(Events(), memory_store=store)
    names = {definition.name for definition in engine.registry.definitions()}
    assert len(names) == 25
    assert {
        "remember_preference",
        "remember_memory",
        "remember_alias",
        "list_memories",
        "forget_memory",
        "resolve_alias",
        "create_routine",
    } <= names

    result = await engine.execute(
        operation_id=uuid4(),
        call_id=uuid4(),
        tool_name="remember_memory",
        arguments={"key": "note", "value": "jawna informacja"},
    )
    assert result.status is ToolStatus.CANCELLED
    assert store.list_memories() == ()
    engine.shutdown()
    await runtime.shutdown()
