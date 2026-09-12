"""Real local audio adapters; heavyweight models load lazily off the event loop."""

from __future__ import annotations

import asyncio
import ctypes
import math
import sys
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from .audio import AudioConfig, PcmFrame
from .providers import (
    ProviderUnavailableError,
    SpeechToTextRequest,
    SpeechToTextResponse,
    TextToSpeechRequest,
    TextToSpeechResponse,
    TranscriptionConfidence,
    TranscriptionSegment,
)
from .stt import SttProviderStatus, SttRuntimeProfile, SttStatusState


def list_input_devices() -> list[dict[str, object]]:
    """Return bounded, presentation-safe microphone metadata from PortAudio."""
    import sounddevice as sd

    default_input = sd.default.device[0]
    devices: list[dict[str, object]] = []
    for index, raw in enumerate(sd.query_devices()):
        channels = int(raw["max_input_channels"])
        if channels <= 0:
            continue
        name = str(raw["name"])[:128]
        devices.append(
            {
                "id": index,
                "name": name,
                "sample_rate": round(float(raw["default_samplerate"])),
                "channels": channels,
                "is_default": index == default_input,
            }
        )
    return devices[:64]


class ThreadSafeAudioIngress:
    """Bounded bridge; producer threads can only enqueue onto the owner loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, maximum_frames: int = 8) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[PcmFrame | BaseException | None] = asyncio.Queue(
            maxsize=maximum_frames
        )
        self._closed = False

    def push_from_thread(self, item: PcmFrame | BaseException) -> None:
        if not self._closed:
            self._loop.call_soon_threadsafe(self._enqueue, item)

    def _enqueue(self, item: PcmFrame | BaseException) -> None:
        if self._closed:
            return
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(item)

    async def next(self) -> PcmFrame | None:
        item = await self._queue.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        self._closed = True
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(None)


class SoundDeviceMicrophone:
    """Bounded microphone stream whose callback only schedules onto asyncio."""

    def __init__(self, config: AudioConfig) -> None:
        self._config = config
        self._stream: Any = None
        self._ingress: ThreadSafeAudioIngress | None = None
        self._closed = False

    def _callback(self, indata: Any, _frames: int, _time: Any, status: Any) -> None:
        ingress = self._ingress
        if ingress is None or self._closed:
            return
        if status:
            ingress.push_from_thread(OSError("microphone stream error"))
            return
        ingress.push_from_thread(
            PcmFrame(pcm_s16le=bytes(indata), sample_rate=self._config.sample_rate)
        )

    async def frames(self) -> AsyncIterator[PcmFrame]:
        if self._closed:
            return
        import sounddevice as sd

        self._ingress = ThreadSafeAudioIngress(asyncio.get_running_loop())
        blocksize = self._config.sample_rate * self._config.frame_duration_ms // 1_000
        stream = sd.RawInputStream(
            samplerate=self._config.sample_rate,
            blocksize=blocksize,
            device=self._config.microphone_device,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )
        self._stream = stream
        try:
            await asyncio.to_thread(stream.start)
            while not self._closed:
                item = await self._ingress.next()
                if item is None:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            await self._close_stream()

    async def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            await asyncio.to_thread(stream.close)

    async def close(self) -> None:
        self._closed = True
        if self._ingress is not None:
            self._ingress.close()
        await self._close_stream()


class OpenWakeWordProvider:
    def __init__(self, model_path: str | None = None, model_name: str = "alexa") -> None:
        self._model_path = model_path
        self._model_name = model_name
        self._model: Any = None
        self._model_lock = threading.Lock()

    def _load(self) -> Any:
        with self._model_lock:
            if self._model is None:
                import openwakeword
                from openwakeword.model import Model

                path = self._model_path
                if path is None:
                    metadata = openwakeword.models.get(self._model_name)
                    if not metadata:
                        raise ProviderUnavailableError("Unknown development wake model")
                    path = metadata["model_path"]
                if not Path(path).is_file():
                    raise ProviderUnavailableError("Wake model is unavailable")
                self._model = Model(wakeword_model_paths=[path])
        return self._model

    def _score(self, audio: PcmFrame) -> float:
        import numpy as np

        if audio.sample_rate != 16_000:
            raise ProviderUnavailableError("openWakeWord requires 16 kHz input")
        predictions = self._load().predict(np.frombuffer(audio.pcm_s16le, dtype=np.int16))
        if not predictions:
            return 0.0
        return float(max(predictions.values()))

    async def score(self, audio: PcmFrame) -> float:
        return await asyncio.to_thread(self._score, audio)

    def _score_clip(self, audio: PcmFrame) -> float:
        import numpy as np

        if audio.sample_rate != 16_000:
            raise ProviderUnavailableError("openWakeWord requires 16 kHz input")
        model = self._load()
        model.reset()
        predictions = model.predict_clip(
            np.frombuffer(audio.pcm_s16le, dtype=np.int16), padding=1, chunk_size=1_280
        )
        return max(
            (float(value) for frame in predictions for value in frame.values()),
            default=0.0,
        )

    async def score_clip(self, audio: PcmFrame) -> float:
        """Score a held-out clip through the same streaming runtime as live wake audio."""
        return await asyncio.to_thread(self._score_clip, audio)

    def activate_model(self, model_path: str, model_name: str) -> None:
        path = Path(model_path).resolve()
        if not path.is_file() or path.suffix.casefold() != ".onnx":
            raise ProviderUnavailableError("Wake model is unavailable")
        with self._model_lock:
            self._model_path = str(path)
            self._model_name = model_name
            self._model = None

    def reset(self) -> None:
        if self._model is not None and hasattr(self._model, "reset"):
            self._model.reset()


class SileroVadProvider:
    def __init__(self) -> None:
        self._model: Any = None
        self._pending = bytearray()

    def _load(self) -> Any:
        if self._model is None:
            from silero_vad import load_silero_vad

            self._model = load_silero_vad(onnx=True)
        return self._model

    def _probability(self, audio: PcmFrame) -> float:
        import numpy as np
        import torch

        if audio.sample_rate != 16_000:
            raise ProviderUnavailableError("Silero VAD requires 16 kHz input")
        self._pending.extend(audio.pcm_s16le)
        scores: list[float] = []
        chunk_bytes = 512 * 2
        while len(self._pending) >= chunk_bytes:
            raw = bytes(self._pending[:chunk_bytes])
            del self._pending[:chunk_bytes]
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32_768.0
            scores.append(float(self._load()(torch.from_numpy(samples), 16_000).item()))
        return max(scores, default=0.0)

    async def probability(self, audio: PcmFrame) -> float:
        return await asyncio.to_thread(self._probability, audio)

    def reset(self) -> None:
        self._pending.clear()
        if self._model is not None:
            self._model.reset_states()


class FasterWhisperPolishProvider:
    def __init__(
        self,
        profile: SttRuntimeProfile,
        *,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.profile = profile
        self._model: Any = None
        self._model_factory = model_factory
        self._model_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._generation_lock = threading.Lock()
        self._generation = 0
        self._hotwords: str | None = None
        self._status = SttProviderStatus(profile=profile, state="loading")
        self._load_failure_state: SttStatusState | None = None

    def set_hotwords(self, hotwords: str | None) -> None:
        self._hotwords = hotwords

    def _runtime_issue(self) -> SttStatusState | None:
        if self._model_factory is not None:
            return None
        if (
            self.profile.device == "cuda"
            and sys.platform == "win32"
            and not _windows_cuda_runtime_available()
        ):
            return "cuda_unavailable"
        try:
            import ctranslate2

            supported = ctranslate2.get_supported_compute_types(self.profile.device)
        except Exception:
            return "cuda_unavailable" if self.profile.device == "cuda" else "unavailable"
        if self.profile.compute_type not in supported:
            return "cuda_unavailable" if self.profile.device == "cuda" else "unavailable"
        return None

    def _model_available(self) -> bool:
        if self._model is not None or self._model_factory is not None:
            return True
        if Path(self.profile.model).is_dir():
            return True
        try:
            from faster_whisper.utils import download_model

            download_model(self.profile.model, local_files_only=True)
        except Exception:
            return False
        return True

    async def status(self) -> SttProviderStatus:
        if self._load_failure_state is not None:
            return self._status
        if self._model is not None:
            return SttProviderStatus(profile=self.profile, state="ready")
        issue = await asyncio.to_thread(self._runtime_issue)
        if issue is not None:
            self._status = SttProviderStatus(
                profile=self.profile,
                state=issue,
                detail=(
                    "CUDA lub wybrany typ obliczeń jest niedostępny."
                    if issue == "cuda_unavailable"
                    else "Środowisko STT jest niedostępne."
                ),
            )
            return self._status
        if not await asyncio.to_thread(self._model_available):
            self._status = SttProviderStatus(
                profile=self.profile,
                state="missing_model",
                detail="Model STT nie jest zainstalowany lokalnie.",
            )
            return self._status
        self._status = SttProviderStatus(profile=self.profile, state="ready")
        return self._status

    def _load(self) -> Any:
        with self._model_lock:
            if self._model is not None:
                return self._model
            issue = self._runtime_issue()
            if issue is not None:
                self._status = SttProviderStatus(profile=self.profile, state=issue)
                raise ProviderUnavailableError("Configured STT runtime is unavailable")
            factory = self._model_factory
            if factory is None:
                from faster_whisper import WhisperModel

                factory = WhisperModel
            self._status = SttProviderStatus(profile=self.profile, state="loading")
            try:
                self._model = factory(
                    self.profile.model,
                    device=self.profile.device,
                    compute_type=self.profile.compute_type,
                    local_files_only=True,
                )
            except Exception as error:
                state = _stt_load_failure_state(
                    self.profile,
                    error,
                    model_available=self._model_available(),
                )
                self._load_failure_state = state
                self._status = SttProviderStatus(profile=self.profile, state=state)
                raise ProviderUnavailableError(
                    "Configured STT model could not be loaded"
                ) from error
            self._load_failure_state = None
            self._status = SttProviderStatus(profile=self.profile, state="ready")
        return self._model

    def _next_generation(self) -> int:
        with self._generation_lock:
            self._generation += 1
            return self._generation

    def _is_current(self, generation: int) -> bool:
        with self._generation_lock:
            return generation == self._generation

    def _transcribe(self, request: SpeechToTextRequest, generation: int) -> SpeechToTextResponse:
        import numpy as np

        with self._inference_lock:
            if not self._is_current(generation):
                raise ProviderUnavailableError("Transcription cancelled")
            samples = np.frombuffer(request.pcm_s16le, dtype=np.int16).astype(np.float32) / 32_768.0
            segments, _info = self._load().transcribe(
                samples,
                language="pl",
                task="transcribe",
                beam_size=self.profile.beam_size,
                best_of=self.profile.best_of,
                patience=self.profile.patience,
                temperature=self.profile.temperature,
                condition_on_previous_text=self.profile.condition_on_previous_text,
                no_speech_threshold=self.profile.no_speech_threshold,
                log_prob_threshold=self.profile.log_probability_threshold,
                compression_ratio_threshold=self.profile.compression_ratio_threshold,
                hotwords=self._hotwords,
                vad_filter=False,
                word_timestamps=False,
            )
            results: list[TranscriptionSegment] = []
            for segment in segments:
                if not self._is_current(generation):
                    raise ProviderUnavailableError("Transcription cancelled")
                text = str(segment.text).strip()
                if text:
                    results.append(
                        TranscriptionSegment(
                            start_seconds=max(0, float(segment.start)),
                            end_seconds=max(0, float(segment.end)),
                            text=text,
                            average_log_probability=float(segment.avg_logprob),
                            no_speech_probability=float(segment.no_speech_prob),
                        )
                    )
            transcript = " ".join(segment.text for segment in results).strip()
            if not transcript:
                raise ProviderUnavailableError("No Polish speech was transcribed")
            return SpeechToTextResponse(
                transcript=transcript,
                language="pl",
                duration_ms=request.duration_ms,
                segments=tuple(results),
                confidence=_transcription_confidence(results),
            )

    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        if request.language != "pl":
            raise ValueError("V1 transcription language must be Polish")
        generation = self._next_generation()
        try:
            return await asyncio.to_thread(self._transcribe, request, generation)
        except asyncio.CancelledError:
            raise
        except ProviderUnavailableError:
            raise
        except Exception as error:
            state: SttStatusState = (
                "cuda_unavailable"
                if _is_cuda_runtime_failure(self.profile, error)
                else "transcription_failure"
            )
            if state == "cuda_unavailable":
                self._load_failure_state = state
            self._status = SttProviderStatus(
                profile=self.profile,
                state=state,
                detail="Lokalna inferencja STT nie powiodła się.",
            )
            raise ProviderUnavailableError("Local STT inference failed") from error

    async def cancel(self) -> None:
        self._next_generation()

    async def close(self) -> None:
        await self.cancel()

        def close_model() -> None:
            with self._inference_lock, self._model_lock:
                self._model = None

        await asyncio.to_thread(close_model)
        self._load_failure_state = None
        self._status = SttProviderStatus(profile=self.profile, state="loading")


def _transcription_confidence(
    segments: list[TranscriptionSegment],
) -> TranscriptionConfidence:
    log_values = [
        value.average_log_probability
        for value in segments
        if value.average_log_probability is not None
    ]
    silence_values = [
        value.no_speech_probability for value in segments if value.no_speech_probability is not None
    ]
    if not log_values and not silence_values:
        return TranscriptionConfidence()
    average_log = sum(log_values) / len(log_values) if log_values else None
    maximum_silence = max(silence_values) if silence_values else None
    reasons: list[str] = []
    if average_log is not None and average_log < -1.0:
        reasons.append("low_average_log_probability")
    if maximum_silence is not None and maximum_silence >= 0.7:
        reasons.append("high_no_speech_probability")
    if reasons:
        return TranscriptionConfidence(level="low", reasons=tuple(reasons))
    if (
        average_log is not None
        and average_log >= -0.45
        and (maximum_silence is None or maximum_silence <= 0.35)
    ):
        return TranscriptionConfidence(level="high", reasons=())
    return TranscriptionConfidence(level="medium", reasons=("mixed_whisper_signals",))


def _stt_load_failure_state(
    profile: SttRuntimeProfile, error: Exception, *, model_available: bool
) -> SttStatusState:
    if _is_cuda_runtime_failure(profile, error):
        return "cuda_unavailable"
    if not model_available:
        return "missing_model"
    return "unavailable"


def _is_cuda_runtime_failure(profile: SttRuntimeProfile, error: Exception) -> bool:
    detail = str(error).casefold()
    cuda_markers = ("cuda", "cublas", "cudnn", "nvrtc", "nvcuda")
    return profile.device == "cuda" and any(marker in detail for marker in cuda_markers)


def _windows_cuda_runtime_available() -> bool:
    for library in ("cublas64_12.dll", "cudnn64_9.dll"):
        try:
            ctypes.CDLL(library)
        except OSError:
            return False
    return True


class PiperPolishProvider:
    def __init__(self, voice_path: str | None) -> None:
        self._voice_path = voice_path
        self._voice: Any = None
        self._cancelled = threading.Event()
        self._stream: Any = None
        self._stream_lock = threading.Lock()

    def _load(self) -> Any:
        if not self._voice_path or not Path(self._voice_path).is_file():
            raise ProviderUnavailableError("Polish Piper voice model is unavailable")
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(self._voice_path, use_cuda=False)
        return self._voice

    def _speak(self, request: TextToSpeechRequest) -> TextToSpeechResponse:
        import sounddevice as sd

        self._cancelled.clear()
        total_samples = 0
        stream: Any = None
        try:
            for chunk in self._load().synthesize(request.text):
                if self._cancelled.is_set():
                    raise ProviderUnavailableError("Speech playback cancelled")
                if stream is None:
                    stream = sd.RawOutputStream(
                        samplerate=chunk.sample_rate,
                        channels=chunk.sample_channels,
                        dtype="int16",
                    )
                    stream.start()
                    with self._stream_lock:
                        self._stream = stream
                stream.write(chunk.audio_int16_bytes)
                total_samples += len(chunk.audio_int16_bytes) // (
                    chunk.sample_width * chunk.sample_channels
                )
            if stream is None:
                raise ProviderUnavailableError("Piper returned no audio")
            return TextToSpeechResponse(
                duration_ms=round(total_samples / float(stream.samplerate) * 1_000)
            )
        finally:
            with self._stream_lock:
                self._stream = None
            if stream is not None:
                stream.close(ignore_errors=True)

    async def speak(self, request: TextToSpeechRequest) -> TextToSpeechResponse:
        if request.language != "pl":
            raise ValueError("V1 speech language must be Polish")
        return await asyncio.to_thread(self._speak, request)

    async def cancel(self) -> None:
        self._cancelled.set()
        with self._stream_lock:
            stream = self._stream
        if stream is not None:
            await asyncio.to_thread(stream.abort, True)


def calibrate_pcm(frames: list[PcmFrame]) -> dict[str, float]:
    """Return non-sensitive level metrics without retaining input audio."""
    import numpy as np

    if not frames:
        raise ValueError("Calibration requires audio")
    samples = np.concatenate(
        [np.frombuffer(frame.pcm_s16le, dtype=np.int16).astype(np.float32) for frame in frames]
    )
    normalized = samples / 32_768.0
    rms = math.sqrt(float(np.mean(np.square(normalized))))
    peak = float(np.max(np.abs(normalized)))
    clipping_ratio = float(np.mean(np.abs(samples) >= 32_760))
    return {"rms": rms, "peak": peak, "clipping_ratio": clipping_ratio}
