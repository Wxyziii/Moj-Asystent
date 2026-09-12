"""Deterministic, hardware-aware selection across replaceable model providers."""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from .llm import (
    LanguageModelProvider,
    LanguageModelRequest,
    ModelStatus,
    ModelStreamEvent,
    ModelUsageEvent,
    ProviderUnavailableError,
)
from .telemetry import TelemetryService

ModelTier = Literal["fast", "quality", "deep"]
ModelMode = Literal["private", "fast", "quality", "deep", "auto"]
DataPolicy = Literal["local_only", "cloud_allowed"]
ProviderName = Literal["ollama", "llama_cpp", "openrouter"]

GIB = 1024**3


class RoutingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderCapabilities(RoutingModel):
    text: bool = True
    image: bool = False
    structured_output: bool = False
    tools: bool = False
    context_size: int = Field(ge=1_024, le=1_010_000)


class HardwareSnapshot(RoutingModel):
    free_ram_bytes: int = Field(ge=0)
    total_ram_bytes: int = Field(gt=0)
    free_vram_bytes: int | None = Field(default=None, ge=0)
    total_vram_bytes: int | None = Field(default=None, ge=1)
    gpu_utilization_percent: float | None = Field(default=None, ge=0, le=100)
    high_load_active: bool = False


class RoutingRequest(RoutingModel):
    user_text: str = Field(min_length=1, max_length=8_192)
    explicit_tier: ModelTier | None = None
    context_characters: int = Field(default=0, ge=0, le=32_768)
    requires_image: bool = False
    requires_tools: bool = False
    sensitive_context: bool = False


class RoutingDecision(RoutingModel):
    requested_mode: ModelMode
    requested_tier: ModelTier
    selected_tier: ModelTier
    provider_name: ProviderName
    model: str = Field(min_length=1, max_length=128)
    local: bool
    capabilities: ProviderCapabilities
    fallback_reason: str | None = Field(default=None, max_length=512)
    explicit_override: bool


class ModelPerformanceMetric(RoutingModel):
    provider_name: ProviderName
    model: str = Field(min_length=1, max_length=128)
    first_token_ms: float | None = Field(default=None, ge=0)
    total_ms: float = Field(ge=0)
    output_characters: int = Field(ge=0, le=8_192)
    input_tokens: int | None = Field(default=None, ge=0, le=10_000_000)
    output_tokens: int | None = Field(default=None, ge=0, le=10_000_000)
    provider_cost: float | None = Field(default=None, ge=0, le=1_000_000)
    cost_unit: Literal["openrouter_credits"] | None = None
    completed: bool


class RoutingHardwareProvider(Protocol):
    async def capture(self) -> HardwareSnapshot: ...


class StaticHardwareProvider:
    """Conservative compatibility seam for tests and explicitly injected providers."""

    def __init__(self) -> None:
        self._snapshot = HardwareSnapshot(
            free_ram_bytes=32 * GIB,
            total_ram_bytes=32 * GIB,
            free_vram_bytes=8 * GIB,
            total_vram_bytes=8 * GIB,
            gpu_utilization_percent=0,
            high_load_active=False,
        )

    async def capture(self) -> HardwareSnapshot:
        return self._snapshot


class TelemetryRoutingHardwareProvider:
    """Adapts the bounded Milestone 9 snapshot into routing-only resource facts."""

    def __init__(
        self,
        telemetry: TelemetryService,
        *,
        high_load_processes: tuple[str, ...] = (),
    ) -> None:
        self._telemetry = telemetry
        self._high_load_processes = {
            item.strip().casefold() for item in high_load_processes if item.strip()
        }

    async def capture(self) -> HardwareSnapshot:
        from uuid import uuid4

        snapshot = await asyncio.to_thread(self._telemetry.capture, uuid4())
        gpu = snapshot.gpus[0] if snapshot.gpus else None
        process_names = {
            process.name.casefold()
            for process in (*snapshot.top_processes, *(gpu.processes if gpu else ()))
        }
        configured_high_load = bool(process_names & self._high_load_processes)
        gpu_high_load = bool(
            gpu is not None
            and gpu.utilization_percent is not None
            and gpu.utilization_percent >= 80
        )
        return HardwareSnapshot(
            free_ram_bytes=snapshot.memory.available_bytes,
            total_ram_bytes=snapshot.memory.total_bytes,
            free_vram_bytes=gpu.memory_free_bytes if gpu else None,
            total_vram_bytes=gpu.memory_total_bytes if gpu else None,
            gpu_utilization_percent=gpu.utilization_percent if gpu else None,
            high_load_active=configured_high_load or gpu_high_load,
        )


@dataclass(frozen=True)
class ModelCandidate:
    tier: ModelTier
    provider_name: ProviderName
    provider: LanguageModelProvider
    local: bool
    capabilities: ProviderCapabilities
    approximate_size_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.approximate_size_bytes is not None and self.approximate_size_bytes <= 0:
            raise ValueError("Approximate model size must be positive")
        if not self.local and self.provider_name != "openrouter":
            raise ValueError("Only the allowlisted cloud provider may be non-local")
        if self.local and self.provider_name == "openrouter":
            raise ValueError("OpenRouter cannot be marked local")


@dataclass(frozen=True)
class SelectedModel:
    decision: RoutingDecision
    candidate: ModelCandidate
    status: ModelStatus

    @property
    def provider_name(self) -> ProviderName:
        return self.decision.provider_name

    @property
    def model(self) -> str:
        return self.decision.model

    @property
    def selected_tier(self) -> ModelTier:
        return self.decision.selected_tier

    @property
    def local(self) -> bool:
        return self.decision.local

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self.decision.capabilities

    @property
    def fallback_reason(self) -> str | None:
        return self.decision.fallback_reason

    @property
    def explicit_override(self) -> bool:
        return self.decision.explicit_override


class ModelRoutingError(ProviderUnavailableError):
    """No configured candidate can safely satisfy the request."""


class ModelRouter:
    """Select one provider without invoking a model to make the decision."""

    def __init__(
        self,
        candidates: tuple[ModelCandidate, ...],
        hardware: RoutingHardwareProvider,
        *,
        mode: ModelMode = "auto",
        data_policy: DataPolicy = "local_only",
        idle_unload_seconds: float = 300,
    ) -> None:
        if not candidates or not any(item.tier == "fast" and item.local for item in candidates):
            raise ValueError("A local fast model candidate is required")
        if not 30 <= idle_unload_seconds <= 3_600:
            raise ValueError("Idle unload timeout is outside the supported bounds")
        self._candidates = candidates
        self._hardware = hardware
        self._mode = mode
        self._data_policy = data_policy
        self._idle_unload_seconds = idle_unload_seconds
        self._active: ModelCandidate | None = None
        self._active_streams = 0
        self._last_used = 0.0
        self._switch_lock = asyncio.Lock()
        self._metrics: deque[ModelPerformanceMetric] = deque(maxlen=64)
        self._idle_task: asyncio.Task[None] | None = None
        self._closed = False

    @classmethod
    def from_provider(cls, provider: LanguageModelProvider) -> ModelRouter:
        return cls(
            (
                ModelCandidate(
                    tier="fast",
                    provider_name="ollama",
                    provider=provider,
                    local=True,
                    capabilities=ProviderCapabilities(
                        text=True,
                        image=True,
                        structured_output=True,
                        tools=True,
                        context_size=32_768,
                    ),
                ),
            ),
            StaticHardwareProvider(),
            mode="fast",
        )

    @property
    def mode(self) -> ModelMode:
        return self._mode

    @property
    def data_policy(self) -> DataPolicy:
        return self._data_policy

    @property
    def model(self) -> str:
        return next(
            item.provider.model for item in self._candidates if item.tier == "fast" and item.local
        )

    def set_mode(self, mode: ModelMode) -> None:
        self._mode = mode

    def set_data_policy(self, policy: DataPolicy) -> None:
        self._data_policy = policy

    def metrics(self) -> tuple[ModelPerformanceMetric, ...]:
        return tuple(self._metrics)

    def is_resident(self, candidate: ModelCandidate) -> bool:
        return self._active is not None and self._active.provider is candidate.provider

    def last_used_monotonic(self, candidate: ModelCandidate) -> float | None:
        return self._last_used if self.is_resident(candidate) and self._last_used > 0 else None

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Model router is closed")
        if self._idle_task is None:
            self._idle_task = asyncio.create_task(self._idle_loop())

    async def select(self, request: RoutingRequest) -> SelectedModel:
        if self._closed:
            raise ModelRoutingError("Router modeli został zatrzymany.")
        target = request.explicit_tier or self._target_for_mode(request)
        if target == "fast":
            hardware = HardwareSnapshot(
                free_ram_bytes=0,
                total_ram_bytes=1,
                free_vram_bytes=None,
                total_vram_bytes=None,
                gpu_utilization_percent=None,
                high_load_active=False,
            )
        else:
            try:
                hardware = await self._hardware.capture()
            except Exception:
                # Telemetry failure must not take down basic chat. The zero-free-memory
                # snapshot permits Fast only and makes larger tiers fail predictably.
                hardware = HardwareSnapshot(
                    free_ram_bytes=0,
                    total_ram_bytes=1,
                    free_vram_bytes=None,
                    total_vram_bytes=None,
                    gpu_utilization_percent=None,
                    high_load_active=False,
                )
        explicit = request.explicit_tier is not None
        restriction_reason: str | None = None
        if (
            self._mode in {"auto", "private"}
            and not explicit
            and target != "fast"
            and hardware.high_load_active
        ):
            target = "fast"
            restriction_reason = "Wykryto wysokie obciążenie GPU; automatycznie użyto modelu Fast."

        failures: list[str] = []
        for candidate in self._ordered_candidates(target):
            rejection = self._candidate_rejection(candidate, request, hardware)
            if rejection is not None:
                failures.append(rejection)
                continue
            status = await self._status(candidate)
            if status.state != "ready":
                failures.append(_status_failure(candidate, status))
                continue
            fallback = restriction_reason
            if candidate.tier != target or failures:
                fallback = restriction_reason or "; ".join(failures[:3])
            decision = RoutingDecision(
                requested_mode=self._mode,
                requested_tier=request.explicit_tier or self._target_for_mode(request),
                selected_tier=candidate.tier,
                provider_name=candidate.provider_name,
                model=candidate.provider.model,
                local=candidate.local,
                capabilities=candidate.capabilities,
                fallback_reason=fallback,
                explicit_override=explicit,
            )
            return SelectedModel(decision=decision, candidate=candidate, status=status)
        raise ModelRoutingError("Żaden skonfigurowany model nie spełnia wymagań tej operacji.")

    async def activate(self, route: SelectedModel) -> None:
        async with self._switch_lock:
            if self._closed:
                raise ModelRoutingError("Router modeli został zatrzymany.")
            previous = self._active
            if previous is not None and previous.provider is not route.candidate.provider:
                await _unload(previous.provider)
            self._active = route.candidate
            self._last_used = time.monotonic()

    async def stream_turn(
        self, route: SelectedModel, request: LanguageModelRequest
    ) -> AsyncIterator[ModelStreamEvent]:
        await self.activate(route)
        async with self._switch_lock:
            self._active_streams += 1
        started = time.monotonic()
        first_token_ms: float | None = None
        output_characters = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        provider_cost: float | None = None
        cost_unit: Literal["openrouter_credits"] | None = None
        completed = False
        try:
            async for event in route.candidate.provider.stream_turn(request):
                if first_token_ms is None:
                    first_token_ms = (time.monotonic() - started) * 1_000
                text = getattr(event, "text", None)
                if isinstance(text, str):
                    output_characters += len(text)
                if isinstance(event, ModelUsageEvent):
                    input_tokens = event.input_tokens
                    output_tokens = event.output_tokens
                    provider_cost = event.provider_cost
                    cost_unit = event.cost_unit
                yield event
            completed = True
        finally:
            async with self._switch_lock:
                self._active_streams -= 1
                self._last_used = time.monotonic()
            self._metrics.append(
                ModelPerformanceMetric(
                    provider_name=route.candidate.provider_name,
                    model=route.candidate.provider.model,
                    first_token_ms=first_token_ms,
                    total_ms=(time.monotonic() - started) * 1_000,
                    output_characters=min(8_192, output_characters),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    provider_cost=provider_cost,
                    cost_unit=cost_unit,
                    completed=completed,
                )
            )

    async def catalog(self) -> tuple[tuple[ModelCandidate, ModelStatus], ...]:
        results = []
        for candidate in self._candidates:
            if not candidate.local and self._data_policy != "cloud_allowed":
                results.append(
                    (
                        candidate,
                        ModelStatus(
                            provider=candidate.provider_name,
                            model=candidate.provider.model,
                            state="unavailable",
                            detail="Dostęp do chmury jest wyłączony.",
                        ),
                    )
                )
                continue
            results.append((candidate, await self._status(candidate)))
        return tuple(results)

    async def primary_status(self) -> tuple[ModelCandidate, ModelStatus]:
        primary = next(item for item in self._candidates if item.tier == "fast" and item.local)
        return primary, await self._status(primary)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._idle_task
        self._idle_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._active is not None:
            await _unload(self._active.provider)
            self._active = None
        seen: set[int] = set()
        for candidate in self._candidates:
            identity = id(candidate.provider)
            if identity in seen:
                continue
            seen.add(identity)
            await candidate.provider.close()

    def _target_for_mode(self, request: RoutingRequest) -> ModelTier:
        if self._mode == "fast":
            return "fast"
        if self._mode == "quality":
            return "quality"
        if self._mode == "deep":
            return "deep"
        score = _complexity_score(request)
        if score >= 8:
            return "deep"
        if score >= 3:
            return "quality"
        return "fast"

    def _ordered_candidates(self, target: ModelTier) -> tuple[ModelCandidate, ...]:
        tiers: tuple[ModelTier, ...]
        if target == "deep":
            tiers = ("deep", "quality", "fast")
        elif target == "quality":
            tiers = ("quality", "fast")
        else:
            tiers = ("fast",)
        ordered: list[ModelCandidate] = []
        for tier in tiers:
            matching = [item for item in self._candidates if item.tier == tier]
            matching.sort(key=lambda item: (not item.local, item.provider_name))
            ordered.extend(matching)
        return tuple(ordered)

    def _candidate_rejection(
        self,
        candidate: ModelCandidate,
        request: RoutingRequest,
        hardware: HardwareSnapshot,
    ) -> str | None:
        if request.requires_image and not candidate.capabilities.image:
            return f"{candidate.provider.model} nie obsługuje obrazu."
        if request.requires_tools and not candidate.capabilities.tools:
            return f"{candidate.provider.model} nie obsługuje narzędzi."
        if not candidate.local:
            if self._mode == "private" or self._data_policy != "cloud_allowed":
                return "Polityka prywatności zabrania użycia modelu online."
            if request.sensitive_context or request.requires_image or request.requires_tools:
                return "Wrażliwy kontekst pozostaje lokalny."
            return None
        if candidate.tier == "quality":
            if hardware.free_ram_bytes < 6 * GIB:
                return "Za mało wolnej pamięci RAM dla modelu Quality."
            if hardware.total_vram_bytes is not None and hardware.total_vram_bytes < 6 * GIB:
                return "Za mało pamięci VRAM dla modelu Quality."
            if hardware.free_vram_bytes is not None and hardware.free_vram_bytes < 2 * GIB:
                return "Za mało wolnej pamięci VRAM dla modelu Quality."
        if candidate.tier == "deep" and (
            hardware.total_ram_bytes < 24 * GIB or hardware.free_ram_bytes < 16 * GIB
        ):
            return "Za mało pamięci RAM dla hybrydowego modelu Deep."
        return None

    async def _status(self, candidate: ModelCandidate) -> ModelStatus:
        try:
            return await candidate.provider.status()
        except Exception:
            return ModelStatus(
                provider=candidate.provider_name,
                model=candidate.provider.model,
                state="error",
                detail="Provider modelu nie zwrócił poprawnego statusu.",
            )

    async def _idle_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(min(60.0, self._idle_unload_seconds / 2))
                await self._unload_if_idle()
        except asyncio.CancelledError:
            raise

    async def _unload_if_idle(self) -> None:
        async with self._switch_lock:
            if (
                self._active is not None
                and self._active_streams == 0
                and self._last_used > 0
                and time.monotonic() - self._last_used >= self._idle_unload_seconds
            ):
                await _unload(self._active.provider)
                self._active = None


def explicit_tier_override(text: str) -> ModelTier | None:
    normalized = " ".join(text.casefold().split())
    if re.search(r"\b(przeanalizuj|przemyśl)\b.*\b(głębiej|dokładniej)\b", normalized):
        return "deep"
    if re.search(r"\bużyj\b.*\b(lepszego|dokładniejszego)\b.*\bmodelu\b", normalized):
        return "quality"
    return None


def contains_sensitive_text(text: str) -> bool:
    normalized = text.casefold()
    return bool(
        re.search(
            r"\b(hasło|password|api[ _-]?key|token|bearer|credential|secret|sekret|"
            r"klucz prywatny|cvv|pin)\b\s*[:=]",
            normalized,
        )
        or re.search(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", text)
    )


def _complexity_score(request: RoutingRequest) -> int:
    normalized = request.user_text.casefold()
    score = 0
    if len(request.user_text) >= 600:
        score += 2
    elif len(request.user_text) >= 240:
        score += 1
    if request.context_characters >= 8_000:
        score += 4
    elif request.context_characters >= 2_000:
        score += 2
    if "```" in request.user_text or request.user_text.count("\n") >= 8:
        score += 2
    if re.search(r"\b(kod|concurrency|wątek|wyścig|architektur|debug|algorytm)\w*\b", normalized):
        score += 2
    if re.search(r"\b(przeanalizuj|porównaj|trade[- ]?off|kompromis)\w*\b", normalized):
        score += 2
    if request.requires_image:
        score += 2
    return score


def _status_failure(candidate: ModelCandidate, status: ModelStatus) -> str:
    if status.state == "missing":
        return f"Model {candidate.provider.model} nie jest zainstalowany."
    if status.state == "loading":
        return f"Model {candidate.provider.model} nadal się ładuje."
    return status.detail or f"Provider {candidate.provider_name} jest niedostępny."


async def _unload(provider: LanguageModelProvider) -> None:
    unload = getattr(provider, "unload", None)
    if callable(unload):
        result = unload()
        if asyncio.iscoroutine(result):
            await cast(Awaitable[None], result)
