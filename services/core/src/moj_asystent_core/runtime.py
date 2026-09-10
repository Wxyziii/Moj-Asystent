"""Single-event-loop state ownership and bounded, ordered subscriptions."""

import asyncio
from typing import Literal
from uuid import UUID

from .protocol import (
    PROTOCOL_VERSION,
    AssistantResponseCompletedPayload,
    AssistantResponseDeltaPayload,
    AssistantResponseStartedPayload,
    AssistantState,
    AssistantStateChanged,
    AudioTranscriptFinalPayload,
    ModelStatusChangedPayload,
    ProtocolEvent,
    SystemHealthPayload,
    new_event,
)
from .providers import SpeechToTextResponse
from .state import AssistantStateMachine


class CoreRuntime:
    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._machine = AssistantStateMachine()
        self._subscriptions: dict[asyncio.Queue[ProtocolEvent | None], UUID] = {}
        self.sessions: set[asyncio.Task[None]] = set()
        self.stopping = False
        self._model_status: ModelStatusChangedPayload | None = None

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

    @property
    def state(self) -> AssistantState:
        self._assert_owner()
        return self._machine.state

    def subscribe(self, hello_id: UUID) -> asyncio.Queue[ProtocolEvent | None]:
        self._assert_owner()
        if self.stopping:
            raise RuntimeError("Core is stopping")
        queue: asyncio.Queue[ProtocolEvent | None] = asyncio.Queue(maxsize=32)
        # No await between snapshot capture and registration: no missed transitions.
        queue.put_nowait(new_event("system.health", self.health_payload(), correlation_id=hello_id))
        snapshot = self._machine.synchronize().model_copy(update={"correlation_id": hello_id})
        queue.put_nowait(snapshot)
        if self._model_status is not None:
            queue.put_nowait(
                new_event("model.status.changed", self._model_status, correlation_id=hello_id)
            )
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

    def publish(self, event: ProtocolEvent) -> None:
        self._assert_owner()
        if self.stopping:
            raise RuntimeError("Core is stopping")
        for queue, hello_id in tuple(self._subscriptions.items()):
            if queue.full():
                del self._subscriptions[queue]
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(event.model_copy(update={"correlation_id": hello_id}))

    def publish_transcript(self, operation_id: UUID, response: SpeechToTextResponse) -> None:
        self.publish(
            new_event(
                "audio.transcript.final",
                AudioTranscriptFinalPayload(
                    operation_id=operation_id,
                    text=response.transcript,
                    language="pl",
                    duration_ms=response.duration_ms,
                ),
            )
        )

    def publish_response_started(
        self, operation_id: UUID, *, model: str, mode: Literal["voice", "text"]
    ) -> None:
        self.publish(
            new_event(
                "assistant.response.started",
                AssistantResponseStartedPayload(operation_id=operation_id, model=model, mode=mode),
            )
        )

    def publish_response_delta(self, operation_id: UUID, sequence: int, text: str) -> None:
        self.publish(
            new_event(
                "assistant.response.delta",
                AssistantResponseDeltaPayload(
                    operation_id=operation_id, sequence=sequence, text=text
                ),
            )
        )

    def publish_response_completed(
        self,
        operation_id: UUID,
        text: str,
        *,
        spoken_text: str | None,
        model: str,
    ) -> None:
        self.publish(
            new_event(
                "assistant.response.completed",
                AssistantResponseCompletedPayload(
                    operation_id=operation_id,
                    text=text,
                    spoken_text=spoken_text,
                    kind="local_model",
                    model=model,
                ),
            )
        )

    def set_model_status(self, payload: ModelStatusChangedPayload) -> None:
        self._model_status = payload
        self.publish(new_event("model.status.changed", payload))

    async def shutdown(self) -> None:
        self._assert_owner()
        self.stopping = True
        sessions = tuple(self.sessions)
        for session in sessions:
            session.cancel()
        await asyncio.gather(*sessions, return_exceptions=True)
        self.sessions.clear()
        self._subscriptions.clear()
