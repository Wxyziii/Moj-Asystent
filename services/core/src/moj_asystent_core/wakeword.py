"""Deterministic custom wake-name domain, local storage and training primitives."""

from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator


class WakeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NormalizedName(WakeModel):
    display: str
    normalized: str


class NameAssessment(WakeModel):
    display_name: str
    normalized_name: str
    score: int = Field(ge=0, le=100)
    rating: Literal["słaba", "dobra", "bardzo dobra"]
    false_trigger_risk: Literal["wysokie", "umiarkowane", "niskie"]
    syllable_count: int = Field(ge=0)
    trainable: bool
    warnings: tuple[str, ...]
    explanation: str


class SampleKind(StrEnum):
    POSITIVE = "positive"
    NATURAL_COMMAND = "natural_command"
    HARD_NEGATIVE = "hard_negative"
    ORDINARY_SPEECH = "ordinary_speech"


class RecordingStep(WakeModel):
    id: str
    kind: SampleKind
    phrase: str
    loudness: str
    distance: str
    intonation: str
    posture: str
    guidance: str
    avoid: str
    expected_seconds: tuple[float, float]


class SampleQuality(WakeModel):
    accepted: bool
    speech_detected: bool
    volume_ok: bool
    no_clipping: bool
    duration_ok: bool
    silence_ok: bool
    signal_to_noise_db: float
    rms: float
    peak: float
    clipping_ratio: float
    silence_ratio: float
    duration_seconds: float
    reason: str


class ValidationMetrics(WakeModel):
    attempts: int
    successful_activations: int
    missed_activations: int
    false_accepts: int
    recall: float
    false_accepts_per_hour: float
    threshold: float
    positive_scores: tuple[float, ...]
    negative_scores: tuple[float, ...]
    passed: bool


class WakeModelMetadata(WakeModel):
    model_id: UUID
    assistant_name: str = Field(min_length=2, max_length=32)
    normalized_name: str = Field(min_length=2, max_length=32)
    backend: Literal["openwakeword-onnx"] = "openwakeword-onnx"
    backend_version: str = Field(min_length=1, max_length=32)
    model_version: int = Field(ge=1)
    model_path: str
    trained_at: datetime
    sensitivity: float = Field(ge=0.05, le=0.95)
    validated: bool
    validation: ValidationMetrics | None = None
    microphone_device: str | int | None = None
    keep_training_samples: bool = False

    @field_validator("trained_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("trained_at must include timezone")
        return value.astimezone(UTC)

    @classmethod
    def for_test(
        cls,
        name: str,
        path: Path,
        *,
        validated: bool,
        sensitivity: float = 0.5,
    ) -> WakeModelMetadata:
        normalized = normalize_assistant_name(name)
        return cls(
            model_id=uuid4(),
            assistant_name=normalized.display,
            normalized_name=normalized.normalized,
            backend_version="test",
            model_version=1,
            model_path=str(path),
            trained_at=datetime.now(UTC),
            sensitivity=sensitivity,
            validated=validated,
        )


_COMMON_POLISH = {
    "ala",
    "ania",
    "halo",
    "hej",
    "jeden",
    "mama",
    "nie",
    "ola",
    "otwórz",
    "proszę",
    "tak",
}
_VOWELS = set("aąeęiouóy")


def normalize_assistant_name(value: str) -> NormalizedName:
    display = " ".join(unicodedata.normalize("NFC", value).strip().split())
    if (
        not display
        or len(display) > 32
        or any(unicodedata.category(char)[0] == "C" for char in display)
    ):
        raise ValueError("Nazwa musi mieć od 1 do 32 czytelnych znaków")
    if not any(char.isalpha() for char in display):
        raise ValueError("Nazwa musi zawierać literę")
    return NormalizedName(display=display, normalized=display.casefold())


def _syllables(value: str) -> int:
    groups = re.findall(r"[aąeęiouóy]+", value.casefold())
    return len(groups)


def analyze_name(value: str) -> NameAssessment:
    name = normalize_assistant_name(value)
    letters = "".join(char for char in name.normalized if char.isalpha())
    syllables = _syllables(letters)
    score = 88
    warnings: list[str] = []
    trainable = len(letters) >= 2 and syllables >= 1
    if len(letters) < 3:
        score -= 55
        warnings.append("Nazwa jest bardzo krótka i może ginąć w zwykłej mowie.")
    elif len(letters) < 5:
        score -= 18
        warnings.append("Krótka nazwa wymaga dokładniejszego testu fałszywych aktywacji.")
    if syllables == 1:
        score -= 18
        warnings.append("Jedna sylaba jest mniej charakterystyczna akustycznie.")
    elif syllables >= 2:
        score += 5
    if name.normalized in _COMMON_POLISH:
        score -= 35
        warnings.append("Nazwa przypomina częste polskie słowo lub polecenie.")
    if len(set(letters)) <= 2:
        score -= 12
        warnings.append("Powtarzalne brzmienie może zwiększać liczbę pomyłek.")
    score = max(0, min(100, score if trainable else min(score, 20)))
    rating: Literal["słaba", "dobra", "bardzo dobra"] = (
        "bardzo dobra" if score >= 80 else "dobra" if score >= 50 else "słaba"
    )
    risk: Literal["wysokie", "umiarkowane", "niskie"] = (
        "niskie" if score >= 80 else "umiarkowane" if score >= 50 else "wysokie"
    )
    return NameAssessment(
        display_name=name.display,
        normalized_name=name.normalized,
        score=score,
        rating=rating,
        false_trigger_risk=risk,
        syllable_count=syllables,
        trainable=trainable,
        warnings=tuple(warnings),
        explanation=(
            "Brzmienie jest wystarczająco długie i wyraźne do treningu."
            if score >= 75
            else "Ta nazwa może częściej aktywować się przypadkowo; sprawdzimy ją w walidacji."
        ),
    )


def _sound_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    for source, target in (
        ("rz", "ż"),
        ("ch", "h"),
        ("ó", "u"),
        ("ci", "ć"),
        ("si", "ś"),
        ("ni", "ń"),
        ("zi", "ź"),
        ("dż", "ż"),
        ("dź", "ź"),
    ):
        text = text.replace(source, target)
    return "".join(char for char in text if char.isalpha())


def generate_confusable_phrases(value: str, limit: int = 7) -> tuple[str, ...]:
    name = normalize_assistant_name(value)
    key = _sound_key(name.normalized)
    variants = {
        key[:-1] if len(key) > 2 else key + "a",
        key[1:] if len(key) > 2 else "o" + key,
        *({letter + key[1:] for letter in "mpktwsz"} if len(key) > 2 else set()),
        *(
            {
                key[:index] + vowel + key[index + 1 :]
                for index, character in enumerate(key)
                if character in _VOWELS
                for vowel in "aeiouy"
                if vowel != character
            }
        ),
    }
    clean = [
        item
        for item in sorted(variants)
        if item and item.casefold() != name.normalized and key not in _sound_key(item)
    ]
    templates = (
        "To brzmi jak {word}, ale jest częścią zwykłego zdania.",
        "Powtórzę {word} jeszcze raz podczas rozmowy.",
        "Nie chodzi teraz o {word}, tylko o ustawienia.",
        "Wróćmy do {word} po zakończeniu pracy.",
        "Czy słowo {word} pasuje do tego zdania?",
        "Dzisiaj usłyszałem {word} w tle.",
        "Zaczniemy od {word} i przejdziemy dalej.",
    )
    return tuple(
        templates[index % len(templates)].format(word=word)
        for index, word in enumerate(clean[:limit])
    )


def _step(
    step_id: str,
    kind: SampleKind,
    phrase: str,
    loudness: str,
    distance: str,
    intonation: str,
    posture: str,
    guidance: str,
    avoid: str,
    expected: tuple[float, float] = (0.45, 3.5),
) -> RecordingStep:
    return RecordingStep(
        id=step_id,
        kind=kind,
        phrase=phrase,
        loudness=loudness,
        distance=distance,
        intonation=intonation,
        posture=posture,
        guidance=guidance,
        avoid=avoid,
        expected_seconds=expected,
    )


def build_curriculum(value: str) -> tuple[RecordingStep, ...]:
    name = normalize_assistant_name(value).display
    variants = [
        ("normalnie", "50–80 cm", "neutralnie", "prosto do ekranu"),
        ("trochę ciszej, bez szeptu", "50–80 cm", "neutralnie", "prosto do ekranu"),
        ("trochę głośniej, bez krzyku", "50–80 cm", "neutralnie", "prosto do ekranu"),
        ("normalnie", "50–80 cm", "jak krótkie pytanie", "prosto do ekranu"),
        ("normalnie", "50–80 cm", "pilnie, ale spokojnie", "prosto do ekranu"),
        ("normalnie", "80–110 cm", "neutralnie", "lekko odchyl się"),
        ("normalnie", "50–80 cm", "neutralnie", "obróć głowę lekko w lewo"),
        ("normalnie", "50–80 cm", "neutralnie", "obróć głowę lekko w prawo"),
    ]
    steps = [
        _step(
            f"wake-{index + 1}",
            SampleKind.POSITIVE,
            name,
            loudness,
            distance,
            intonation,
            posture,
            "To przykład aktywujący — po sygnale wypowiedz tylko imię.",
            "Nie przeciągaj sylab i nie dodawaj innych słów.",
        )
        for index, (loudness, distance, intonation, posture) in enumerate(variants * 2)
    ]
    for index, suffix in enumerate(
        ("otwórz Spotify", "sprawdź temperaturę", "co się tutaj dzieje?")
    ):
        steps.append(
            _step(
                f"command-{index + 1}",
                SampleKind.NATURAL_COMMAND,
                f"{name}, {suffix}",
                "normalnie",
                "50–80 cm",
                "naturalnie, jednym zdaniem",
                "tak jak zwykle przy komputerze",
                "To przykład aktywujący w początku prawdziwego polecenia.",
                "Nie rób długiej pauzy między imieniem a poleceniem.",
                (1.0, 5.0),
            )
        )
    for index, phrase in enumerate(generate_confusable_phrases(name)):
        steps.append(
            _step(
                f"negative-{index + 1}",
                SampleKind.HARD_NEGATIVE,
                phrase,
                "normalnie",
                "50–80 cm",
                "jak zwykłe zdanie",
                "prosto do ekranu",
                "To przykład negatywny — asystent nie powinien się obudzić.",
                f"Nie wypowiadaj dokładnej nazwy „{name}”.",
                (1.5, 8.0),
            )
        )
    ordinary = (
        "Dzisiaj porządkuję pliki i sprawdzam ustawienia komputera. Potem zrobię krótką "
        "przerwę, otworzę kalendarz i wrócę do rozpoczętej pracy."
    )
    steps.append(
        _step(
            "ordinary-speech",
            SampleKind.ORDINARY_SPEECH,
            ordinary,
            "normalnie",
            "50–80 cm",
            "spokojnie, jak podczas rozmowy",
            "w zwykłej pozycji przy komputerze",
            "To dłuższy przykład negatywny do pomiaru przypadkowych aktywacji.",
            f"Nie dodawaj nazwy „{name}” i nie przyspieszaj sztucznie.",
            (8.0, 35.0),
        )
    )
    return tuple(steps)


def analyze_sample(
    pcm_s16le: bytes,
    sample_rate: int,
    *,
    expected_seconds: tuple[float, float],
    noise_floor_rms: float = 0.008,
) -> SampleQuality:
    if not pcm_s16le or len(pcm_s16le) % 2 or not 8_000 <= sample_rate <= 48_000:
        raise ValueError("Nieprawidłowy format próbki PCM")
    samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float64) / 32_768.0
    duration = len(samples) / sample_rate
    rms = math.sqrt(float(np.mean(samples * samples)))
    peak = float(np.max(np.abs(samples)))
    clipping = float(np.mean(np.abs(samples) >= 0.985))
    frame = max(1, round(sample_rate * 0.02))
    usable = samples[: len(samples) - len(samples) % frame]
    frame_rms = (
        np.sqrt(np.mean(usable.reshape(-1, frame) ** 2, axis=1)) if len(usable) else np.array([0.0])
    )
    silence_threshold = max(noise_floor_rms * 1.5, 0.01)
    active_frames = frame_rms >= silence_threshold
    if active_frames.any():
        first_active = int(np.flatnonzero(active_frames)[0])
        last_active = int(np.flatnonzero(active_frames)[-1]) + 1
        speech_window = frame_rms[first_active:last_active]
        # Fixed-duration browser recordings intentionally include a little
        # leading/trailing padding. Measure silence inside the spoken window so
        # a short name such as "Zbyszek" is not rejected for its tail padding.
        silence_ratio = float(np.mean(speech_window < silence_threshold))
    else:
        silence_ratio = 1.0
    speech = rms >= max(noise_floor_rms * 1.8, 0.012) and silence_ratio < 0.92
    volume_ok = 0.018 <= rms <= 0.55
    no_clipping = clipping <= 0.002 and peak < 0.999
    duration_ok = expected_seconds[0] <= duration <= expected_seconds[1]
    silence_ok = silence_ratio <= 0.75
    snr = 20 * math.log10(max(rms, 1e-8) / max(noise_floor_rms, 1e-8))
    accepted = speech and volume_ok and no_clipping and duration_ok and silence_ok and snr >= 6
    if 0.0005 < rms < 0.018:
        reason = "Za cicho. Powiedz frazę nieco głośniej."
    elif not speech:
        reason = "Nie wykryto wyraźnego głosu. Podejdź bliżej i spróbuj ponownie."
    elif not no_clipping:
        reason = "Dźwięk jest przesterowany. Mów ciszej lub odsuń mikrofon."
    elif not volume_ok:
        reason = (
            "Za cicho. Powiedz frazę nieco głośniej."
            if rms < 0.018
            else "Za głośno. Odsuń się trochę od mikrofonu."
        )
    elif not duration_ok:
        reason = "Nagranie ma niewłaściwą długość. Wypowiedz tylko wskazany tekst."
    elif not silence_ok:
        reason = "W nagraniu jest za dużo ciszy. Zacznij mówić tuż po sygnale."
    elif snr < 6:
        reason = "Tło jest zbyt głośne. Ogranicz hałas lub zbliż się do mikrofonu."
    else:
        reason = "Próbka jest gotowa."
    return SampleQuality(
        accepted=accepted,
        speech_detected=speech,
        volume_ok=volume_ok,
        no_clipping=no_clipping,
        duration_ok=duration_ok,
        silence_ok=silence_ok,
        signal_to_noise_db=round(snr, 2),
        rms=round(rms, 6),
        peak=round(peak, 6),
        clipping_ratio=round(clipping, 6),
        silence_ratio=round(silence_ratio, 6),
        duration_seconds=round(duration, 3),
        reason=reason,
    )


def select_sensitivity(positive_scores: list[float], negative_scores: list[float]) -> float:
    if not positive_scores:
        return 0.5
    positive_floor = float(np.percentile(positive_scores, 20))
    negative_ceiling = max(negative_scores, default=0.05)
    return round(max(0.2, min(0.85, (positive_floor + negative_ceiling) / 2)), 2)


def calculate_validation(
    positive_scores: list[float],
    negative_scores: list[float],
    *,
    threshold: float,
    negative_duration_seconds: float,
) -> ValidationMetrics:
    if not positive_scores or negative_duration_seconds <= 0:
        raise ValueError("Walidacja wymaga prób aktywacji i czasu testu negatywnego")
    hits = sum(score >= threshold for score in positive_scores)
    false_accepts = sum(score >= threshold for score in negative_scores)
    recall = hits / len(positive_scores)
    false_per_hour = false_accepts * 3_600 / negative_duration_seconds
    return ValidationMetrics(
        attempts=len(positive_scores),
        successful_activations=hits,
        missed_activations=len(positive_scores) - hits,
        false_accepts=false_accepts,
        recall=round(recall, 4),
        false_accepts_per_hour=round(false_per_hour, 3),
        threshold=threshold,
        positive_scores=tuple(positive_scores),
        negative_scores=tuple(negative_scores),
        passed=recall >= 0.8 and false_accepts == 0,
    )


def default_wake_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "Moj-Asystent" / "wakewords"


class WakeModelStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_wake_root()).resolve()
        self.models = self.root / "models"
        self.sessions = self.root / "sessions"
        self.metadata_path = self.root / "active.json"
        self.models.mkdir(parents=True, exist_ok=True)
        self.sessions.mkdir(parents=True, exist_ok=True)

    def load_active(self) -> WakeModelMetadata | None:
        if not self.metadata_path.is_file():
            return None
        try:
            metadata = WakeModelMetadata.model_validate_json(
                self.metadata_path.read_text(encoding="utf-8")
            )
            model_path = Path(metadata.model_path).resolve()
            if model_path.parent != self.models.resolve() or not model_path.is_file():
                return None
            return metadata
        except (OSError, ValueError):
            return None

    def activate(self, metadata: WakeModelMetadata) -> WakeModelMetadata:
        if not metadata.validated:
            raise ValueError("Nie można aktywować modelu bez udanej walidacji")
        source = Path(metadata.model_path).resolve()
        if not source.is_file() or source.suffix.casefold() != ".onnx":
            raise ValueError("Kandydat modelu ONNX nie istnieje")
        destination = (self.models / f"{metadata.model_id}.onnx").resolve()
        if destination.parent != self.models.resolve():
            raise ValueError("Nieprawidłowy identyfikator modelu")
        temporary_model = destination.with_suffix(".onnx.tmp")
        shutil.copyfile(source, temporary_model)
        os.chmod(temporary_model, 0o600)
        os.replace(temporary_model, destination)
        activated = metadata.model_copy(update={"model_path": str(destination)})
        temporary_metadata = self.metadata_path.with_suffix(".json.tmp")
        temporary_metadata.write_text(activated.model_dump_json(indent=2), encoding="utf-8")
        os.chmod(temporary_metadata, 0o600)
        os.replace(temporary_metadata, self.metadata_path)
        return activated

    def write_active_for_test(self, metadata: WakeModelMetadata) -> None:
        self.metadata_path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")

    def cleanup_session(self, session_id: UUID) -> None:
        directory = (self.sessions / str(session_id)).resolve()
        if directory.parent != self.sessions.resolve():
            raise ValueError("Nieprawidłowa sesja")
        shutil.rmtree(directory, ignore_errors=True)


@dataclass
class CancellationToken:
    _cancelled: bool = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise asyncio.CancelledError
