"""Cancellable, in-memory audio orchestration owned by the core event loop."""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .providers import (
    SpeechToTextProvider,
    SpeechToTextRequest,
    TextToSpeechProvider,
    TextToSpeechRequest,
    VoiceActivityProvider,
    WakeWordProvider,
)
from .runtime import CoreRuntime
from .state import InvalidStateTransition

logger = logging.getLogger(__name__)


def _default_piper_voice() -> str | None:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        return None
    candidate = Path(root) / "Moj-Asystent" / "models" / "piper" / "pl_PL-gosia-medium.onnx"
    return str(candidate) if candidate.is_file() else None


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    microphone_device: int | str | None = None
    sample_rate: int = Field(default=16_000, ge=8_000, le=48_000)
    frame_duration_ms: int = Field(default=80, ge=20, le=200)
    wake_sensitivity: float = Field(default=0.5, ge=0, le=1)
    vad_start_threshold: float = Field(default=0.6, ge=0, le=1)
    vad_end_threshold: float = Field(default=0.35, ge=0, le=1)
    min_speech_ms: int = Field(default=160, ge=20, le=5_000)
    trailing_silence_ms: int = Field(default=560, ge=40, le=3_000)
    maximum_utterance_seconds: float = Field(default=30, gt=0, le=120)
    stt_model: str = Field(default="medium", min_length=1, max_length=128)
    tts_voice_path: str | None = Field(default_factory=_default_piper_voice)
    wake_model_path: str | None = None
    development_wake_model: str = Field(default="alexa", min_length=1, max_length=64)
    follow_up_timeout_seconds: float = Field(default=20, gt=0, le=60)
    voice_responses_enabled: bool = True
    error_recovery_seconds: float = Field(default=1, ge=0, le=10)
    microphone_retry_seconds: float = Field(default=2, gt=0, le=30)

    def model_post_init(self, _context: object) -> None:
        if self.vad_end_threshold >= self.vad_start_threshold:
            raise ValueError("VAD end threshold must be lower than start threshold")

    @classmethod
    def from_environment(cls) -> AudioConfig:
        values: dict[str, object] = {}
        for variable, field_name in {
            "MOJ_ASYSTENT_MICROPHONE_DEVICE": "microphone_device",
            "MOJ_ASYSTENT_STT_MODEL": "stt_model",
            "MOJ_ASYSTENT_TTS_VOICE_PATH": "tts_voice_path",
            "MOJ_ASYSTENT_WAKE_MODEL_PATH": "wake_model_path",
            "MOJ_ASYSTENT_DEVELOPMENT_WAKE_MODEL": "development_wake_model",
        }.items():
            if value := os.environ.get(variable):
                values[field_name] = value
        for variable, field_name in {
            "MOJ_ASYSTENT_SAMPLE_RATE": "sample_rate",
            "MOJ_ASYSTENT_WAKE_SENSITIVITY": "wake_sensitivity",
            "MOJ_ASYSTENT_VAD_START_THRESHOLD": "vad_start_threshold",
            "MOJ_ASYSTENT_VAD_END_THRESHOLD": "vad_end_threshold",
            "MOJ_ASYSTENT_MAX_UTTERANCE_SECONDS": "maximum_utterance_seconds",
            "MOJ_ASYSTENT_FOLLOW_UP_SECONDS": "follow_up_timeout_seconds",
        }.items():
            if value := os.environ.get(variable):
                values[field_name] = value
        if value := os.environ.get("MOJ_ASYSTENT_VOICE_RESPONSES"):
            values["voice_responses_enabled"] = value.lower() in {"1", "true", "yes"}
        return cls.model_validate(values)


@dataclass(frozen=True)
class PcmFrame:
    pcm_s16le: bytes
    sample_rate: int

    def __post_init__(self) -> None:
        if not self.pcm_s16le or len(self.pcm_s16le) % 2:
            raise ValueError("PCM frame must contain complete signed 16-bit samples")
        if not 8_000 <= self.sample_rate <= 48_000:
            raise ValueError("Unsupported sample rate")

    @property
    def duration_ms(self) -> float:
        return len(self.pcm_s16le) / 2 / self.sample_rate * 1_000


def normalize_pcm(frame: PcmFrame, target_rate: int = 16_000) -> PcmFrame:
    """Resample one bounded PCM frame for the fixed-rate wake/VAD/STT engines."""
    if frame.sample_rate == target_rate:
        return frame
    import numpy as np
    from scipy.signal import resample_poly

    divisor = math.gcd(frame.sample_rate, target_rate)
    samples = np.frombuffer(frame.pcm_s16le, dtype="<i2")
    normalized = resample_poly(
        samples.astype(np.float32), target_rate // divisor, frame.sample_rate // divisor
    )
    pcm = np.clip(np.rint(normalized), -32_768, 32_767).astype("<i2").tobytes()
    return PcmFrame(pcm_s16le=pcm, sample_rate=target_rate)


class AudioSource(Protocol):
    def frames(self) -> AsyncIterator[PcmFrame]: ...

    async def close(self) -> None: ...


class SpeechBoundary(Enum):
    NONE = "none"
    STARTED = "started"
    COMPLETED = "completed"


class SpeechBoundaryDetector:
    def __init__(self, config: AudioConfig) -> None:
        self._config = config
        self.reset()

    def reset(self) -> None:
        self.active = False
        self._elapsed_ms = 0.0
        self._speech_ms = 0.0
        self._silence_ms = 0.0

    def push(self, probability: float, frame: PcmFrame) -> SpeechBoundary:
        duration = frame.duration_ms
        if not self.active:
            if probability < self._config.vad_start_threshold:
                return SpeechBoundary.NONE
            self.active = True
            self._elapsed_ms = duration
            self._speech_ms = duration
            return SpeechBoundary.STARTED

        self._elapsed_ms += duration
        if probability >= self._config.vad_start_threshold:
            self._speech_ms += duration
            self._silence_ms = 0
        elif probability <= self._config.vad_end_threshold:
            self._silence_ms += duration

        if self._elapsed_ms >= self._config.maximum_utterance_seconds * 1_000 or (
            self._speech_ms >= self._config.min_speech_ms
            and self._silence_ms >= self._config.trailing_silence_ms
        ):
            return SpeechBoundary.COMPLETED
        return SpeechBoundary.NONE


class AudioPipeline:
    def __init__(
        self,
        runtime: CoreRuntime,
        config: AudioConfig,
        wake: WakeWordProvider,
        vad: VoiceActivityProvider,
        stt: SpeechToTextProvider,
        tts: TextToSpeechProvider,
        source: AudioSource | None = None,
    ) -> None:
        self._runtime = runtime
        self.config = config
        self._wake = wake
        self._vad = vad
        self._stt = stt
        self._tts = tts
        self._source = source
        self._detector = SpeechBoundaryDetector(config)
        self._capture = bytearray()
        self._generation = 0
        self._operation_id: UUID | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._processing_task: asyncio.Task[None] | None = None
        self._follow_up_task: asyncio.Task[None] | None = None
        self._playback_active = False
        self._wake_suspended = False
        self.closed = False
        self.last_error_code: str | None = None

    @property
    def current_operation_id(self) -> UUID | None:
        return self._operation_id

    async def activate_wake_model(
        self, model_path: str, model_name: str, sensitivity: float
    ) -> None:
        """Swap a validated wake model on the owner loop without disturbing STT/TTS."""
        if not 0.05 <= sensitivity <= 0.95:
            raise ValueError("Wake sensitivity is outside the supported range")
        activate = getattr(self._wake, "activate_model", None)
        if activate is None:
            raise RuntimeError("Wake provider cannot reload a model")
        await asyncio.to_thread(activate, model_path, model_name)
        self.config = self.config.model_copy(
            update={"wake_model_path": model_path, "wake_sensitivity": sensitivity}
        )
        self._wake.reset()

    def set_wake_suspended(self, suspended: bool) -> None:
        self._wake_suspended = suspended
        if suspended:
            self._wake.reset()

    async def start(self) -> None:
        if self.closed:
            raise RuntimeError("Audio pipeline is closed")
        if self._source is not None and self._capture_task is None:
            self._capture_task = asyncio.create_task(self._consume_source())

    async def pause_capture(self) -> None:
        """Release the live microphone while the onboarding recorder owns it."""
        task = self._capture_task
        self._capture_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._source is not None:
            await self._source.close()
            self._source = None

    async def resume_capture(self, source: AudioSource) -> None:
        if self.closed:
            raise RuntimeError("Audio pipeline is closed")
        await self.pause_capture()
        self._source = source
        await self.start()

    async def _consume_source(self) -> None:
        assert self._source is not None
        while not self.closed:
            try:
                async for frame in self._source.frames():
                    await self.handle_frame(frame)
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, ValueError):
                await self._fail("microphone_unavailable")
            if not self.closed:
                await asyncio.sleep(self.config.microphone_retry_seconds)

    async def handle_frame(self, frame: PcmFrame) -> None:
        if self.closed or self._playback_active or self._processing_task is not None:
            return
        arrival_generation = self._generation
        if frame.sample_rate != 16_000:
            frame = await asyncio.to_thread(normalize_pcm, frame)
        if self.closed or arrival_generation != self._generation:
            return
        state = self._runtime.state
        if state == "idle":
            if self._wake_suspended:
                return
            if await self._wake.score(frame) >= self.config.wake_sensitivity:
                await self._activate_from_wake()
            return
        if state not in {"listening", "follow_up"}:
            return

        generation = self._generation
        probability = await self._vad.probability(frame)
        if self.closed or generation != self._generation or self._runtime.state != state:
            return
        boundary = self._detector.push(probability, frame)
        if boundary is SpeechBoundary.STARTED:
            if state == "follow_up":
                self._cancel_follow_up_timer()
                self._runtime.transition("listening", expected_state="follow_up")
            self._capture = bytearray(frame.pcm_s16le)
        elif self._detector.active:
            self._capture.extend(frame.pcm_s16le)
        if boundary is SpeechBoundary.COMPLETED:
            audio = bytes(self._capture)
            self._capture.clear()
            self._detector.reset()
            self._vad.reset()
            generation = self._generation
            operation_id = uuid4()
            self._operation_id = operation_id
            self._processing_task = asyncio.create_task(
                self._process(audio, frame.sample_rate, generation, operation_id)
            )

    async def _activate_from_wake(self) -> None:
        if self._runtime.state != "idle":
            return
        self._generation += 1
        self._wake.reset()
        self._vad.reset()
        self._detector.reset()
        self._runtime.transition("wake_detected", expected_state="idle")
        self._runtime.transition("listening", expected_state="wake_detected")

    async def manual_listen(self) -> None:
        if self.closed:
            return
        await self.cancel()
        self._generation += 1
        self._vad.reset()
        self._detector.reset()
        if self._runtime.state == "idle":
            self._runtime.transition("listening", expected_state="idle")

    async def _process(
        self, audio: bytes, sample_rate: int, generation: int, operation_id: UUID
    ) -> None:
        try:
            self._runtime.transition("transcribing", expected_state="listening")
            response = await self._stt.transcribe(
                SpeechToTextRequest(
                    pcm_s16le=audio,
                    sample_rate=sample_rate,
                    language="pl",
                    duration_ms=round(len(audio) / 2 / sample_rate * 1_000),
                )
            )
            if not self._is_current(generation, operation_id):
                return
            self._runtime.publish_transcript(operation_id, response)
            self._runtime.transition("thinking", expected_state="transcribing")
            answer = f"Usłyszałem: „{response.transcript}”. To testowa odpowiedź bez modelu AI."
            self._runtime.publish_placeholder_response(operation_id, answer)
            if self.config.voice_responses_enabled:
                self._runtime.transition("speaking", expected_state="thinking")
                self._playback_active = True
                try:
                    await self._tts.speak(TextToSpeechRequest(text=answer, language="pl"))
                finally:
                    self._playback_active = False
                if not self._is_current(generation, operation_id):
                    return
            await self._open_follow_up_window(generation)
        except asyncio.CancelledError:
            raise
        except Exception:  # Providers are an external boundary; details stay out of logs.
            logger.warning("audio_operation_failed", extra={"operation_id": str(operation_id)})
            if self._is_current(generation, operation_id):
                await self._fail("provider_unavailable")
        finally:
            if self._operation_id == operation_id:
                self._operation_id = None
            if self._processing_task is asyncio.current_task():
                self._processing_task = None

    def _is_current(self, generation: int, operation_id: UUID) -> bool:
        return (
            not self.closed
            and generation == self._generation
            and operation_id == self._operation_id
        )

    async def _open_follow_up_window(self, generation: int) -> None:
        state = self._runtime.state
        if state == "speaking":
            self._runtime.transition("follow_up", expected_state="speaking")
        elif state == "thinking":
            self._runtime.transition("follow_up", expected_state="thinking")
        self._cancel_follow_up_timer()
        self._follow_up_task = asyncio.create_task(self._follow_up_timeout(generation))

    async def _follow_up_timeout(self, generation: int) -> None:
        try:
            await asyncio.sleep(self.config.follow_up_timeout_seconds)
            if generation == self._generation and self._runtime.state == "follow_up":
                self._generation += 1
                self._runtime.transition("idle", expected_state="follow_up")
        except asyncio.CancelledError:
            raise

    def _cancel_follow_up_timer(self) -> None:
        task = self._follow_up_task
        self._follow_up_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def wait_current_operation(self) -> None:
        task = self._processing_task
        if task is not None:
            await task

    async def cancel(self) -> None:
        self._generation += 1
        self._operation_id = None
        self._capture.clear()
        self._detector.reset()
        self._cancel_follow_up_timer()
        tasks = [task for task in (self._processing_task,) if task is not None]
        for task in tasks:
            task.cancel()
        await self._stt.cancel()
        await self._tts.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._processing_task = None
        if self._runtime.state != "idle" and not self._runtime.stopping:
            try:
                self._runtime.transition("idle")
            except InvalidStateTransition:
                await self._fail("invalid_audio_state")

    async def _fail(self, code: str) -> None:
        self.last_error_code = code
        self._generation += 1
        self._operation_id = None
        if not self._runtime.stopping and self._runtime.state != "error":
            self._runtime.transition("error")
        await asyncio.sleep(self.config.error_recovery_seconds)
        if not self._runtime.stopping and self._runtime.state == "error":
            self._runtime.transition("idle", expected_state="error")

    async def shutdown(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._generation += 1
        self._cancel_follow_up_timer()
        tasks = [
            task
            for task in (self._capture_task, self._processing_task)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        await self._stt.cancel()
        await self._tts.cancel()
        if self._source is not None:
            await self._source.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._capture_task = None
        self._processing_task = None
        self._capture.clear()
