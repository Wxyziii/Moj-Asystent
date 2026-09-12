"""Strict protocol-v1 models shared conceptually with ``packages/protocol``."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

PROTOCOL_VERSION = "1.4"
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

    def __init__(
        self,
        message: str,
        code: Literal[
            "invalid_message", "unsupported_protocol", "message_too_large"
        ] = "invalid_message",
    ) -> None:
        super().__init__(message)
        self.code = code


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventPayload(StrictModel):
    pass


class ClientHelloPayload(EventPayload):
    client_id: Annotated[str, Field(min_length=1, max_length=128)]
    protocol_version: Literal["1.4"]


class SystemHealthPayload(EventPayload):
    service: Literal["core"]
    status: Literal["ready", "stopping"]
    protocol_version: Literal["1.4"]
    assistant_state: AssistantState


class AssistantStateChangedPayload(EventPayload):
    previous_state: AssistantState | None
    state: AssistantState


class AudioTranscriptFinalPayload(EventPayload):
    operation_id: UUID
    text: Annotated[str, Field(min_length=1, max_length=8_192)]
    language: Literal["pl"]
    duration_ms: Annotated[StrictInt, Field(gt=0, le=120_000)]

    @field_validator("operation_id", mode="before")
    @classmethod
    def validate_operation_id(cls, value: object) -> object:
        return _validate_hyphenated_uuid(value)


class AssistantResponseStartedPayload(EventPayload):
    operation_id: UUID
    model: Annotated[str, Field(min_length=1, max_length=128)]
    mode: Literal["voice", "text"]
    provider: Literal["ollama", "llama_cpp", "openrouter"]
    tier: Literal["fast", "quality", "deep"]
    location: Literal["local", "cloud"]
    fallback_reason: Annotated[str, Field(min_length=1, max_length=512)] | None

    @field_validator("operation_id", mode="before")
    @classmethod
    def validate_operation_id(cls, value: object) -> object:
        return _validate_hyphenated_uuid(value)


class AssistantResponseDeltaPayload(EventPayload):
    operation_id: UUID
    sequence: Annotated[StrictInt, Field(ge=0, le=100_000)]
    text: Annotated[str, Field(min_length=1, max_length=4_096)]

    @field_validator("operation_id", mode="before")
    @classmethod
    def validate_operation_id(cls, value: object) -> object:
        return _validate_hyphenated_uuid(value)


class AssistantResponseCompletedPayload(EventPayload):
    operation_id: UUID
    text: Annotated[str, Field(min_length=1, max_length=8_192)]
    spoken_text: Annotated[str, Field(min_length=1, max_length=1_024)] | None
    kind: Literal["local_model", "cloud_model"]
    model: Annotated[str, Field(min_length=1, max_length=128)]
    provider: Literal["ollama", "llama_cpp", "openrouter"]
    tier: Literal["fast", "quality", "deep"]
    location: Literal["local", "cloud"]
    fallback_reason: Annotated[str, Field(min_length=1, max_length=512)] | None

    @field_validator("operation_id", mode="before")
    @classmethod
    def validate_operation_id(cls, value: object) -> object:
        return _validate_hyphenated_uuid(value)


class ModelStatusChangedPayload(EventPayload):
    provider: Literal["ollama", "llama_cpp", "openrouter"]
    model: Annotated[str, Field(min_length=1, max_length=128)]
    tier: Literal["fast", "quality", "deep"]
    location: Literal["local", "cloud"]
    status: Literal["unavailable", "missing", "loading", "ready", "error"]
    detail: Annotated[str, Field(min_length=1, max_length=256)] | None


class ToolExecutionStatusPayload(EventPayload):
    operation_id: UUID
    call_id: UUID
    tool_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    status: Literal["requested", "confirmation_required", "executing"]

    @field_validator("operation_id", "call_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object) -> object:
        return _validate_hyphenated_uuid(value)


class ConfirmationDetail(EventPayload):
    label: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[str, Field(min_length=1, max_length=1_024)]


class ToolConfirmationRequestedPayload(EventPayload):
    confirmation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")]
    operation_id: UUID
    call_id: UUID
    tool_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    arguments_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    action: Annotated[str, Field(min_length=1, max_length=256)]
    target: Annotated[str, Field(min_length=1, max_length=1_024)]
    details: tuple[ConfirmationDetail, ...] = Field(max_length=12)
    risk: Annotated[str, Field(min_length=1, max_length=512)]
    expires_at: datetime
    persistent_allowed: bool

    @field_validator("operation_id", "call_id", mode="before")
    @classmethod
    def validate_operation_id(cls, value: object) -> object:
        return _validate_hyphenated_uuid(value)

    @field_validator("expires_at", mode="before")
    @classmethod
    def validate_expiry(cls, value: object) -> object:
        return EventBase.validate_timestamp(value)


class ToolConfirmationResolvedPayload(EventPayload):
    confirmation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")]
    operation_id: UUID
    call_id: UUID
    tool_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    decision: Literal["allow", "cancel", "always_allow"]

    @field_validator("operation_id", "call_id", mode="before")
    @classmethod
    def validate_operation_id(cls, value: object) -> object:
        return _validate_hyphenated_uuid(value)


class ToolResultPayload(EventPayload):
    operation_id: UUID
    call_id: UUID
    tool_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    status: Literal["success", "failure", "denied", "cancelled", "timeout"]
    message: Annotated[str, Field(min_length=1, max_length=512)]

    @field_validator("operation_id", "call_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object) -> object:
        return _validate_hyphenated_uuid(value)


class SystemErrorPayload(EventPayload):
    code: Literal["invalid_message", "unsupported_protocol", "invalid_origin", "message_too_large"]
    message: Annotated[str, Field(min_length=1, max_length=256)]


class WatcherNotificationPayload(EventPayload):
    watcher_id: UUID
    notification_id: UUID
    watcher_type: Literal["window", "process", "file", "resource", "build", "download"]
    event_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")]
    title: Annotated[str, Field(min_length=1, max_length=128)]
    message: Annotated[str, Field(min_length=1, max_length=512)]
    target: Annotated[str, Field(min_length=1, max_length=1_024)]
    status: Literal["active", "paused", "completed", "failed", "cancelled", "expired"]
    actions: tuple[Literal["inspect", "later", "stop"], ...] = Field(max_length=3)

    @field_validator("watcher_id", "notification_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object) -> object:
        return _validate_hyphenated_uuid(value)


class EventBase(StrictModel):
    protocol_version: Literal["1.4"]
    event_id: UUID
    occurred_at: datetime
    correlation_id: UUID | None

    @field_validator("event_id", "correlation_id", mode="before")
    @classmethod
    def validate_uuid(cls, value: object) -> object:
        return None if value is None else _validate_hyphenated_uuid(value)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def validate_timestamp(cls, value: object) -> object:
        if isinstance(value, datetime):
            offset = value.utcoffset()
            if offset is None or offset.total_seconds() != 0:
                raise ValueError("Expected UTC time")
            return value
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)", value
        ):
            raise ValueError("Expected an RFC3339 UTC timestamp")
        return value


class ClientHello(EventBase):
    type: Literal["client.hello"]
    payload: ClientHelloPayload


class SystemHealth(EventBase):
    type: Literal["system.health"]
    payload: SystemHealthPayload


class AssistantStateChanged(EventBase):
    type: Literal["assistant.state.changed"]
    payload: AssistantStateChangedPayload


class AudioTranscriptFinal(EventBase):
    type: Literal["audio.transcript.final"]
    payload: AudioTranscriptFinalPayload


class AssistantResponseStarted(EventBase):
    type: Literal["assistant.response.started"]
    payload: AssistantResponseStartedPayload


class AssistantResponseDelta(EventBase):
    type: Literal["assistant.response.delta"]
    payload: AssistantResponseDeltaPayload


class AssistantResponseCompleted(EventBase):
    type: Literal["assistant.response.completed"]
    payload: AssistantResponseCompletedPayload


class ModelStatusChanged(EventBase):
    type: Literal["model.status.changed"]
    payload: ModelStatusChangedPayload


class ToolExecutionStatus(EventBase):
    type: Literal["tool.execution.status"]
    payload: ToolExecutionStatusPayload


class ToolConfirmationRequested(EventBase):
    type: Literal["tool.confirmation.requested"]
    payload: ToolConfirmationRequestedPayload


class ToolConfirmationResolved(EventBase):
    type: Literal["tool.confirmation.resolved"]
    payload: ToolConfirmationResolvedPayload


class ToolResult(EventBase):
    type: Literal["tool.result"]
    payload: ToolResultPayload


class SystemError(EventBase):
    type: Literal["system.error"]
    payload: SystemErrorPayload


class WatcherNotification(EventBase):
    type: Literal["watcher.notification"]
    payload: WatcherNotificationPayload


type ProtocolEvent = (
    ClientHello
    | SystemHealth
    | AssistantStateChanged
    | AudioTranscriptFinal
    | AssistantResponseStarted
    | AssistantResponseDelta
    | AssistantResponseCompleted
    | ModelStatusChanged
    | ToolExecutionStatus
    | ToolConfirmationRequested
    | ToolConfirmationResolved
    | ToolResult
    | SystemError
    | WatcherNotification
)
_EVENT_MODELS: dict[str, type[ProtocolEvent]] = {
    "client.hello": ClientHello,
    "system.health": SystemHealth,
    "assistant.state.changed": AssistantStateChanged,
    "audio.transcript.final": AudioTranscriptFinal,
    "assistant.response.started": AssistantResponseStarted,
    "assistant.response.delta": AssistantResponseDelta,
    "assistant.response.completed": AssistantResponseCompleted,
    "model.status.changed": ModelStatusChanged,
    "tool.execution.status": ToolExecutionStatus,
    "tool.confirmation.requested": ToolConfirmationRequested,
    "tool.confirmation.resolved": ToolConfirmationResolved,
    "tool.result": ToolResult,
    "system.error": SystemError,
    "watcher.notification": WatcherNotification,
}


def parse_event(value: object) -> ProtocolEvent:
    if not isinstance(value, dict):
        raise ProtocolValidationError("Protocol event must be an object")
    version = value.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ProtocolValidationError(
            "Unsupported protocol_version; expected 1.4",
            "unsupported_protocol",
        )
    event_type = value.get("type")
    model = _EVENT_MODELS.get(event_type) if isinstance(event_type, str) else None
    if model is None:
        raise ProtocolValidationError("Unsupported event type")
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ProtocolValidationError("Invalid event fields") from error


def _validate_hyphenated_uuid(value: object) -> object:
    if not isinstance(value, UUID) and (
        not isinstance(value, str)
        or not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            value,
        )
    ):
        raise ValueError("Expected a hyphenated UUID")
    return value


def new_event(
    event_type: Literal[
        "system.health",
        "assistant.state.changed",
        "audio.transcript.final",
        "assistant.response.started",
        "assistant.response.delta",
        "assistant.response.completed",
        "model.status.changed",
        "tool.execution.status",
        "tool.confirmation.requested",
        "tool.confirmation.resolved",
        "tool.result",
        "system.error",
        "watcher.notification",
    ],
    payload: EventPayload,
    *,
    correlation_id: UUID | None = None,
) -> (
    SystemHealth
    | AssistantStateChanged
    | AudioTranscriptFinal
    | AssistantResponseStarted
    | AssistantResponseDelta
    | AssistantResponseCompleted
    | ModelStatusChanged
    | ToolExecutionStatus
    | ToolConfirmationRequested
    | ToolConfirmationResolved
    | ToolResult
    | SystemError
    | WatcherNotification
):
    values = {
        "protocol_version": PROTOCOL_VERSION,
        "event_id": uuid4(),
        "occurred_at": datetime.now(UTC),
        "correlation_id": correlation_id,
        "type": event_type,
        "payload": payload,
    }
    return cast(
        SystemHealth
        | AssistantStateChanged
        | AudioTranscriptFinal
        | AssistantResponseStarted
        | AssistantResponseDelta
        | AssistantResponseCompleted
        | ModelStatusChanged
        | ToolExecutionStatus
        | ToolConfirmationRequested
        | ToolConfirmationResolved
        | ToolResult
        | SystemError
        | WatcherNotification,
        parse_event(values),
    )
