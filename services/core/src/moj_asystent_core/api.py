"""Local HTTP and WebSocket boundary for the desktop shell."""

import ipaddress
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, WebSocketException, status

from .protocol import (
    PROTOCOL_VERSION,
    ClientHello,
    ProtocolEvent,
    ProtocolValidationError,
    SystemErrorPayload,
    SystemHealthPayload,
    new_event,
    parse_event,
)
from .state import AssistantStateMachine

logger = logging.getLogger(__name__)
MAX_WEBSOCKET_MESSAGE_BYTES = 32_768
ALLOWED_ORIGINS = frozenset(
    {"http://localhost:1420", "http://tauri.localhost", "tauri://localhost"}
)


@dataclass(frozen=True)
class CoreSettings:
    host: str = "127.0.0.1"
    port: int = 8765

    def __post_init__(self) -> None:
        try:
            is_loopback = ipaddress.ip_address(self.host).is_loopback
        except ValueError as error:
            raise ValueError("Core host must be a loopback IP address") from error
        if not is_loopback:
            raise ValueError("Core service may only bind to a loopback address")
        if not 1 <= self.port <= 65_535:
            raise ValueError("Core port must be between 1 and 65535")


class CoreRuntime:
    def __init__(self) -> None:
        self.state_machine = AssistantStateMachine()
        self.active_connections = 0

    def health_payload(self) -> SystemHealthPayload:
        return SystemHealthPayload(status="ready", assistant_state=self.state_machine.state)


@asynccontextmanager
async def lifecycle(app: FastAPI) -> AsyncIterator[None]:
    app.state.runtime = CoreRuntime()
    logger.info("core_started", extra={"protocol_version": PROTOCOL_VERSION})
    yield
    logger.info("core_stopped")


def create_app(settings: CoreSettings | None = None) -> FastAPI:
    """Create a local-only app. Settings validation prevents accidental LAN binding."""
    resolved_settings = settings or CoreSettings()
    app = FastAPI(title="Mój Asystent Core", version=PROTOCOL_VERSION, lifespan=lifecycle)
    app.state.settings = resolved_settings

    @app.get("/health")
    async def health() -> dict[str, str]:
        runtime: CoreRuntime = app.state.runtime
        return runtime.health_payload().model_dump()

    @app.websocket("/ws")
    async def event_stream(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            await websocket.accept()
            await _send_error(websocket, "invalid_origin", "Niedozwolone źródło połączenia.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        runtime: CoreRuntime = app.state.runtime
        runtime.active_connections += 1
        try:
            hello = await _receive_hello(websocket)
            await _send_event(
                websocket,
                new_event("system.health", runtime.health_payload(), correlation_id=hello.event_id),
            )
            await _send_event(websocket, runtime.state_machine.synchronize())
            while True:
                await _receive_rejected_message(websocket)
        except WebSocketDisconnect:
            logger.info("desktop_disconnected")
        except WebSocketException as error:
            await websocket.close(code=error.code)
        finally:
            runtime.active_connections -= 1

    return app


async def _receive_hello(websocket: WebSocket) -> ClientHello:
    try:
        event = await _receive_event(websocket)
    except ProtocolValidationError as error:
        await _reject_protocol_error(websocket, error)
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION) from error
    if not isinstance(event, ClientHello):
        await _send_error(websocket, "invalid_message", "Pierwsza wiadomość musi być client.hello.")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    return event


async def _receive_rejected_message(websocket: WebSocket) -> None:
    try:
        await _receive_event(websocket)
    except ProtocolValidationError as error:
        await _reject_protocol_error(websocket, error)
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION) from error
    await _send_error(websocket, "invalid_message", "Ten etap nie obsługuje dodatkowych poleceń.")
    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)


async def _receive_event(websocket: WebSocket):
    raw = await websocket.receive_text()
    if len(raw.encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
        raise ProtocolValidationError("Message exceeds the allowed size")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ProtocolValidationError("Message must be valid JSON") from error
    return parse_event(decoded)


async def _reject_protocol_error(websocket: WebSocket, error: ProtocolValidationError) -> None:
    code = "unsupported_protocol" if "protocol_version" in str(error) else "invalid_message"
    await _send_error(websocket, code, "Nieprawidłowa wiadomość protokołu.")


async def _send_error(
    websocket: WebSocket,
    code: Literal["invalid_message", "unsupported_protocol", "invalid_origin", "message_too_large"],
    message: str,
) -> None:
    await _send_event(
        websocket, new_event("system.error", SystemErrorPayload(code=code, message=message))
    )


async def _send_event(websocket: WebSocket, event: ProtocolEvent) -> None:
    await websocket.send_json(event.model_dump(mode="json"))
