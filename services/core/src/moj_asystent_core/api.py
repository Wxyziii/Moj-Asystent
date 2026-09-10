"""Authenticated local HTTP controls and observable WebSocket event boundary."""

import asyncio
import ipaddress
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .audio import AudioConfig, AudioPipeline
from .audio_providers import (
    FasterWhisperPolishProvider,
    OpenWakeWordProvider,
    PiperPolishProvider,
    SileroVadProvider,
    SoundDeviceMicrophone,
)
from .auth import SessionCredential
from .local_boundary import LocalHostMiddleware
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
from .runtime import CoreRuntime

logger = logging.getLogger(__name__)
MAX_WEBSOCKET_MESSAGE_BYTES = 32_768
ALLOWED_ORIGINS = (
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
)


@dataclass(frozen=True)
class CoreSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    handshake_timeout: float = 5.0
    heartbeat_interval: float = 10.0
    credential: SessionCredential = field(default_factory=SessionCredential.generate, repr=False)
    audio: AudioConfig = field(default_factory=AudioConfig)
    audio_enabled: bool = False

    def __post_init__(self) -> None:
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError("Core service may only bind to a loopback address")
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise ValueError("Core port must be between 1 and 65535")
        if not 0 < self.handshake_timeout <= 60 or not 0 < self.heartbeat_interval <= 10:
            raise ValueError("Core timeouts must be positive and bounded")


@asynccontextmanager
async def lifecycle(app: FastAPI) -> AsyncIterator[None]:
    runtime = CoreRuntime()
    app.state.runtime = runtime
    config: AudioConfig = app.state.settings.audio
    audio = AudioPipeline(
        runtime,
        config,
        OpenWakeWordProvider(config.wake_model_path, config.development_wake_model),
        SileroVadProvider(),
        FasterWhisperPolishProvider(config.stt_model),
        PiperPolishProvider(config.tts_voice_path),
        SoundDeviceMicrophone(config),
    )
    app.state.audio = audio
    if app.state.settings.audio_enabled:
        await audio.start()
    logger.info("core_started", extra={"protocol_version": PROTOCOL_VERSION})
    try:
        yield
    finally:
        await audio.shutdown()
        await runtime.shutdown()
        logger.info("core_stopped")


def create_app(settings: CoreSettings | None = None) -> FastAPI:
    resolved = settings or CoreSettings()
    app = FastAPI(title="Mój Asystent Core", version=PROTOCOL_VERSION, lifespan=lifecycle)
    app.state.settings = resolved
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(LocalHostMiddleware)

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        prefix = "Bearer "
        candidate = (
            authorization[len(prefix) :]
            if authorization and authorization.startswith(prefix)
            else ""
        )
        if not candidate or not resolved.credential.matches(candidate):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/health", response_model=SystemHealthPayload)
    async def health(_: Annotated[None, Depends(authorize)]) -> SystemHealthPayload:
        return app.state.runtime.health_payload()

    @app.post("/audio/listen")
    async def listen(_: Annotated[None, Depends(authorize)]) -> dict[str, str]:
        await app.state.audio.manual_listen()
        return {"status": "listening"}

    @app.post("/audio/cancel")
    async def cancel(_: Annotated[None, Depends(authorize)]) -> dict[str, str]:
        await app.state.audio.cancel()
        return {"status": "idle"}

    @app.post("/shutdown")
    async def shutdown(_: Annotated[None, Depends(authorize)]) -> dict[str, str]:
        request_shutdown = getattr(app.state, "request_shutdown", None)
        if request_shutdown is None:
            raise HTTPException(status_code=503, detail="Shutdown controller unavailable")
        asyncio.get_running_loop().call_soon(request_shutdown)
        return {"status": "stopping"}

    @app.websocket("/ws")
    async def event_stream(websocket: WebSocket) -> None:
        if websocket.headers.get("origin") not in (*ALLOWED_ORIGINS, None):
            await websocket.close(code=1008)
            return
        if not _authorized_websocket(websocket, resolved.credential):
            await websocket.close(code=1008)
            return
        runtime: CoreRuntime = app.state.runtime
        if runtime.stopping or len(runtime.sessions) >= 16:
            await websocket.close(code=1013)
            return
        task = asyncio.current_task()
        assert task is not None
        runtime.sessions.add(task)
        children: list[asyncio.Task[None]] = []
        queue: asyncio.Queue[ProtocolEvent | None] | None = None
        close_code = 1000
        try:
            await websocket.accept(subprotocol="moj-asystent.v1")
            hello = await asyncio.wait_for(_receive_event(websocket), resolved.handshake_timeout)
            if not isinstance(hello, ClientHello) or hello.correlation_id is not None:
                raise ProtocolValidationError("First message must be client.hello")
            queue = runtime.subscribe(hello.event_id)

            async def send_events() -> None:
                nonlocal close_code
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), resolved.heartbeat_interval)
                    except TimeoutError:
                        event = new_event(
                            "system.health", runtime.health_payload(), correlation_id=hello.event_id
                        )
                    if event is None:
                        close_code = 1013
                        return
                    await _send_event(websocket, event)

            async def reject_commands() -> None:
                await _receive_event(websocket)
                raise ProtocolValidationError("No client commands are supported after hello")

            children = [asyncio.create_task(send_events()), asyncio.create_task(reject_commands())]
            done, _ = await asyncio.wait(children, return_when=asyncio.FIRST_COMPLETED)
            for child in done:
                child.result()
        except ProtocolValidationError as error:
            close_code = 1008
            for child in children:
                child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            with suppress(WebSocketDisconnect, OSError, TimeoutError):
                await _send_event(
                    websocket,
                    new_event(
                        "system.error",
                        SystemErrorPayload(
                            code=error.code, message="Nieprawidłowa wiadomość protokołu."
                        ),
                    ),
                )
        except TimeoutError:
            close_code = 1008
        except (WebSocketDisconnect, OSError):
            pass
        except asyncio.CancelledError:
            close_code = 1001
            raise
        finally:
            for child in children:
                child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            if queue is not None:
                runtime.unsubscribe(queue)
            runtime.sessions.discard(task)
            with suppress(WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
                await asyncio.wait_for(websocket.close(code=close_code), 2.0)

    return app


async def _receive_event(websocket: WebSocket) -> ProtocolEvent:
    frame = await websocket.receive()
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    raw = frame.get("text")
    if not isinstance(raw, str):
        raise ProtocolValidationError("Only text JSON frames are supported")
    if len(raw.encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
        raise ProtocolValidationError("Message too large", "message_too_large")
    try:
        decoded = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ProtocolValidationError("Invalid JSON") from error
    return parse_event(decoded)


async def _send_event(websocket: WebSocket, event: ProtocolEvent) -> None:
    await asyncio.wait_for(websocket.send_json(event.model_dump(mode="json")), 2.0)


def _authorized_websocket(websocket: WebSocket, credential: SessionCredential) -> bool:
    protocols = {
        value.strip()
        for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    }
    prefix = "credential."
    candidates = [value[len(prefix) :] for value in protocols if value.startswith(prefix)]
    return (
        "moj-asystent.v1" in protocols
        and len(candidates) == 1
        and credential.matches(candidates[0])
    )
