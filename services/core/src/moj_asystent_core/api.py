"""Authenticated local HTTP controls and observable WebSocket event boundary."""

import asyncio
import ipaddress
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import Base64Bytes, BaseModel, ConfigDict, Field, field_validator

from .audio import AudioConfig, AudioPipeline, PcmFrame
from .audio_providers import (
    FasterWhisperPolishProvider,
    OpenWakeWordProvider,
    PiperPolishProvider,
    SileroVadProvider,
    SoundDeviceMicrophone,
    list_input_devices,
)
from .auth import SessionCredential
from .conversation import LocalConversationService, TextChatController
from .llm import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    LanguageModelProvider,
    OllamaLanguageModelProvider,
)
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
from .tools import ToolEngine, build_tool_engine
from .tools.confirmations import ConfirmationRejected
from .tools.models import ConfirmationDecision
from .tools.platform import WindowsToolPlatform
from .tools.policy import default_policy_path
from .wakeword import WakeModelMetadata, WakeModelStore, default_wake_root
from .wakeword_training import OpenWakeWordOnnxTrainer, WakeOnboardingService

logger = logging.getLogger(__name__)
MAX_WEBSOCKET_MESSAGE_BYTES = 32_768
ALLOWED_ORIGINS = (
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: Literal["1.3"]


class BeginOnboardingRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=32)
    microphone_device: str | int | None = None
    keep_training_samples: bool = False


class PcmRequest(StrictRequest):
    session_id: UUID
    pcm_s16le: Base64Bytes = Field(min_length=2, max_length=2_000_000)
    sample_rate: int = Field(ge=8_000, le=48_000)


class SampleRequest(PcmRequest):
    step_id: str = Field(pattern=r"^[a-z]+-[0-9]+$|^ordinary-speech$", max_length=32)


class TrainRequest(StrictRequest):
    session_id: UUID
    seed: int = Field(default=44, ge=0, le=2_147_483_647)


class ValidationRequest(PcmRequest):
    kind: Literal["positive", "negative"]


class ActivateRequest(StrictRequest):
    session_id: UUID
    allow_override: bool = False


class SensitivityRequest(StrictRequest):
    value: float = Field(ge=0.05, le=0.95)


class ChatRequest(StrictRequest):
    text: str = Field(min_length=1, max_length=8_192)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message cannot be blank")
        return normalized


class ResolveConfirmationRequest(StrictRequest):
    confirmation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")
    operation_id: UUID
    call_id: UUID
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["allow", "cancel", "always_allow"]


@dataclass(frozen=True)
class CoreSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    handshake_timeout: float = 5.0
    heartbeat_interval: float = 10.0
    credential: SessionCredential = field(default_factory=SessionCredential.generate, repr=False)
    action_credential: SessionCredential = field(
        default_factory=SessionCredential.generate, repr=False
    )
    audio: AudioConfig = field(default_factory=AudioConfig)
    audio_enabled: bool = False
    wake_data_root: Path = field(default_factory=default_wake_root)
    ollama_url: str = DEFAULT_OLLAMA_URL
    llm_model: str = DEFAULT_MODEL
    permission_policy_path: Path = field(default_factory=default_policy_path)

    def __post_init__(self) -> None:
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError("Core service may only bind to a loopback address")
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise ValueError("Core port must be between 1 and 65535")
        if not 0 < self.handshake_timeout <= 60 or not 0 < self.heartbeat_interval <= 10:
            raise ValueError("Core timeouts must be positive and bounded")
        if self.credential.matches(self.action_credential.reveal()):
            raise ValueError("Core and action credentials must be distinct")


@asynccontextmanager
async def lifecycle(app: FastAPI) -> AsyncIterator[None]:
    runtime = CoreRuntime()
    app.state.runtime = runtime
    store = WakeModelStore(app.state.settings.wake_data_root)
    active = store.load_active()
    config: AudioConfig = app.state.settings.audio
    if active is not None:
        config = config.model_copy(
            update={
                "microphone_device": active.microphone_device,
                "wake_model_path": active.model_path,
                "wake_sensitivity": active.sensitivity,
            }
        )
    wake_provider = OpenWakeWordProvider(config.wake_model_path, config.development_wake_model)
    provider: LanguageModelProvider = app.state.language_model_provider
    tool_engine: ToolEngine = build_tool_engine(
        runtime,
        policy_path=app.state.settings.permission_policy_path,
        platform=app.state.tool_platform,
    )
    app.state.tool_engine = tool_engine
    conversation = LocalConversationService(runtime, provider, tool_engine=tool_engine)
    chat = TextChatController(runtime, conversation)
    app.state.conversation = conversation
    app.state.chat = chat
    audio = AudioPipeline(
        runtime,
        config,
        wake_provider,
        SileroVadProvider(),
        FasterWhisperPolishProvider(config.stt_model),
        PiperPolishProvider(config.tts_voice_path),
        SoundDeviceMicrophone(config),
        conversation,
    )
    app.state.audio = audio

    async def activate_runtime(metadata: WakeModelMetadata) -> None:
        await audio.activate_wake_model(
            metadata.model_path, metadata.normalized_name, metadata.sensitivity
        )
        audio.set_wake_suspended(False)
        if app.state.settings.audio_enabled:
            await audio.resume_capture(SoundDeviceMicrophone(audio.config))

    onboarding = WakeOnboardingService(store, OpenWakeWordOnnxTrainer(), activate_runtime)
    app.state.onboarding = onboarding
    if app.state.settings.audio_enabled and active is not None:
        await audio.start()
    await conversation.refresh_status()
    logger.info("core_started", extra={"protocol_version": PROTOCOL_VERSION})
    try:
        yield
    finally:
        await onboarding.shutdown()
        await chat.shutdown()
        await audio.shutdown()
        tool_engine.shutdown()
        await conversation.close()
        await runtime.shutdown()
        logger.info("core_stopped")


def create_app(
    settings: CoreSettings | None = None,
    language_model_provider: LanguageModelProvider | None = None,
    tool_platform: WindowsToolPlatform | None = None,
) -> FastAPI:
    resolved = settings or CoreSettings()
    app = FastAPI(title="Mój Asystent Core", version=PROTOCOL_VERSION, lifespan=lifecycle)
    app.state.settings = resolved
    app.state.language_model_provider = language_model_provider or OllamaLanguageModelProvider(
        base_url=resolved.ollama_url, model=resolved.llm_model
    )
    app.state.tool_platform = tool_platform
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
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

    def authorize_action(authorization: Annotated[str | None, Header()] = None) -> None:
        prefix = "Bearer "
        candidate = (
            authorization[len(prefix) :]
            if authorization and authorization.startswith(prefix)
            else ""
        )
        if not candidate or not resolved.action_credential.matches(candidate):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def onboarding_service() -> WakeOnboardingService:
        return app.state.onboarding

    def safe_call(function, *args):
        try:
            return function(*args)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def public_metadata(metadata: WakeModelMetadata) -> dict[str, object]:
        return metadata.model_dump(mode="json", exclude={"model_path"})

    @app.get("/health", response_model=SystemHealthPayload)
    async def health(_: Annotated[None, Depends(authorize)]) -> SystemHealthPayload:
        return app.state.runtime.health_payload()

    @app.post("/audio/listen")
    async def listen(_: Annotated[None, Depends(authorize)]) -> dict[str, str]:
        await app.state.chat.cancel()
        await app.state.audio.manual_listen()
        return {"status": "listening"}

    @app.post("/audio/cancel")
    async def cancel(_: Annotated[None, Depends(authorize)]) -> dict[str, str]:
        await app.state.audio.cancel()
        return {"status": "idle"}

    @app.get("/model/status")
    async def model_status(
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        status = await app.state.conversation.refresh_status()
        return status.model_dump()

    @app.post("/chat")
    async def chat(
        request: ChatRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        await app.state.audio.cancel()
        operation_id = await app.state.chat.start(request.text)
        return {"status": "accepted", "operation_id": operation_id}

    @app.post("/chat/cancel")
    async def cancel_chat(_: Annotated[None, Depends(authorize)]) -> dict[str, str]:
        await app.state.chat.cancel()
        return {"status": "idle"}

    @app.post("/tool-confirmations/resolve")
    async def resolve_tool_confirmation(
        request: ResolveConfirmationRequest,
        _: Annotated[None, Depends(authorize_action)],
    ) -> dict[str, str]:
        try:
            app.state.tool_engine.resolve_confirmation(
                ConfirmationDecision(
                    confirmation_id=request.confirmation_id,
                    operation_id=str(request.operation_id),
                    call_id=str(request.call_id),
                    tool_name=request.tool_name,
                    arguments_digest=request.arguments_digest,
                    decision=request.decision,
                )
            )
        except ConfirmationRejected as error:
            raise HTTPException(
                status_code=409, detail="Confirmation is no longer valid"
            ) from error
        return {"status": "resolved"}

    @app.get("/audio/devices")
    async def audio_devices(_: Annotated[None, Depends(authorize)]) -> list[dict[str, object]]:
        return await asyncio.to_thread(list_input_devices)

    @app.get("/onboarding/status")
    async def onboarding_status(
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        active = onboarding_service().active()
        return {
            "completed": active is not None,
            "active": public_metadata(active) if active is not None else None,
        }

    @app.post("/onboarding/sessions")
    async def begin_onboarding(
        request: BeginOnboardingRequest,
        _: Annotated[None, Depends(authorize)],
    ):
        await app.state.audio.pause_capture()
        try:
            result = safe_call(
                onboarding_service().begin,
                request.name,
                request.microphone_device,
                request.keep_training_samples,
            )
        except Exception:
            if onboarding_service().active() is not None and resolved.audio_enabled:
                await app.state.audio.resume_capture(SoundDeviceMicrophone(app.state.audio.config))
            raise
        app.state.audio.set_wake_suspended(True)
        return result

    @app.get("/onboarding/sessions/{session_id}")
    async def onboarding_session(
        session_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ):
        return safe_call(onboarding_service().get, session_id)

    @app.post("/onboarding/calibration")
    async def onboarding_calibration(
        request: PcmRequest,
        _: Annotated[None, Depends(authorize)],
    ):
        return safe_call(
            onboarding_service().calibrate,
            request.session_id,
            PcmFrame(bytes(request.pcm_s16le), request.sample_rate),
        )

    @app.post("/onboarding/samples")
    async def onboarding_sample(
        request: SampleRequest,
        _: Annotated[None, Depends(authorize)],
    ):
        return safe_call(
            onboarding_service().add_sample,
            request.session_id,
            request.step_id,
            PcmFrame(bytes(request.pcm_s16le), request.sample_rate),
        )

    @app.post("/onboarding/training")
    async def onboarding_training(
        request: TrainRequest,
        _: Annotated[None, Depends(authorize)],
    ):
        try:
            return onboarding_service().start_training(request.session_id, seed=request.seed)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/onboarding/training/{job_id}")
    async def onboarding_training_status(
        job_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ):
        return safe_call(onboarding_service().job, job_id)

    @app.post("/onboarding/training/{job_id}/cancel")
    async def onboarding_training_cancel(
        job_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ):
        try:
            return await onboarding_service().cancel_training(job_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.delete("/onboarding/sessions/{session_id}")
    async def onboarding_cancel(
        session_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, str]:
        try:
            await onboarding_service().cancel_session(session_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        app.state.audio.set_wake_suspended(False)
        if onboarding_service().active() is not None and resolved.audio_enabled:
            await app.state.audio.resume_capture(SoundDeviceMicrophone(app.state.audio.config))
        return {"status": "cancelled"}

    @app.post("/onboarding/validation")
    async def onboarding_validation(
        request: ValidationRequest,
        _: Annotated[None, Depends(authorize)],
    ):
        try:
            return await onboarding_service().validate_sample(
                request.session_id,
                PcmFrame(bytes(request.pcm_s16le), request.sample_rate),
                positive=request.kind == "positive",
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/onboarding/activate")
    async def onboarding_activate(
        request: ActivateRequest,
        _: Annotated[None, Depends(authorize)],
    ):
        try:
            activated = await onboarding_service().activate(
                request.session_id, allow_override=request.allow_override
            )
            return public_metadata(activated)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.patch("/onboarding/sensitivity")
    async def onboarding_sensitivity(
        request: SensitivityRequest,
        _: Annotated[None, Depends(authorize)],
    ):
        try:
            updated = await onboarding_service().set_sensitivity(request.value)
            return public_metadata(updated)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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
            if not runtime.sessions:
                # A confirmation must never remain actionable when no trusted UI
                # session can still display its exact target and consequences.
                app.state.tool_engine.cancel_all_confirmations()
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
