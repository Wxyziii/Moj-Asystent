"""Offline, opt-in benchmark harness for local Polish STT profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import unicodedata
import wave
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .audio_providers import FasterWhisperPolishProvider
from .providers import SpeechToTextRequest
from .stt import ManagedSttRuntime, SttRuntimeProfile

MAXIMUM_MANIFEST_BYTES = 1_048_576
MAXIMUM_AUDIO_BYTES = 100 * 1_048_576
MAXIMUM_SAMPLE_SECONDS = 120

DEFAULT_BENCHMARK_PROFILES = (
    SttRuntimeProfile(
        profile_id="medium_cpu_int8",
        model="medium",
        device="cpu",
        compute_type="int8",
    ),
    SttRuntimeProfile(
        profile_id="turbo_cuda_int8_float16",
        model="large-v3-turbo",
        device="cuda",
        compute_type="int8_float16",
    ),
    SttRuntimeProfile(
        profile_id="turbo_cuda_float16",
        model="large-v3-turbo",
        device="cuda",
        compute_type="float16",
    ),
)


class BenchmarkManifestError(ValueError):
    """Raised when a local benchmark manifest or WAV is unsafe or malformed."""


class _ManifestSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    audio: str = Field(min_length=1, max_length=240)
    reference: str = Field(min_length=1, max_length=2_000)


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    samples: tuple[_ManifestSample, ...] = Field(min_length=1, max_length=200)


class _LoadedSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    pcm_s16le: bytes
    sample_rate: int
    duration_ms: int
    reference: str
    reference_words: tuple[str, ...]


class SttBenchmarkSampleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_index: int = Field(ge=1, le=200)
    reference: str = Field(min_length=1, max_length=2_000)
    transcription: str | None = Field(default=None, max_length=8_000)
    audio_duration_ms: int = Field(gt=0, le=120_000)
    transcription_duration_ms: int = Field(ge=0, le=3_600_000)
    word_error_rate: float | None = Field(default=None, ge=0)
    status: Literal["completed", "failed"]
    failure_code: str | None = None


class SttBenchmarkProfileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    model: str
    device: str
    compute_type: str
    status: Literal["completed", "unavailable", "failed"]
    word_error_rate: float | None = Field(default=None, ge=0)
    audio_duration_ms: int = Field(ge=0)
    transcription_duration_ms: int = Field(ge=0)
    real_time_factor: float | None = Field(default=None, ge=0)
    average_latency_ms: float | None = Field(default=None, ge=0)
    process_memory_delta_bytes: int | None = None
    process_memory_after_bytes: int | None = Field(default=None, ge=0)
    gpu_memory_delta_bytes: int | None = None
    failure_count: int = Field(ge=0)
    failure_code: str | None = None
    samples: tuple[SttBenchmarkSampleResult, ...] = ()


class SttBenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    generated_at: datetime
    sample_count: int = Field(gt=0, le=200)
    profiles: tuple[SttBenchmarkProfileResult, ...]


def normalize_polish_transcript(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return tuple(
        "".join(character for character in token if character.isalnum() or character == "_")
        for token in normalized.split()
        if any(character.isalnum() or character == "_" for character in token)
    )


def word_error_rate(reference: str, hypothesis: str) -> float:
    reference_words = normalize_polish_transcript(reference)
    if not reference_words:
        raise ValueError("Reference transcript must contain at least one word")
    errors = _edit_distance(reference_words, normalize_polish_transcript(hypothesis))
    return errors / len(reference_words)


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_word in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_word in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1] + (reference_word != hypothesis_word),
                )
            )
        previous = current
    return previous[-1]


def _load_manifest(path: Path) -> tuple[_LoadedSample, ...]:
    manifest_path = path.resolve()
    try:
        if manifest_path.stat().st_size > MAXIMUM_MANIFEST_BYTES:
            raise BenchmarkManifestError("Manifest benchmarku jest zbyt duży.")
        manifest = _Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError, json.JSONDecodeError) as error:
        raise BenchmarkManifestError("Manifest benchmarku jest nieprawidłowy.") from error

    root = manifest_path.parent
    loaded: list[_LoadedSample] = []
    for sample in manifest.samples:
        relative = Path(sample.audio)
        if relative.is_absolute() or ".." in relative.parts:
            raise BenchmarkManifestError("Ścieżka audio musi być względna wobec manifestu.")
        audio_path = (root / relative).resolve()
        if not audio_path.is_relative_to(root):
            raise BenchmarkManifestError("Ścieżka audio musi być względna wobec manifestu.")
        if audio_path.suffix.casefold() != ".wav":
            raise BenchmarkManifestError("Benchmark obsługuje wyłącznie pliki WAV.")
        try:
            if audio_path.stat().st_size > MAXIMUM_AUDIO_BYTES:
                raise BenchmarkManifestError("Plik WAV przekracza bezpieczny limit rozmiaru.")
            with wave.open(str(audio_path), "rb") as recording:
                if recording.getnchannels() != 1:
                    raise BenchmarkManifestError("Plik WAV musi być mono.")
                if recording.getsampwidth() != 2 or recording.getcomptype() != "NONE":
                    raise BenchmarkManifestError("Plik WAV musi zawierać nieskompresowane PCM16.")
                sample_rate = recording.getframerate()
                if not 8_000 <= sample_rate <= 48_000:
                    raise BenchmarkManifestError("Częstotliwość WAV jest poza zakresem 8–48 kHz.")
                frame_count = recording.getnframes()
                duration_ms = round(frame_count / sample_rate * 1_000)
                if not 1 <= duration_ms <= MAXIMUM_SAMPLE_SECONDS * 1_000:
                    raise BenchmarkManifestError(
                        "Długość próbki WAV jest poza bezpiecznym limitem."
                    )
                pcm = recording.readframes(frame_count)
        except (OSError, EOFError, wave.Error) as error:
            raise BenchmarkManifestError("Nie udało się odczytać pliku WAV.") from error
        reference_words = normalize_polish_transcript(sample.reference)
        if not reference_words:
            raise BenchmarkManifestError("Transkrypcja referencyjna musi zawierać słowa.")
        loaded.append(
            _LoadedSample(
                pcm_s16le=pcm,
                sample_rate=sample_rate,
                duration_ms=duration_ms,
                reference=sample.reference,
                reference_words=reference_words,
            )
        )
    return tuple(loaded)


async def run_benchmark(
    manifest_path: Path,
    *,
    profiles: tuple[SttRuntimeProfile, ...] = DEFAULT_BENCHMARK_PROFILES,
    runtime_factory: Callable[[SttRuntimeProfile], ManagedSttRuntime] = (
        FasterWhisperPolishProvider
    ),
) -> SttBenchmarkReport:
    samples = _load_manifest(manifest_path)
    results: list[SttBenchmarkProfileResult] = []
    process = psutil.Process()
    for profile in profiles:
        runtime = runtime_factory(profile)
        before_memory = process.memory_info().rss
        before_gpu = _gpu_memory_bytes() if profile.device == "cuda" else None
        total_audio_ms = sum(sample.duration_ms for sample in samples)
        total_latency_ms = 0
        successful_audio_ms = 0
        total_errors = 0
        total_reference_words = 0
        failures = 0
        failure_code: str | None = None
        sample_results: list[SttBenchmarkSampleResult] = []
        try:
            status = await runtime.status()
            if status.state != "ready":
                results.append(
                    _unavailable_result(profile, total_audio_ms, status.state, before_memory)
                )
                continue
            for sample_index, sample in enumerate(samples, start=1):
                started = perf_counter()
                try:
                    response = await runtime.transcribe(
                        SpeechToTextRequest(
                            pcm_s16le=sample.pcm_s16le,
                            sample_rate=sample.sample_rate,
                            language="pl",
                            duration_ms=sample.duration_ms,
                        )
                    )
                except Exception:
                    latency_ms = round((perf_counter() - started) * 1_000)
                    failures += 1
                    failure_code = "transcription_failure"
                    sample_results.append(
                        SttBenchmarkSampleResult(
                            sample_index=sample_index,
                            reference=sample.reference,
                            audio_duration_ms=sample.duration_ms,
                            transcription_duration_ms=latency_ms,
                            status="failed",
                            failure_code="transcription_failure",
                        )
                    )
                    continue
                latency_ms = round((perf_counter() - started) * 1_000)
                total_latency_ms += latency_ms
                successful_audio_ms += sample.duration_ms
                hypothesis = normalize_polish_transcript(response.transcript)
                sample_errors = _edit_distance(sample.reference_words, hypothesis)
                total_errors += sample_errors
                total_reference_words += len(sample.reference_words)
                sample_results.append(
                    SttBenchmarkSampleResult(
                        sample_index=sample_index,
                        reference=sample.reference,
                        transcription=response.transcript,
                        audio_duration_ms=sample.duration_ms,
                        transcription_duration_ms=latency_ms,
                        word_error_rate=sample_errors / len(sample.reference_words),
                        status="completed",
                    )
                )
            after_memory = process.memory_info().rss
            after_gpu = _gpu_memory_bytes() if profile.device == "cuda" else None
            successful = len(samples) - failures
            status_value: Literal["completed", "unavailable", "failed"] = (
                "completed" if successful else "failed"
            )
            results.append(
                SttBenchmarkProfileResult(
                    profile_id=profile.profile_id,
                    model=_public_model(profile.model),
                    device=profile.device,
                    compute_type=profile.compute_type,
                    status=status_value,
                    word_error_rate=(
                        total_errors / total_reference_words if total_reference_words else None
                    ),
                    audio_duration_ms=total_audio_ms,
                    transcription_duration_ms=total_latency_ms,
                    real_time_factor=(
                        total_latency_ms / successful_audio_ms if successful else None
                    ),
                    average_latency_ms=(total_latency_ms / successful if successful else None),
                    process_memory_delta_bytes=after_memory - before_memory,
                    process_memory_after_bytes=after_memory,
                    gpu_memory_delta_bytes=(
                        after_gpu - before_gpu
                        if after_gpu is not None and before_gpu is not None
                        else None
                    ),
                    failure_count=failures,
                    failure_code=failure_code,
                    samples=tuple(sample_results),
                )
            )
        finally:
            await runtime.close()
    return SttBenchmarkReport(
        generated_at=datetime.now(UTC),
        sample_count=len(samples),
        profiles=tuple(results),
    )


def _unavailable_result(
    profile: SttRuntimeProfile,
    audio_duration_ms: int,
    failure_code: str,
    memory: int,
) -> SttBenchmarkProfileResult:
    return SttBenchmarkProfileResult(
        profile_id=profile.profile_id,
        model=_public_model(profile.model),
        device=profile.device,
        compute_type=profile.compute_type,
        status="unavailable",
        audio_duration_ms=audio_duration_ms,
        transcription_duration_ms=0,
        process_memory_delta_bytes=0,
        process_memory_after_bytes=memory,
        failure_count=0,
        failure_code=failure_code,
    )


def _public_model(model: str) -> str:
    return model.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "model-localny"


def _gpu_memory_bytes() -> int | None:
    try:
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
            return sum(
                int(process.usedGpuMemory)
                for process in processes
                if int(process.pid) == os.getpid() and process.usedGpuMemory is not None
            )
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Lokalny benchmark polskiego STT")
    parser.add_argument("manifest", type=Path, help="Ścieżka do lokalnego manifestu JSON")
    parser.add_argument("--output", type=Path, help="Opcjonalny plik raportu JSON")
    arguments = parser.parse_args()
    try:
        report = asyncio.run(run_benchmark(arguments.manifest))
    except BenchmarkManifestError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
    encoded = report.model_dump_json(indent=2)
    if arguments.output is not None:
        output = arguments.output.resolve()
        if output.suffix.casefold() != ".json":
            print("Raport musi mieć rozszerzenie .json.", file=sys.stderr)
            raise SystemExit(2)
        try:
            with output.open("x", encoding="utf-8") as destination:
                destination.write(encoded + "\n")
        except FileExistsError as error:
            print("Plik raportu już istnieje; nie został nadpisany.", file=sys.stderr)
            raise SystemExit(2) from error
        except OSError as error:
            print("Nie udało się bezpiecznie zapisać raportu.", file=sys.stderr)
            raise SystemExit(2) from error
    else:
        print(encoded)


if __name__ == "__main__":
    main()
