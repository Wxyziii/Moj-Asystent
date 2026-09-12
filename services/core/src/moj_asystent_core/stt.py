"""Deterministic local STT runtime selection and privacy-safe state."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import deque
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .providers import ProviderUnavailableError, SpeechToTextRequest, SpeechToTextResponse

SttDevice = Literal["cpu", "cuda"]
SttComputeType = Literal["int8", "int8_float16", "float16", "float32"]
SttStatusState = Literal[
    "ready",
    "loading",
    "unavailable",
    "missing_model",
    "cuda_unavailable",
    "fallback_active",
    "transcription_failure",
]
_VOCABULARY_ENTRY = re.compile(r"^[\w .+#-]+$", re.UNICODE)
DEFAULT_POLISH_TECHNICAL_VOCABULARY = (
    "GitHub",
    "Codex",
    "Qwen",
    "OpenRouter",
    "Proxmox",
    "Tauri",
    "Ollama",
    "NVIDIA",
    "Discord",
    "Steam",
    "PowerShell",
    "localhost",
)


class SttVocabulary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[Annotated[str, Field(min_length=1, max_length=64)], ...] = Field(
        default=(), max_length=32
    )

    @field_validator("entries", mode="before")
    @classmethod
    def normalize_entries(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, str):
                normalized.append(raw)
                continue
            if any(unicodedata.category(character).startswith("C") for character in raw):
                raise ValueError("Vocabulary entries cannot contain control characters")
            entry = " ".join(unicodedata.normalize("NFKC", raw).strip().split())
            key = entry.casefold()
            if key not in seen:
                normalized.append(entry)
                seen.add(key)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_entry_characters(self) -> SttVocabulary:
        if any(not _VOCABULARY_ENTRY.fullmatch(entry) for entry in self.entries):
            raise ValueError("Vocabulary entries contain unsupported characters")
        return self

    @property
    def hotwords(self) -> str | None:
        return ", ".join(self.entries) or None


class SttRuntimeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    model: str = Field(min_length=1, max_length=256)
    device: SttDevice
    compute_type: SttComputeType
    beam_size: int = Field(default=5, ge=1, le=10)
    best_of: int = Field(default=5, ge=1, le=10)
    patience: float = Field(default=1.0, ge=0.1, le=3.0)
    temperature: float = Field(default=0.0, ge=0, le=1)
    condition_on_previous_text: bool = False
    no_speech_threshold: float = Field(default=0.6, ge=0, le=1)
    log_probability_threshold: float = Field(default=-1.0, ge=-10, le=0)
    compression_ratio_threshold: float = Field(default=2.4, ge=1, le=10)


class SttProviderStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: SttRuntimeProfile
    state: SttStatusState
    detail: str | None = Field(default=None, min_length=1, max_length=256)
    fallback_active: bool = False
    fallback_reason: str | None = Field(default=None, min_length=1, max_length=256)


class SttTranscriptionDiagnostic(BaseModel):
    """Bounded operational metadata; transcript text and PCM are intentionally absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    diagnostic_id: UUID
    recorded_at: datetime
    utterance_duration_ms: int = Field(gt=0, le=120_000)
    vad_speech_start_ms: int = Field(ge=0, le=120_000)
    vad_speech_end_ms: int | None = Field(default=None, ge=0, le=120_000)
    transcription_duration_ms: int = Field(ge=0, le=3_600_000)
    real_time_factor: float = Field(ge=0, le=10_000)
    model: str = Field(min_length=1, max_length=256)
    device: SttDevice
    compute_type: SttComputeType
    segment_average_log_probability: float | None = Field(default=None, ge=-100, le=10)
    segment_max_no_speech_probability: float | None = Field(default=None, ge=0, le=1)
    fallback_reason: str | None = Field(default=None, min_length=1, max_length=256)
    pre_roll_ms: int = Field(ge=0, le=2_000)
    post_roll_ms: int = Field(ge=0, le=1_500)
    confidence: Literal["high", "medium", "low", "unknown"]
    outcome: Literal["success", "failure"]
    failure_code: (
        Literal[
            "missing_model", "cuda_unavailable", "provider_unavailable", "transcription_failure"
        ]
        | None
    ) = None


class ManagedSttRuntime(Protocol):
    profile: SttRuntimeProfile

    async def status(self) -> SttProviderStatus: ...

    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...

    def set_hotwords(self, hotwords: str | None) -> None: ...


class SttRuntimeSelector:
    """Choose the first ready local runtime in a fixed, explainable order."""

    def __init__(
        self,
        runtimes: tuple[ManagedSttRuntime, ...],
        *,
        vocabulary: SttVocabulary | None = None,
        maximum_diagnostics: int = 64,
    ) -> None:
        if not runtimes:
            raise ValueError("At least one STT runtime is required")
        if not 1 <= maximum_diagnostics <= 256:
            raise ValueError("STT diagnostic capacity is outside safe bounds")
        self._runtimes = runtimes
        self._vocabulary = vocabulary or SttVocabulary()
        self._active: ManagedSttRuntime | None = None
        self._status = SttProviderStatus(profile=runtimes[0].profile, state="loading")
        self._lock = asyncio.Lock()
        self._diagnostics: deque[SttTranscriptionDiagnostic] = deque(maxlen=maximum_diagnostics)
        for runtime in self._runtimes:
            runtime.set_hotwords(self._vocabulary.hotwords)

    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        async with self._lock:
            started = perf_counter()
            fallback_state: SttStatusState | None = None
            for index, runtime in enumerate(self._runtimes):
                status = await runtime.status()
                if status.state != "ready":
                    if fallback_state is None:
                        fallback_state = cast(SttStatusState, status.state)
                    self._status = status
                    continue
                try:
                    result = await runtime.transcribe(request)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if fallback_state is None:
                        fallback_state = cast(SttStatusState, "transcription_failure")
                    self._status = SttProviderStatus(
                        profile=runtime.profile,
                        state="transcription_failure",
                        detail="Transkrypcja lokalna nie powiodła się.",
                    )
                    continue
                self._active = runtime
                is_fallback = bool(index)
                reason = (
                    f"Preferowany profil: {fallback_state or 'unavailable'}."
                    if is_fallback
                    else None
                )
                self._status = status.model_copy(
                    update={
                        "state": "fallback_active" if is_fallback else "ready",
                        "fallback_active": is_fallback,
                        "fallback_reason": reason,
                    }
                )
                self._record_diagnostic(
                    request,
                    result,
                    runtime.profile,
                    started,
                    fallback_reason=reason,
                )
                return result
            self._record_diagnostic(
                request,
                None,
                self._runtimes[0].profile,
                started,
                failure_code=_failure_code(fallback_state),
            )
        raise ProviderUnavailableError("No local Polish STT runtime is available")

    async def status(self) -> SttProviderStatus:
        if self._active is not None:
            return self._status
        fallback_state: SttStatusState | None = None
        for index, runtime in enumerate(self._runtimes):
            status = await runtime.status()
            if status.state == "ready":
                if index:
                    return status.model_copy(
                        update={
                            "state": "fallback_active",
                            "fallback_active": True,
                            "fallback_reason": (
                                f"Preferowany profil: {fallback_state or 'unavailable'}."
                            ),
                        }
                    )
                return status
            if fallback_state is None:
                fallback_state = cast(SttStatusState, status.state)
            self._status = status
        return self._status

    @property
    def profiles(self) -> tuple[SttRuntimeProfile, ...]:
        return tuple(runtime.profile for runtime in self._runtimes)

    @property
    def vocabulary(self) -> SttVocabulary:
        return self._vocabulary

    def set_vocabulary(self, vocabulary: SttVocabulary) -> None:
        self._vocabulary = vocabulary
        for runtime in self._runtimes:
            runtime.set_hotwords(vocabulary.hotwords)

    def diagnostics(self) -> tuple[SttTranscriptionDiagnostic, ...]:
        return tuple(self._diagnostics)

    async def cancel(self) -> None:
        await asyncio.gather(*(runtime.cancel() for runtime in self._runtimes))

    async def close(self) -> None:
        await asyncio.gather(*(runtime.close() for runtime in self._runtimes))

    def _record_diagnostic(
        self,
        request: SpeechToTextRequest,
        response: SpeechToTextResponse | None,
        profile: SttRuntimeProfile,
        started: float,
        *,
        fallback_reason: str | None = None,
        failure_code: Literal[
            "missing_model", "cuda_unavailable", "provider_unavailable", "transcription_failure"
        ]
        | None = None,
    ) -> None:
        elapsed_ms = min(3_600_000, max(0, round((perf_counter() - started) * 1_000)))
        log_values = [
            segment.average_log_probability
            for segment in (response.segments if response is not None else ())
            if segment.average_log_probability is not None
        ]
        silence_values = [
            segment.no_speech_probability
            for segment in (response.segments if response is not None else ())
            if segment.no_speech_probability is not None
        ]
        self._diagnostics.append(
            SttTranscriptionDiagnostic(
                diagnostic_id=uuid4(),
                recorded_at=datetime.now(UTC),
                utterance_duration_ms=request.duration_ms,
                vad_speech_start_ms=request.vad_speech_start_ms,
                vad_speech_end_ms=request.vad_speech_end_ms,
                transcription_duration_ms=elapsed_ms,
                real_time_factor=min(10_000, elapsed_ms / request.duration_ms),
                model=profile.model,
                device=profile.device,
                compute_type=profile.compute_type,
                segment_average_log_probability=(
                    sum(log_values) / len(log_values) if log_values else None
                ),
                segment_max_no_speech_probability=max(silence_values) if silence_values else None,
                fallback_reason=fallback_reason,
                pre_roll_ms=request.pre_roll_ms,
                post_roll_ms=request.post_roll_ms,
                confidence=response.confidence.level if response is not None else "unknown",
                outcome="success" if response is not None else "failure",
                failure_code=failure_code,
            )
        )


def _failure_code(
    state: str | None,
) -> Literal["missing_model", "cuda_unavailable", "provider_unavailable", "transcription_failure"]:
    if state in {"missing_model", "cuda_unavailable", "transcription_failure"}:
        return state
    return "provider_unavailable"
