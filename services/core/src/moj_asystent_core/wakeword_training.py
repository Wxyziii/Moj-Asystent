"""Cancellable local openWakeWord-compatible training and onboarding sessions."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .audio import PcmFrame, normalize_pcm
from .audio_providers import OpenWakeWordProvider, calibrate_pcm
from .wakeword import (
    CancellationToken,
    NameAssessment,
    RecordingStep,
    SampleQuality,
    ValidationMetrics,
    WakeModelMetadata,
    WakeModelStore,
    analyze_name,
    analyze_sample,
    build_curriculum,
    calculate_validation,
    select_sensitivity,
)


class TrainingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CalibrationResult(TrainingModel):
    rms: float
    peak: float
    clipping_ratio: float
    noise_floor_rms: float
    ready: bool
    message: str


class OnboardingSessionView(TrainingModel):
    session_id: UUID
    name: NameAssessment
    microphone_device: str | int | None
    keep_training_samples: bool
    curriculum: tuple[RecordingStep, ...]
    accepted_step_ids: tuple[str, ...]
    calibration: CalibrationResult | None
    training_job_id: UUID | None
    candidate_ready: bool
    validation: ValidationMetrics | None


class TrainingJobView(TrainingModel):
    job_id: UUID
    session_id: UUID
    status: str
    progress: int = Field(ge=0, le=100)
    stage: str
    error: str | None = None


class WakeTrainer(Protocol):
    backend_version: str

    def train(
        self,
        session_directory: Path,
        curriculum: tuple[RecordingStep, ...],
        output_path: Path,
        cancellation: CancellationToken,
        progress: Callable[[int, str], None],
        seed: int,
    ) -> None: ...


class OpenWakeWordOnnxTrainer:
    """Train the official openWakeWord embedding classifier and export ONNX."""

    backend_version = "openwakeword-embedding-dnn-v1"

    def train(
        self,
        session_directory: Path,
        curriculum: tuple[RecordingStep, ...],
        output_path: Path,
        cancellation: CancellationToken,
        progress: Callable[[int, str], None],
        seed: int,
    ) -> None:
        import torch
        from openwakeword.utils import AudioFeatures

        torch.set_num_threads(max(1, min(2, os.cpu_count() or 1)))
        rng = np.random.default_rng(seed)
        progress(8, "Przygotowuję próbki")
        labels_by_id = {step.id: step.kind.value for step in curriculum}
        clips: list[np.ndarray] = []
        labels: list[float] = []
        for path in sorted(session_directory.glob("*.pcm")):
            cancellation.raise_if_cancelled()
            kind = labels_by_id.get(path.stem)
            if kind is None:
                continue
            raw = np.frombuffer(path.read_bytes(), dtype="<i2")
            clips.extend(_augment_clip(raw.astype(np.int16), rng))
            labels.extend([1.0 if kind in {"positive", "natural_command"} else 0.0] * 6)
        if len(set(labels)) != 2 or len(labels) < 24:
            raise ValueError("Za mało zróżnicowanych próbek do treningu")
        cancellation.raise_if_cancelled()
        progress(26, "Wyznaczam cechy głosu")
        # openWakeWord's official 16 kHz frozen embedding backbone.
        features = AudioFeatures().embed_clips(
            np.stack(clips), batch_size=16, ncpu=max(1, min(2, os.cpu_count() or 1))
        )
        x = torch.from_numpy(np.asarray(features, dtype=np.float32))
        y = torch.tensor(labels, dtype=torch.float32).reshape(-1, 1)
        generator = torch.Generator().manual_seed(seed)
        order = torch.randperm(len(x), generator=generator)
        x, y = x[order], y[order]
        split = max(2, round(len(x) * 0.8))
        train_x, train_y = x[:split], y[:split]
        input_shape = tuple(int(value) for value in train_x.shape[1:])
        model = torch.nn.Sequential(
            torch.nn.Flatten(),
            torch.nn.Linear(int(np.prod(input_shape)), 32),
            torch.nn.LayerNorm(32),
            torch.nn.ReLU(),
            torch.nn.Linear(32, 1),
            torch.nn.Sigmoid(),
        )
        optimizer = torch.optim.Adam(model.parameters(), lr=0.002)
        loss = torch.nn.BCELoss()
        progress(45, "Uczę rozpoznawania imienia")
        model.train()
        for epoch in range(240):
            cancellation.raise_if_cancelled()
            optimizer.zero_grad()
            output = model(train_x)
            value = loss(output, train_y)
            value.backward()
            optimizer.step()
            if epoch % 30 == 0:
                progress(45 + round(epoch / 240 * 38), "Uczę rozpoznawania imienia")
        cancellation.raise_if_cancelled()
        progress(88, "Eksportuję model ONNX")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".onnx.partial")
        model.eval()
        torch.onnx.export(
            model,
            (torch.rand((1, *input_shape)),),
            temporary,
            input_names=["input"],
            output_names=["wake_score"],
            opset_version=13,
            dynamo=False,
        )
        cancellation.raise_if_cancelled()
        os.replace(temporary, output_path)
        progress(100, "Model gotowy do walidacji")


def _augment_clip(samples: np.ndarray, rng: np.random.Generator) -> list[np.ndarray]:
    target = 32_000
    base = np.zeros(target, dtype=np.int16)
    source = samples[:target]
    start = max(0, (target - len(source)) // 2)
    base[start : start + len(source)] = source
    variants: list[np.ndarray] = []
    for index in range(6):
        gain = 1.0 if index == 0 else float(rng.uniform(0.72, 1.22))
        shift = 0 if index == 0 else int(rng.integers(-1_200, 1_201))
        noise = np.zeros(target) if index == 0 else rng.normal(0, rng.uniform(25, 180), target)
        mixed = np.roll(base.astype(np.float64), shift) * gain + noise
        variants.append(np.clip(np.rint(mixed), -32_768, 32_767).astype(np.int16))
    return variants


class _Session:
    def __init__(self, view: OnboardingSessionView, directory: Path) -> None:
        self.view = view
        self.directory = directory
        self.candidate_path = directory / "candidate.onnx"
        self.positive_scores: list[float] = []
        self.negative_scores: list[float] = []
        self.negative_seconds = 0.0


class _Job:
    def __init__(self, view: TrainingJobView, cancellation: CancellationToken) -> None:
        self.view = view
        self.cancellation = cancellation
        self.task: asyncio.Task[None] | None = None


class WakeOnboardingService:
    def __init__(
        self,
        store: WakeModelStore,
        trainer: WakeTrainer,
        activate_runtime: Callable[[WakeModelMetadata], Awaitable[None]],
    ) -> None:
        self.store = store
        self.trainer = trainer
        self._activate_runtime = activate_runtime
        self._sessions: dict[UUID, _Session] = {}
        self._jobs: dict[UUID, _Job] = {}
        self._generation = 0

    def active(self) -> WakeModelMetadata | None:
        return self.store.load_active()

    def begin(
        self,
        name: str,
        microphone_device: str | int | None,
        keep_training_samples: bool,
    ) -> OnboardingSessionView:
        assessment = analyze_name(name)
        if not assessment.trainable:
            raise ValueError("Ta nazwa jest zbyt krótka do wiarygodnego treningu")
        session_id = uuid4()
        directory = self.store.sessions / str(session_id)
        directory.mkdir(parents=True, exist_ok=False)
        view = OnboardingSessionView(
            session_id=session_id,
            name=assessment,
            microphone_device=microphone_device,
            keep_training_samples=keep_training_samples,
            curriculum=build_curriculum(name),
            accepted_step_ids=(),
            calibration=None,
            training_job_id=None,
            candidate_ready=False,
            validation=None,
        )
        self._sessions[session_id] = _Session(view, directory)
        return view

    def get(self, session_id: UUID) -> OnboardingSessionView:
        return self._session(session_id).view

    def calibrate(self, session_id: UUID, frame: PcmFrame) -> CalibrationResult:
        session = self._session(session_id)
        frame = normalize_pcm(frame)
        metrics = calibrate_pcm([frame])
        ready = metrics["clipping_ratio"] <= 0.002 and metrics["rms"] >= 0.005
        result = CalibrationResult(
            **metrics,
            noise_floor_rms=max(0.003, min(0.04, metrics["rms"] * 0.35)),
            ready=ready,
            message=(
                "Mikrofon jest gotowy."
                if ready
                else "Zmień poziom mikrofonu lub ogranicz hałas i spróbuj ponownie."
            ),
        )
        session.view = session.view.model_copy(update={"calibration": result})
        return result

    def add_sample(self, session_id: UUID, step_id: str, frame: PcmFrame) -> SampleQuality:
        session = self._session(session_id)
        frame = normalize_pcm(frame)
        step = next((item for item in session.view.curriculum if item.id == step_id), None)
        if step is None:
            raise ValueError("Nieznany krok nagrania")
        floor = session.view.calibration.noise_floor_rms if session.view.calibration else 0.008
        quality = analyze_sample(
            frame.pcm_s16le,
            frame.sample_rate,
            expected_seconds=step.expected_seconds,
            noise_floor_rms=floor,
        )
        path = session.directory / f"{step.id}.pcm"
        if quality.accepted:
            path.write_bytes(frame.pcm_s16le)
            os.chmod(path, 0o600)
            accepted = tuple(dict.fromkeys((*session.view.accepted_step_ids, step.id)))
            session.view = session.view.model_copy(update={"accepted_step_ids": accepted})
        else:
            path.unlink(missing_ok=True)
        return quality

    def start_training(self, session_id: UUID, *, seed: int = 44) -> TrainingJobView:
        session = self._session(session_id)
        if session.view.training_job_id is not None:
            current = self._jobs.get(session.view.training_job_id)
            if current is not None and current.view.status == "running":
                raise ValueError("Trening tej nazwy już trwa")
        required = {step.id for step in session.view.curriculum}
        # Reconcile the in-memory view with files already accepted before a
        # transient UI/core desynchronization. This preserves completed work.
        on_disk = {
            path.stem
            for path in session.directory.glob("*.pcm")
            if path.is_file() and path.stat().st_size > 0
        }
        accepted = tuple(
            step.id
            for step in session.view.curriculum
            if step.id in required and step.id in on_disk
        )
        if set(accepted) != set(session.view.accepted_step_ids):
            session.view = session.view.model_copy(update={"accepted_step_ids": accepted})
        missing = [step.id for step in session.view.curriculum if step.id not in accepted]
        if missing:
            raise ValueError(f"Brakuje próbek: {', '.join(missing)}")
        self._generation += 1
        generation = self._generation
        job_id = uuid4()
        view = TrainingJobView(
            job_id=job_id,
            session_id=session_id,
            status="running",
            progress=0,
            stage="Przygotowuję trening",
        )
        job = _Job(view, CancellationToken())
        self._jobs[job_id] = job
        session.view = session.view.model_copy(
            update={"training_job_id": job_id, "candidate_ready": False}
        )

        def progress(value: int, stage: str) -> None:
            loop.call_soon_threadsafe(self._update_job, job_id, generation, value, stage)

        loop = asyncio.get_running_loop()

        async def run() -> None:
            try:
                await asyncio.to_thread(
                    self.trainer.train,
                    session.directory,
                    session.view.curriculum,
                    session.candidate_path,
                    job.cancellation,
                    progress,
                    seed,
                )
                if generation != self._generation or job.cancellation.cancelled:
                    session.candidate_path.unlink(missing_ok=True)
                    return
                job.view = job.view.model_copy(
                    update={"status": "ready", "progress": 100, "stage": "Gotowy do walidacji"}
                )
                session.view = session.view.model_copy(update={"candidate_ready": True})
            except asyncio.CancelledError:
                job.view = job.view.model_copy(
                    update={"status": "cancelled", "stage": "Trening anulowany"}
                )
                session.candidate_path.unlink(missing_ok=True)
                self._cleanup_partial_outputs(session)
            except Exception:
                job.view = job.view.model_copy(
                    update={
                        "status": "failed",
                        "stage": "Trening nie powiódł się",
                        "error": "Nie udało się zbudować modelu.",
                    }
                )
                session.candidate_path.unlink(missing_ok=True)
                self._cleanup_partial_outputs(session)

        job.task = asyncio.create_task(run())
        return job.view

    def _update_job(self, job_id: UUID, generation: int, progress: int, stage: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None and generation == self._generation:
            job.view = job.view.model_copy(update={"progress": progress, "stage": stage})

    def job(self, job_id: UUID) -> TrainingJobView:
        try:
            return self._jobs[job_id].view
        except KeyError as error:
            raise ValueError("Nieznane zadanie treningowe") from error

    async def cancel_training(self, job_id: UUID) -> TrainingJobView:
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("Nieznane zadanie treningowe")
        self._generation += 1
        job.cancellation.cancel()
        if job.task is not None:
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
        job.view = job.view.model_copy(update={"status": "cancelled", "stage": "Trening anulowany"})
        self._session(job.view.session_id).candidate_path.unlink(missing_ok=True)
        self._cleanup_partial_outputs(self._session(job.view.session_id))
        return job.view

    async def validate_sample(
        self, session_id: UUID, frame: PcmFrame, *, positive: bool
    ) -> ValidationMetrics:
        session = self._session(session_id)
        if not session.view.candidate_ready or not session.candidate_path.is_file():
            raise ValueError("Model nie jest gotowy do walidacji")
        provider = OpenWakeWordProvider(
            str(session.candidate_path), session.view.name.normalized_name
        )
        score = await provider.score_clip(frame)
        if positive:
            session.positive_scores.append(score)
        else:
            session.negative_scores.append(score)
            session.negative_seconds += frame.duration_ms / 1_000
        threshold = select_sensitivity(session.positive_scores, session.negative_scores)
        metrics = calculate_validation(
            session.positive_scores,
            session.negative_scores,
            threshold=threshold,
            negative_duration_seconds=max(session.negative_seconds, 0.001),
        )
        session.view = session.view.model_copy(update={"validation": metrics})
        return metrics

    async def activate(
        self, session_id: UUID, *, allow_override: bool = False
    ) -> WakeModelMetadata:
        session = self._session(session_id)
        metrics = session.view.validation
        if metrics is None or metrics.attempts < 5 or session.negative_seconds < 10:
            raise ValueError("Walidacja wymaga co najmniej 5 prób imienia i testu negatywnego")
        if not metrics.passed and not allow_override:
            raise ValueError("Walidacja nie spełnia progu jakości")
        metadata = WakeModelMetadata(
            model_id=uuid4(),
            assistant_name=session.view.name.display_name,
            normalized_name=session.view.name.normalized_name,
            backend_version=self.trainer.backend_version,
            model_version=1,
            model_path=str(session.candidate_path),
            trained_at=datetime.now(UTC),
            sensitivity=metrics.threshold,
            validated=True,
            validation=metrics,
            microphone_device=session.view.microphone_device,
            keep_training_samples=session.view.keep_training_samples,
        )
        activated = self.store.activate(metadata)
        await self._activate_runtime(activated)
        if not session.view.keep_training_samples:
            self.store.cleanup_session(session_id)
        return activated

    async def set_sensitivity(self, value: float) -> WakeModelMetadata:
        active = self.store.load_active()
        if active is None:
            raise ValueError("Brak aktywnego modelu")
        updated = active.model_copy(update={"sensitivity": value})
        temporary = self.store.metadata_path.with_suffix(".json.tmp")
        temporary.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, self.store.metadata_path)
        await self._activate_runtime(updated)
        return updated

    async def cancel_session(self, session_id: UUID) -> None:
        session = self._session(session_id)
        if session.view.training_job_id is not None:
            job = self._jobs.get(session.view.training_job_id)
            if job is not None and job.view.status == "running":
                await self.cancel_training(job.view.job_id)
        self._sessions.pop(session_id, None)
        if not session.view.keep_training_samples:
            self.store.cleanup_session(session_id)

    async def shutdown(self) -> None:
        for job in self._jobs.values():
            job.cancellation.cancel()
            if job.task is not None:
                job.task.cancel()
        await asyncio.gather(
            *(job.task for job in self._jobs.values() if job.task is not None),
            return_exceptions=True,
        )
        for session in self._sessions.values():
            if not session.view.keep_training_samples:
                shutil.rmtree(session.directory, ignore_errors=True)

    def _session(self, session_id: UUID) -> _Session:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise ValueError("Nieznana lub wygasła sesja onboardingu") from error

    @staticmethod
    def _cleanup_partial_outputs(session: _Session) -> None:
        for path in session.directory.glob("*.partial"):
            path.unlink(missing_ok=True)
