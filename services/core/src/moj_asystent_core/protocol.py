"""Strict protocol-v1 models shared conceptually with ``packages/protocol``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROTOCOL_VERSION = "1.0"
AssistantState = Literal[
    "idle",
    "wake_detected",
    "listening",
    "transcribing",
    "thinking",
    "speaking",
    "follow_up",
    "error",
]


class ProtocolValidationError(ValueError):
    """Raised when untrusted wire data is not a supported protocol-v1 event."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventPayload(StrictModel):
    pass


class ClientHelloPayload(EventPayload):
    client_id: Annotated[str, Field(min_length=1, max_length=128)]
    protocol_version: Literal["1.0"]


class SystemHealthPayload(EventPayload):
    service: Literal["core"] = "core"
    status: Literal["ready", "stopping"]
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    assistant_state: AssistantState


class AssistantStateChangedPayload(EventPayload):
    previous_state: AssistantState | None
    state: AssistantState


class SystemErrorPayload(EventPayload):
    code: Literal["invalid_message", "unsupported_protocol", "invalid_origin", "message_too_large"]
    message: Annotated[str, Field(min_length=1, max_length=256)]


class EventBase(StrictModel):
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    event_id: UUID
    occurred_at: datetime
    correlation_id: UUID | None = None


class ClientHello(EventBase):
    type: Literal["client.hello"]
    payload: ClientHelloPayload


class SystemHealth(EventBase):
    type: Literal["system.health"]
    payload: SystemHealthPayload


class AssistantStateChanged(EventBase):
    type: Literal["assistant.state.changed"]
    payload: AssistantStateChangedPayload


class SystemError(EventBase):
    type: Literal["system.error"]
    payload: SystemErrorPayload


type ProtocolEvent = ClientHello | SystemHealth | AssistantStateChanged | SystemError
_EVENT_MODELS: dict[str, type[ProtocolEvent]] = {
    "client.hello": ClientHello,
    "system.health": SystemHealth,
    "assistant.state.changed": AssistantStateChanged,
    "system.error": SystemError,
}


def parse_event(value: object) -> ProtocolEvent:
    if not isinstance(value, dict):
        raise ProtocolValidationError("Protocol event must be an object")
    version = value.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ProtocolValidationError(
            f"Unsupported protocol_version {version!r}; expected {PROTOCOL_VERSION!r}"
        )
    event_type = value.get("type")
    model = _EVENT_MODELS.get(event_type) if isinstance(event_type, str) else None
    if model is None:
        raise ProtocolValidationError(f"Unsupported event type {event_type!r}")
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ProtocolValidationError(error.json(include_url=False)) from error


def new_event(
    event_type: Literal["system.health", "assistant.state.changed", "system.error"],
    payload: EventPayload,
    *,
    correlation_id: UUID | None = None,
) -> SystemHealth | AssistantStateChanged | SystemError:
    values = {
        "protocol_version": PROTOCOL_VERSION,
        "event_id": uuid4(),
        "occurred_at": datetime.now(UTC),
        "correlation_id": correlation_id,
        "type": event_type,
        "payload": payload,
    }
    return cast(SystemHealth | AssistantStateChanged | SystemError, parse_event(values))
