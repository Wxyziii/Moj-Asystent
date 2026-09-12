"""Authenticated local HTTP controls and observable WebSocket event boundary."""

import asyncio
import ipaddress
import json
import logging
from base64 import b64encode
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import (
    Base64Bytes,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

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
from .context import Bounds, ContextSettings
from .conversation import LocalConversationService, TextChatController
from .llm import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    LanguageModelProvider,
    OllamaLanguageModelProvider,
)
from .local_boundary import LocalHostMiddleware
from .memory import (
    AliasRecord,
    MemoryRecord,
    MemoryStoreBusyError,
    MemoryStoreCorruptError,
    MemoryStoreError,
    MemoryValidationError,
    RoutineRecord,
    RoutineStep,
    RoutineSummary,
    SQLiteMemoryStore,
    WatcherEventRecord,
    WatcherRecord,
    default_memory_database_path,
)
from .model_providers import (
    DEFAULT_LLAMA_CPP_URL,
    LlamaCppLanguageModelProvider,
    OpenRouterLanguageModelProvider,
)
from .model_routing import (
    DataPolicy,
    ModelCandidate,
    ModelMode,
    ModelRouter,
    ProviderCapabilities,
    TelemetryRoutingHardwareProvider,
)
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
from .routines import RoutineCreateRequest, RoutineService, RoutineValidationError
from .runtime import CoreRuntime
from .stt import (
    DEFAULT_POLISH_TECHNICAL_VOCABULARY,
    SttRuntimeProfile,
    SttRuntimeSelector,
    SttVocabulary,
)
from .tools import ToolEngine, build_tool_engine
from .tools.confirmations import ConfirmationRejected
from .tools.models import ConfirmationDecision, ContextProviderArguments
from .tools.platform import WindowsToolPlatform
from .tools.policy import default_policy_path
from .vision import PendingVisionStore, VisionInspectionResult, VisionSettings
from .wakeword import WakeModelMetadata, WakeModelStore, default_wake_root
from .wakeword_training import OpenWakeWordOnnxTrainer, WakeOnboardingService
from .watchers import (
    WatcherCreateRequest,
    WatcherObservationProvider,
    WatcherPlatform,
    WatcherService,
    WatcherToolBridge,
    WatcherValidationError,
)

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
    protocol_version: Literal["1.4"]


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
    visual_context_id: UUID | None = None

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


class RegionBoundsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    left: StrictInt
    top: StrictInt
    right: StrictInt
    bottom: StrictInt


class CaptureRegionRequest(StrictRequest):
    reason: str = Field(min_length=1, max_length=256)
    region: RegionBoundsRequest
    monitor_bounds: RegionBoundsRequest
    dpi_scale: float = Field(ge=0.5, le=4.0)


class RegionCaptureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    result: VisionInspectionResult
    preview_data_url: str | None = Field(default=None, max_length=140_000)


class MemorySettingRequest(StrictRequest):
    enabled: StrictBool


class ModelSettingsRequest(StrictRequest):
    mode: ModelMode | None = None
    data_policy: DataPolicy | None = None

    @model_validator(mode="after")
    def at_least_one_value(self) -> Self:
        if self.mode is None and self.data_policy is None:
            raise ValueError("At least one model setting is required")
        return self


class VoiceSettingsRequest(StrictRequest):
    vocabulary: tuple[str, ...] = Field(max_length=32)

    @field_validator("vocabulary")
    @classmethod
    def validate_vocabulary(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return SttVocabulary(entries=value).entries


class MemoryWriteRequest(StrictRequest):
    key: str = Field(min_length=1, max_length=96)
    value: str = Field(min_length=1, max_length=2_048)


class AliasWriteRequest(StrictRequest):
    kind: Literal["app", "project"]
    alias: str = Field(min_length=1, max_length=96)
    target: str = Field(min_length=1, max_length=1_024)
    overwrite: StrictBool = False


class MemoryClearRequest(StrictRequest):
    confirm: Literal[True]


class RoutineWriteRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=96)
    description: str | None = Field(default=None, max_length=512)
    steps: tuple[RoutineStep, ...] = Field(min_length=1, max_length=16)


class RoutineRenameRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=96)


class WatcherWriteRequest(StrictRequest):
    explicit_intent: Literal[True]
    type: Literal["window", "process", "file", "resource", "build", "download"]
    name: str = Field(min_length=1, max_length=96)
    target: dict[str, object]
    condition: dict[str, object]
    interval_seconds: StrictInt = Field(default=2, ge=1, le=3_600)
    expires_at: datetime | None = None
    one_shot: StrictBool = True
    notification_level: Literal["normal", "quiet"] = "normal"


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
    quality_model: str = "qwen3.5:9b"
    llama_cpp_url: str = DEFAULT_LLAMA_CPP_URL
    llama_cpp_model: str = "Qwen3.5 27B GGUF"
    llama_cpp_model_path: Path | None = None
    llama_cpp_executable_path: Path | None = None
    llama_cpp_gpu_layers: int = 20
    llama_cpp_context_size: int = 16_384
    openrouter_model: str | None = None
    openrouter_api_key: str | None = field(default=None, repr=False)
    high_load_processes: tuple[str, ...] = ()
    permission_policy_path: Path = field(default_factory=default_policy_path)
    memory_database_path: Path = field(default_factory=default_memory_database_path)
    context: ContextSettings = field(default_factory=ContextSettings)
    vision: VisionSettings = field(default_factory=VisionSettings)

    def __post_init__(self) -> None:
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError("Core service may only bind to a loopback address")
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise ValueError("Core port must be between 1 and 65535")
        if not 0 < self.handshake_timeout <= 60 or not 0 < self.heartbeat_interval <= 10:
            raise ValueError("Core timeouts must be positive and bounded")
        if self.credential.matches(self.action_credential.reveal()):
            raise ValueError("Core and action credentials must be distinct")
        if not 0 <= self.llama_cpp_gpu_layers <= 256:
            raise ValueError("llama.cpp GPU layer count is outside safe bounds")
        if not 2_048 <= self.llama_cpp_context_size <= 65_536:
            raise ValueError("llama.cpp context size is outside safe bounds")


def _stored_model_mode(store: SQLiteMemoryStore) -> ModelMode:
    record = store.preference("model_mode")
    if record is not None and record.value in {"private", "fast", "quality", "deep", "auto"}:
        return record.value
    return "auto"


def _stored_data_policy(store: SQLiteMemoryStore) -> DataPolicy:
    record = store.preference("model_data_policy")
    if record is not None and record.value in {"local_only", "cloud_allowed"}:
        return record.value
    return "local_only"


def _stored_stt_vocabulary(store: SQLiteMemoryStore) -> SttVocabulary:
    record = store.preference("stt_vocabulary")
    if record is not None:
        try:
            return SttVocabulary.model_validate_json(record.value)
        except ValueError:
            pass
    return SttVocabulary(entries=DEFAULT_POLISH_TECHNICAL_VOCABULARY)


def _stt_profile(
    config: AudioConfig,
    *,
    profile_id: str,
    model: str,
    device: Literal["cpu", "cuda"],
    compute_type: Literal["int8", "int8_float16", "float16", "float32"],
) -> SttRuntimeProfile:
    return SttRuntimeProfile(
        profile_id=profile_id,
        model=model,
        device=device,
        compute_type=compute_type,
        beam_size=config.stt_beam_size,
        best_of=config.stt_best_of,
        patience=config.stt_patience,
        temperature=config.stt_temperature,
        condition_on_previous_text=False,
        no_speech_threshold=config.stt_no_speech_threshold,
        log_probability_threshold=config.stt_log_probability_threshold,
        compression_ratio_threshold=config.stt_compression_ratio_threshold,
    )


def _build_stt_selector(config: AudioConfig, vocabulary: SttVocabulary) -> SttRuntimeSelector:
    profiles: list[SttRuntimeProfile] = []
    if config.stt_preferred_enabled:
        profiles.append(
            _stt_profile(
                config,
                profile_id="preferred",
                model=config.stt_preferred_model,
                device=config.stt_preferred_device,
                compute_type=config.stt_preferred_compute_type,
            )
        )
    profiles.append(
        _stt_profile(
            config,
            profile_id="configured_fallback",
            model=config.stt_model,
            device=config.stt_fallback_device,
            compute_type=config.stt_fallback_compute_type,
        )
    )
    compatibility = _stt_profile(
        config,
        profile_id="cpu_compatibility",
        model="medium",
        device="cpu",
        compute_type="int8",
    )
    if not any(
        (item.model, item.device, item.compute_type)
        == (compatibility.model, compatibility.device, compatibility.compute_type)
        for item in profiles
    ):
        profiles.append(compatibility)
    return SttRuntimeSelector(
        tuple(FasterWhisperPolishProvider(profile) for profile in profiles),
        vocabulary=vocabulary,
    )


def _public_stt_model(model: str) -> str:
    return model.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "model lokalny"


def _public_stt_profile(profile: SttRuntimeProfile) -> dict[str, object]:
    return {
        **profile.model_dump(mode="json", exclude={"model"}),
        "model": _public_stt_model(profile.model),
    }


async def _voice_settings_payload(
    selector: SttRuntimeSelector, config: AudioConfig
) -> dict[str, object]:
    status = await selector.status()
    return {
        "active": {
            **_public_stt_profile(status.profile),
            "status": status.state,
            "detail": status.detail,
            "fallback_active": status.fallback_active,
            "fallback_reason": status.fallback_reason,
        },
        "profiles": [_public_stt_profile(profile) for profile in selector.profiles],
        "vocabulary": list(selector.vocabulary.entries),
        "vad": {
            "pre_roll_ms": config.vad_pre_roll_ms,
            "post_roll_ms": config.vad_post_roll_ms,
            "trailing_silence_ms": config.trailing_silence_ms,
            "maximum_utterance_seconds": config.maximum_utterance_seconds,
        },
    }


def _build_model_router(
    settings: CoreSettings,
    platform: WindowsToolPlatform,
    *,
    mode: ModelMode,
    data_policy: DataPolicy,
) -> ModelRouter:
    local_capabilities = ProviderCapabilities(
        text=True,
        image=True,
        structured_output=True,
        tools=True,
        context_size=32_768,
    )
    deep_provider = LlamaCppLanguageModelProvider(
        model=settings.llama_cpp_model,
        model_path=settings.llama_cpp_model_path,
        executable_path=settings.llama_cpp_executable_path,
        base_url=settings.llama_cpp_url,
        gpu_layers=settings.llama_cpp_gpu_layers,
        context_size=settings.llama_cpp_context_size,
    )
    deep_size = None
    if settings.llama_cpp_model_path is not None:
        try:
            deep_size = settings.llama_cpp_model_path.stat().st_size
        except OSError:
            deep_size = None
    candidates = [
        ModelCandidate(
            tier="fast",
            provider_name="ollama",
            provider=OllamaLanguageModelProvider(
                base_url=settings.ollama_url, model=settings.llm_model, keep_alive="5m"
            ),
            local=True,
            capabilities=local_capabilities,
            approximate_size_bytes=int(3.4 * 1024**3),
        ),
        ModelCandidate(
            tier="quality",
            provider_name="ollama",
            provider=OllamaLanguageModelProvider(
                base_url=settings.ollama_url,
                model=settings.quality_model,
                keep_alive="10m",
            ),
            local=True,
            capabilities=local_capabilities,
            approximate_size_bytes=int(6.6 * 1024**3),
        ),
        ModelCandidate(
            tier="deep",
            provider_name="llama_cpp",
            provider=deep_provider,
            local=True,
            capabilities=ProviderCapabilities(
                text=True,
                image=False,
                structured_output=True,
                tools=False,
                context_size=settings.llama_cpp_context_size,
            ),
            approximate_size_bytes=deep_size or 17 * 1024**3,
        ),
    ]
    if settings.openrouter_model:
        candidates.append(
            ModelCandidate(
                tier="deep",
                provider_name="openrouter",
                provider=OpenRouterLanguageModelProvider(
                    api_key=settings.openrouter_api_key,
                    model=settings.openrouter_model,
                ),
                local=False,
                capabilities=ProviderCapabilities(
                    text=True,
                    image=False,
                    structured_output=True,
                    tools=False,
                    context_size=32_768,
                ),
            )
        )
    return ModelRouter(
        tuple(candidates),
        TelemetryRoutingHardwareProvider(
            platform.telemetry, high_load_processes=settings.high_load_processes
        ),
        mode=mode,
        data_policy=data_policy,
    )


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
    tool_platform = app.state.tool_platform or WindowsToolPlatform(
        context_settings=app.state.settings.context,
        vision_settings=app.state.settings.vision,
    )
    app.state.tool_platform = tool_platform
    owns_memory_store = app.state.memory_store is None
    memory_store = app.state.memory_store or SQLiteMemoryStore(
        app.state.settings.memory_database_path
    )
    app.state.memory_store = memory_store
    configured_mode = _stored_model_mode(memory_store)
    configured_policy = _stored_data_policy(memory_store)
    vocabulary = _stored_stt_vocabulary(memory_store)
    stt_selector = app.state.stt_selector or _build_stt_selector(config, vocabulary)
    stt_selector.set_vocabulary(vocabulary)
    app.state.stt_selector = stt_selector
    router = app.state.model_router
    if router is None:
        injected_provider: LanguageModelProvider | None = app.state.language_model_provider
        if injected_provider is not None:
            router = ModelRouter.from_provider(injected_provider)
        else:
            router = _build_model_router(
                app.state.settings,
                tool_platform,
                mode=configured_mode,
                data_policy=configured_policy,
            )
    router.set_mode(configured_mode)
    router.set_data_policy(configured_policy)
    app.state.model_router = router
    await router.start()

    def publish_watcher_notification(payload) -> None:
        runtime.publish(new_event("watcher.notification", payload))

    watcher_service = WatcherService(
        memory_store,
        cast(WatcherPlatform, tool_platform),
        publish_watcher_notification,
        provider=app.state.watcher_provider,
    )
    app.state.watcher_service = watcher_service
    await watcher_service.start()
    tool_engine: ToolEngine = build_tool_engine(
        runtime,
        policy_path=app.state.settings.permission_policy_path,
        platform=tool_platform,
        memory_store=memory_store,
        watcher_bridge=WatcherToolBridge(watcher_service),
    )
    app.state.tool_engine = tool_engine
    conversation = LocalConversationService(
        runtime, router, tool_engine=tool_engine, memory_store=memory_store
    )
    routine_service = RoutineService(memory_store, tool_engine)
    app.state.routine_service = routine_service
    vision_store = PendingVisionStore()
    chat = TextChatController(runtime, conversation, vision_store)
    app.state.vision_store = vision_store
    app.state.conversation = conversation
    app.state.chat = chat
    audio = AudioPipeline(
        runtime,
        config,
        wake_provider,
        SileroVadProvider(),
        stt_selector,
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
        await watcher_service.shutdown()
        await onboarding.shutdown()
        await routine_service.shutdown()
        await chat.shutdown()
        await audio.shutdown()
        vision_store.close()
        tool_engine.shutdown()
        await conversation.close()
        await runtime.shutdown()
        if owns_memory_store:
            memory_store.close()
        logger.info("core_stopped")


def create_app(
    settings: CoreSettings | None = None,
    language_model_provider: LanguageModelProvider | None = None,
    tool_platform: WindowsToolPlatform | None = None,
    memory_store: SQLiteMemoryStore | None = None,
    watcher_provider: WatcherObservationProvider | None = None,
    model_router: ModelRouter | None = None,
    stt_selector: SttRuntimeSelector | None = None,
) -> FastAPI:
    resolved = settings or CoreSettings()
    app = FastAPI(title="Mój Asystent Core", version=PROTOCOL_VERSION, lifespan=lifecycle)
    app.state.settings = resolved
    app.state.language_model_provider = language_model_provider
    app.state.model_router = model_router
    app.state.tool_platform = tool_platform
    app.state.memory_store = memory_store
    app.state.watcher_provider = watcher_provider
    app.state.stt_selector = stt_selector
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

    async def memory_call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except MemoryValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except MemoryStoreBusyError as error:
            raise HTTPException(
                status_code=503, detail="Pamięć lokalna jest chwilowo zajęta."
            ) from error
        except (MemoryStoreCorruptError, MemoryStoreError) as error:
            raise HTTPException(
                status_code=503, detail="Pamięć lokalna jest niedostępna."
            ) from error

    def routine_error(error: Exception) -> HTTPException:
        if isinstance(error, RoutineValidationError | MemoryValidationError):
            return HTTPException(status_code=422, detail=str(error))
        if isinstance(error, MemoryStoreBusyError):
            return HTTPException(status_code=503, detail="Pamięć lokalna jest chwilowo zajęta.")
        return HTTPException(status_code=503, detail="Pamięć lokalna jest niedostępna.")

    def watcher_error(error: Exception) -> HTTPException:
        if isinstance(error, WatcherValidationError | MemoryValidationError):
            return HTTPException(status_code=422, detail=str(error))
        if isinstance(error, MemoryStoreBusyError):
            return HTTPException(status_code=503, detail="Pamięć lokalna jest chwilowo zajęta.")
        return HTTPException(status_code=503, detail="Obserwacje są chwilowo niedostępne.")

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

    @app.get("/voice/settings")
    async def voice_settings(
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        return await _voice_settings_payload(app.state.stt_selector, app.state.audio.config)

    @app.patch("/voice/settings")
    async def update_voice_settings(
        request: VoiceSettingsRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        vocabulary = SttVocabulary(entries=request.vocabulary)
        await memory_call(
            app.state.memory_store.remember_preference,
            "stt_vocabulary",
            vocabulary.model_dump_json(),
            source="settings",
        )
        app.state.stt_selector.set_vocabulary(vocabulary)
        return await _voice_settings_payload(app.state.stt_selector, app.state.audio.config)

    @app.get("/voice/diagnostics")
    async def voice_diagnostics(
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        diagnostics = []
        for item in app.state.stt_selector.diagnostics():
            payload = item.model_dump(mode="json", exclude={"model"})
            payload["model"] = _public_stt_model(item.model)
            diagnostics.append(payload)
        return {"diagnostics": diagnostics}

    @app.get("/model/status")
    async def model_status(
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        status = await app.state.conversation.refresh_status()
        return status.model_dump()

    @app.get("/model/settings")
    async def model_settings(
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, str]:
        router: ModelRouter = app.state.model_router
        effective = "local_only" if router.mode == "private" else router.data_policy
        return {
            "mode": router.mode,
            "data_policy": router.data_policy,
            "effective_data_policy": effective,
        }

    @app.patch("/model/settings")
    async def update_model_settings(
        request: ModelSettingsRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, str]:
        router: ModelRouter = app.state.model_router
        await app.state.chat.cancel()
        if request.mode is not None:
            await memory_call(
                app.state.memory_store.remember_preference,
                "model_mode",
                request.mode,
                source="settings",
            )
            router.set_mode(request.mode)
        if request.data_policy is not None:
            await memory_call(
                app.state.memory_store.remember_preference,
                "model_data_policy",
                request.data_policy,
                source="settings",
            )
            router.set_data_policy(request.data_policy)
        effective = "local_only" if router.mode == "private" else router.data_policy
        return {
            "mode": router.mode,
            "data_policy": router.data_policy,
            "effective_data_policy": effective,
        }

    @app.get("/models/catalog")
    async def model_catalog(
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        router: ModelRouter = app.state.model_router
        entries = []
        for candidate, status in await router.catalog():
            entries.append(
                {
                    "tier": candidate.tier,
                    "provider": candidate.provider_name,
                    "model": candidate.provider.model,
                    "location": "local" if candidate.local else "cloud",
                    "capabilities": candidate.capabilities.model_dump(mode="json"),
                    "approximate_size_bytes": candidate.approximate_size_bytes,
                    "status": status.state,
                    "detail": status.detail,
                    "resident": router.is_resident(candidate),
                }
            )
        return {"models": entries}

    @app.get("/model/metrics")
    async def model_metrics(
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        router: ModelRouter = app.state.model_router
        return {"metrics": [item.model_dump(mode="json") for item in router.metrics()[-20:]]}

    @app.get("/memory/settings")
    async def memory_settings(_: Annotated[None, Depends(authorize)]) -> dict[str, bool]:
        enabled = await memory_call(app.state.memory_store.history_retention_enabled)
        return {"history_retention": enabled}

    @app.patch("/memory/settings")
    async def update_memory_settings(
        request: MemorySettingRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, bool]:
        await memory_call(app.state.memory_store.set_history_retention, request.enabled)
        return {"history_retention": request.enabled}

    @app.get("/memories")
    async def list_memories(
        limit: int = 12,
        _: Annotated[None, Depends(authorize)] = None,
    ) -> dict[str, object]:
        records: tuple[MemoryRecord, ...] = await memory_call(
            app.state.memory_store.list_memories, limit=limit
        )
        return {"memories": [record.model_dump(mode="json") for record in records]}

    @app.post("/memories")
    async def create_memory(
        request: MemoryWriteRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        record: MemoryRecord = await memory_call(
            app.state.memory_store.create_memory, request.key, request.value
        )
        return record.model_dump(mode="json")

    @app.delete("/memories/{memory_id}")
    async def delete_memory(
        memory_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, bool]:
        removed = await memory_call(app.state.memory_store.delete_memory, memory_id)
        return {"removed": removed}

    @app.post("/memory/clear")
    async def clear_memories(
        request: MemoryClearRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, int]:
        removed = await memory_call(app.state.memory_store.clear_memories)
        return {"removed": removed}

    @app.delete("/history")
    async def clear_history(_: Annotated[None, Depends(authorize)]) -> dict[str, int]:
        removed = await memory_call(app.state.memory_store.clear_history)
        return {"removed": removed}

    @app.get("/aliases")
    async def list_aliases(
        kind: Literal["app", "project"] | None = None,
        _: Annotated[None, Depends(authorize)] = None,
    ) -> dict[str, object]:
        aliases: tuple[AliasRecord, ...] = await memory_call(
            app.state.memory_store.list_aliases, kind=kind
        )
        return {"aliases": [alias.model_dump(mode="json") for alias in aliases]}

    @app.post("/aliases")
    async def save_alias(
        request: AliasWriteRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        alias: AliasRecord = await memory_call(
            app.state.memory_store.save_alias,
            request.kind,
            request.alias,
            request.target,
            overwrite=request.overwrite,
        )
        return alias.model_dump(mode="json")

    @app.delete("/aliases/{alias_id}")
    async def delete_alias(
        alias_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, bool]:
        removed = await memory_call(app.state.memory_store.delete_alias, alias_id)
        return {"removed": removed}

    @app.get("/routines")
    async def list_routines(_: Annotated[None, Depends(authorize)]) -> dict[str, object]:
        summaries: tuple[RoutineSummary, ...] = await memory_call(
            app.state.memory_store.list_routines
        )
        return {"routines": [item.model_dump(mode="json") for item in summaries]}

    @app.post("/routines/runs/{operation_id}/cancel")
    async def cancel_routine(
        operation_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, bool]:
        return {"cancelled": await app.state.routine_service.cancel(operation_id)}

    @app.post("/routines")
    async def create_routine(
        request: RoutineWriteRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        try:
            routine: RoutineRecord = await app.state.routine_service.create(
                RoutineCreateRequest(
                    name=request.name, description=request.description, steps=request.steps
                )
            )
        except Exception as error:
            raise routine_error(error) from error
        return routine.model_dump(mode="json")

    @app.get("/routines/{routine_id}")
    async def inspect_routine(
        routine_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        routine = await memory_call(app.state.memory_store.get_routine, routine_id)
        if routine is None:
            raise HTTPException(status_code=404, detail="Rutyna nie istnieje.")
        return routine.model_dump(mode="json")

    @app.patch("/routines/{routine_id}")
    async def rename_routine(
        routine_id: UUID,
        request: RoutineRenameRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        try:
            routine: RoutineRecord = await app.state.routine_service.rename(
                routine_id, request.name
            )
        except Exception as error:
            raise routine_error(error) from error
        return routine.model_dump(mode="json")

    @app.delete("/routines/{routine_id}")
    async def delete_routine(
        routine_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, bool]:
        try:
            removed = await app.state.routine_service.delete(routine_id)
        except Exception as error:
            raise routine_error(error) from error
        return {"removed": removed}

    @app.post("/routines/{routine_id}/run")
    async def run_routine(
        routine_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ):
        try:
            result = await app.state.routine_service.run(routine_id)
        except Exception as error:
            raise routine_error(error) from error
        return result.model_dump(mode="json")

    @app.get("/watchers")
    async def list_watchers(_: Annotated[None, Depends(authorize)]) -> dict[str, object]:
        records: tuple[WatcherRecord, ...] = await app.state.watcher_service.list()
        return {"watchers": [record.model_dump(mode="json") for record in records]}

    @app.get("/watchers/events")
    async def list_watcher_events(
        watcher_id: UUID | None = None,
        limit: int = 128,
        _: Annotated[None, Depends(authorize)] = None,
    ) -> dict[str, object]:
        try:
            records: tuple[WatcherEventRecord, ...] = await app.state.watcher_service.events(
                watcher_id, limit=limit
            )
        except Exception as error:
            raise watcher_error(error) from error
        return {"events": [record.model_dump(mode="json") for record in records]}

    @app.delete("/watchers/events")
    async def clear_watcher_events(
        watcher_id: UUID | None = None,
        _: Annotated[None, Depends(authorize)] = None,
    ) -> dict[str, int]:
        try:
            removed = await app.state.watcher_service.clear_events(watcher_id)
        except Exception as error:
            raise watcher_error(error) from error
        return {"removed": removed}

    @app.post("/watchers")
    async def create_watcher(
        request: WatcherWriteRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        try:
            watcher = await app.state.watcher_service.create(
                WatcherCreateRequest.model_validate(
                    {
                        "explicit_intent": request.explicit_intent,
                        "type": request.type,
                        "name": request.name,
                        "target": request.target,
                        "condition": request.condition,
                        "interval_seconds": request.interval_seconds,
                        "expires_at": request.expires_at,
                        "one_shot": request.one_shot,
                        "notification_level": request.notification_level,
                    }
                )
            )
        except Exception as error:
            raise watcher_error(error) from error
        return watcher.model_dump(mode="json")

    @app.get("/watchers/{watcher_id}")
    async def inspect_watcher(
        watcher_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        watcher = await app.state.watcher_service.get(watcher_id)
        if watcher is None:
            raise HTTPException(status_code=404, detail="Obserwacja nie istnieje.")
        return watcher.model_dump(mode="json")

    @app.post("/watchers/{watcher_id}/pause")
    async def pause_watcher(
        watcher_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        try:
            watcher = await app.state.watcher_service.pause(watcher_id)
        except Exception as error:
            raise watcher_error(error) from error
        return watcher.model_dump(mode="json")

    @app.post("/watchers/{watcher_id}/resume")
    async def resume_watcher(
        watcher_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        try:
            watcher = await app.state.watcher_service.resume(watcher_id)
        except Exception as error:
            raise watcher_error(error) from error
        return watcher.model_dump(mode="json")

    @app.post("/watchers/{watcher_id}/cancel")
    async def cancel_watcher(
        watcher_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        try:
            watcher = await app.state.watcher_service.cancel(watcher_id)
        except Exception as error:
            raise watcher_error(error) from error
        return watcher.model_dump(mode="json")

    @app.delete("/watchers/{watcher_id}")
    async def delete_watcher(
        watcher_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, bool]:
        try:
            removed = await app.state.watcher_service.delete(watcher_id)
        except Exception as error:
            raise watcher_error(error) from error
        return {"removed": removed}

    @app.post("/chat")
    async def chat(
        request: ChatRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, object]:
        await app.state.audio.cancel()
        try:
            operation_id = await app.state.chat.start(request.text, request.visual_context_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"status": "accepted", "operation_id": operation_id}

    @app.post("/vision/regions", response_model=RegionCaptureResponse)
    async def capture_region(
        request: CaptureRegionRequest,
        _: Annotated[None, Depends(authorize)],
    ) -> RegionCaptureResponse:
        operation_id = uuid4()
        region = Bounds.model_validate(request.region.model_dump())
        monitor = Bounds.model_validate(request.monitor_bounds.model_dump())
        try:
            outcome = await asyncio.to_thread(
                app.state.tool_platform.capture_region,
                ContextProviderArguments(reason=request.reason),
                operation_id,
                region=region,
                monitor_bounds=monitor,
                dpi_scale=request.dpi_scale,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        preview = None
        if outcome.preview is not None:
            preview = "data:image/jpeg;base64," + b64encode(outcome.preview).decode("ascii")
            outcome.preview[:] = b"\0" * len(outcome.preview)
            outcome.preview.clear()
            outcome.preview = None
        if outcome.result.available:
            app.state.vision_store.put(outcome)
        else:
            outcome.clear()
        return RegionCaptureResponse(result=outcome.result, preview_data_url=preview)

    @app.delete("/vision/captures/{capture_id}")
    async def discard_region(
        capture_id: UUID,
        _: Annotated[None, Depends(authorize)],
    ) -> dict[str, bool]:
        return {"removed": app.state.vision_store.discard(capture_id)}

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
            # Keep fail-closed session cleanup before an await: TestClient and
            # real server shutdown may cancel this task repeatedly.
            if queue is not None:
                runtime.unsubscribe(queue)
            runtime.sessions.discard(task)
            if not runtime.sessions:
                # A confirmation must never remain actionable when no trusted UI
                # session can still display its exact target and consequences.
                app.state.tool_engine.cancel_all_confirmations()
            await asyncio.gather(*children, return_exceptions=True)
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
