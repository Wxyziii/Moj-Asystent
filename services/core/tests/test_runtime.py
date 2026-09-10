import asyncio
from uuid import uuid4

import pytest

from moj_asystent_core.runtime import CoreRuntime
from moj_asystent_core.state import InvalidStateTransition


@pytest.mark.asyncio
async def test_multiple_subscribers_receive_one_ordered_authoritative_state() -> None:
    runtime = CoreRuntime()
    first_id, second_id = uuid4(), uuid4()
    first, second = runtime.subscribe(first_id), runtime.subscribe(second_id)
    for queue, hello_id in [(first, first_id), (second, second_id)]:
        assert queue.get_nowait().correlation_id == hello_id
        snapshot = queue.get_nowait()
        assert snapshot.payload.previous_state is None
        assert snapshot.correlation_id == hello_id
    event = runtime.transition("listening", expected_state="idle")
    first_event, second_event = first.get_nowait(), second.get_nowait()
    assert first_event.event_id == second_event.event_id == event.event_id
    assert first_event.payload == second_event.payload == event.payload
    assert first_event.correlation_id == first_id
    assert second_event.correlation_id == second_id
    with pytest.raises(RuntimeError, match="Stale"):
        runtime.transition("error", expected_state="idle")
    assert runtime.health_payload().assistant_state == "listening"
    assert first.empty() and second.empty()
    late = runtime.subscribe(uuid4())
    late.get_nowait()
    assert late.get_nowait().payload.state == "listening"
    await runtime.shutdown()
    with pytest.raises(RuntimeError, match="stopping"):
        runtime.transition("idle")


@pytest.mark.asyncio
async def test_slow_subscriber_is_evicted_without_blocking_the_others() -> None:
    runtime = CoreRuntime()
    slow, fast = runtime.subscribe(uuid4()), runtime.subscribe(uuid4())
    for _ in range(20):
        runtime.transition("listening")
        runtime.transition("idle")
        while not fast.empty():
            assert fast.get_nowait() is not None
    assert slow.get_nowait() is None
    assert slow.empty()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_foreign_event_loop_cannot_mutate_state() -> None:
    runtime = CoreRuntime()

    async def foreign() -> None:
        runtime.transition("listening")

    with pytest.raises(RuntimeError, match="owning"):
        await asyncio.to_thread(lambda: asyncio.run(foreign()))
    assert runtime.health_payload().assistant_state == "idle"


def test_invalid_initial_state_fails_at_construction() -> None:
    from moj_asystent_core.state import AssistantStateMachine

    with pytest.raises(InvalidStateTransition):
        AssistantStateMachine("unknown")
