"""Single-event-loop state ownership and bounded, ordered subscriptions."""

import asyncio
from uuid import UUID

from .protocol import (
    PROTOCOL_VERSION,
    AssistantState,
    AssistantStateChanged,
    ProtocolEvent,
    SystemHealthPayload,
    new_event,
)
from .state import AssistantStateMachine


class CoreRuntime:
    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._machine = AssistantStateMachine()
        self._subscriptions: dict[asyncio.Queue[ProtocolEvent | None], UUID] = {}
        self.sessions: set[asyncio.Task[None]] = set()
        self.stopping = False

    def _assert_owner(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Core state must be accessed on its owning event loop")

    def health_payload(self) -> SystemHealthPayload:
        self._assert_owner()
        return SystemHealthPayload(
            service="core",
            protocol_version=PROTOCOL_VERSION,
            status="stopping" if self.stopping else "ready",
            assistant_state=self._machine.state,
        )

    def subscribe(self, hello_id: UUID) -> asyncio.Queue[ProtocolEvent | None]:
        self._assert_owner()
        if self.stopping:
            raise RuntimeError("Core is stopping")
        queue: asyncio.Queue[ProtocolEvent | None] = asyncio.Queue(maxsize=32)
        # No await between snapshot capture and registration: no missed transitions.
        queue.put_nowait(new_event("system.health", self.health_payload(), correlation_id=hello_id))
        snapshot = self._machine.synchronize().model_copy(update={"correlation_id": hello_id})
        queue.put_nowait(snapshot)
        self._subscriptions[queue] = hello_id
        return queue

    def unsubscribe(self, queue: asyncio.Queue[ProtocolEvent | None]) -> None:
        self._assert_owner()
        self._subscriptions.pop(queue, None)

    def transition(
        self, target: AssistantState, *, expected_state: AssistantState | None = None
    ) -> AssistantStateChanged:
        self._assert_owner()
        if self.stopping:
            raise RuntimeError("Core is stopping")
        if expected_state is not None and self._machine.state != expected_state:
            raise RuntimeError("Stale state transition")
        event = self._machine.transition(target)
        # Validation, mutation and publication form one uninterrupted operation.
        for queue, hello_id in tuple(self._subscriptions.items()):
            if queue.full():
                del self._subscriptions[queue]
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(
                    None
                )  # Reconnect for a fresh snapshot; never silently lose events.
            else:
                queue.put_nowait(event.model_copy(update={"correlation_id": hello_id}))
        return event

    async def shutdown(self) -> None:
        self._assert_owner()
        self.stopping = True
        sessions = tuple(self.sessions)
        for session in sessions:
            session.cancel()
        await asyncio.gather(*sessions, return_exceptions=True)
        self.sessions.clear()
        self._subscriptions.clear()
