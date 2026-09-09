from datetime import UTC, datetime
from uuid import uuid4

import pytest

from moj_asystent_core.protocol import (
    PROTOCOL_VERSION,
    AssistantStateChanged,
    ClientHello,
    ProtocolValidationError,
    parse_event,
)


def envelope(
    event_type: str, payload: dict[str, object], version: str = PROTOCOL_VERSION
) -> dict[str, object]:
    return {
        "protocol_version": version,
        "event_id": str(uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "correlation_id": None,
        "type": event_type,
        "payload": payload,
    }


def test_protocol_round_trips_a_typed_client_hello() -> None:
    wire_event = envelope(
        "client.hello",
        {"client_id": "desktop-development", "protocol_version": PROTOCOL_VERSION},
    )

    event = parse_event(wire_event)

    assert isinstance(event, ClientHello)
    assert event.payload.client_id == "desktop-development"
    assert event.model_dump(mode="json") == wire_event


def test_protocol_round_trips_an_assistant_state_event() -> None:
    wire_event = envelope(
        "assistant.state.changed",
        {"previous_state": "idle", "state": "listening"},
    )

    event = parse_event(wire_event)

    assert isinstance(event, AssistantStateChanged)
    assert event.payload.state == "listening"


@pytest.mark.parametrize("version", ["", "0.9", "2.0", "v1"])
def test_protocol_rejects_an_unsupported_version(version: str) -> None:
    with pytest.raises(ProtocolValidationError, match="protocol_version"):
        parse_event(
            envelope("client.hello", {"client_id": "desktop", "protocol_version": version}, version)
        )


def test_protocol_rejects_unknown_event_types_and_unexpected_fields() -> None:
    with pytest.raises(ProtocolValidationError, match="Unsupported event type"):
        parse_event(envelope("tool.execute", {}))

    with pytest.raises(ProtocolValidationError):
        parse_event(
            envelope(
                "client.hello",
                {"client_id": "desktop", "protocol_version": PROTOCOL_VERSION, "unexpected": True},
            )
        )
