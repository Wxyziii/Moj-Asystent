"""Demand-driven, bounded and privacy-safe local system telemetry."""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

import psutil
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CpuTelemetry(TelemetryModel):
    percent: Annotated[float, Field(ge=0, le=100)] | None
    logical_processors: Annotated[StrictInt, Field(ge=1, le=4096)]
    sample_meaningful: bool


class MemoryTelemetry(TelemetryModel):
    total_bytes: Annotated[StrictInt, Field(gt=0)]
    used_bytes: Annotated[StrictInt, Field(ge=0)]
    available_bytes: Annotated[StrictInt, Field(ge=0)]
    percent: Annotated[float, Field(ge=0, le=100)]


class DiskTelemetry(TelemetryModel):
    device: Annotated[str, Field(min_length=1, max_length=128)]
    mountpoint: Annotated[str, Field(min_length=1, max_length=128)]
    total_bytes: Annotated[StrictInt, Field(gt=0)]
    used_bytes: Annotated[StrictInt, Field(ge=0)]
    free_bytes: Annotated[StrictInt, Field(ge=0)]
    percent: Annotated[float, Field(ge=0, le=100)]
    read_bytes_per_second: Annotated[float, Field(ge=0)] | None
    write_bytes_per_second: Annotated[float, Field(ge=0)] | None
    rates_available: bool
    throughput_scope: Literal["system", "volume", "unavailable"]


class NetworkTelemetry(TelemetryModel):
    interface: Annotated[str, Field(min_length=1, max_length=128)]
    total_bytes_sent: Annotated[StrictInt, Field(ge=0)]
    total_bytes_received: Annotated[StrictInt, Field(ge=0)]
    sent_bytes_per_second: Annotated[float, Field(ge=0)] | None
    received_bytes_per_second: Annotated[float, Field(ge=0)] | None
    rates_available: bool


class ProcessTelemetry(TelemetryModel):
    pid: Annotated[StrictInt, Field(gt=0)]
    name: Annotated[str, Field(min_length=1, max_length=128)]
    executable: None = None
    cpu_percent: Annotated[float, Field(ge=0, le=100)] | None = None
    rss_bytes: Annotated[StrictInt, Field(ge=0)]
    read_bytes_per_second: Annotated[float, Field(ge=0)] | None = None
    write_bytes_per_second: Annotated[float, Field(ge=0)] | None = None


class GpuProcessTelemetry(TelemetryModel):
    pid: Annotated[StrictInt, Field(gt=0)]
    name: Annotated[str, Field(min_length=1, max_length=128)]
    memory_used_bytes: Annotated[StrictInt, Field(ge=0)]
    utilization_percent: None = None


class GpuTelemetry(TelemetryModel):
    index: Annotated[StrictInt, Field(ge=0, le=32)]
    name: Annotated[str, Field(min_length=1, max_length=128)]
    utilization_percent: Annotated[float, Field(ge=0, le=100)] | None
    memory_total_bytes: Annotated[StrictInt, Field(gt=0)]
    memory_used_bytes: Annotated[StrictInt, Field(ge=0)]
    memory_free_bytes: Annotated[StrictInt, Field(ge=0)]
    temperature_celsius: Annotated[float, Field(ge=-50, le=200)] | None
    power_watts: Annotated[float, Field(ge=0)] | None
    power_limit_watts: Annotated[float, Field(gt=0)] | None
    processes: tuple[GpuProcessTelemetry, ...] = Field(max_length=20)
    unavailable_metrics: tuple[str, ...] = Field(max_length=16)


class DiagnosticFact(TelemetryModel):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
    level: Literal["elevated", "high", "concerning"]
    summary: Annotated[str, Field(min_length=1, max_length=256)]


class TelemetryTimings(TelemetryModel):
    sample_interval_ms: Annotated[float, Field(ge=0, le=5000)]
    total_ms: Annotated[float, Field(ge=0, le=10000)]


class TelemetrySnapshot(TelemetryModel):
    snapshot_id: UUID
    operation_id: UUID
    captured_at: datetime
    cpu: CpuTelemetry
    memory: MemoryTelemetry
    disks: tuple[DiskTelemetry, ...] = Field(max_length=8)
    network: tuple[NetworkTelemetry, ...] = Field(max_length=16)
    gpus: tuple[GpuTelemetry, ...] = Field(max_length=8)
    top_processes: tuple[ProcessTelemetry, ...] = Field(max_length=20)
    facts: tuple[DiagnosticFact, ...] = Field(max_length=20)
    unavailable_sources: tuple[str, ...] = Field(max_length=32)
    active_process_pid: int | None = None
    active_process_name: str | None = Field(default=None, max_length=128)
    timings: TelemetryTimings


@dataclass(frozen=True)
class MemoryReading:
    total_bytes: int
    available_bytes: int
    used_bytes: int


@dataclass(frozen=True)
class RawDisk:
    device: str
    mountpoint: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    read_bytes: int | None
    write_bytes: int | None


@dataclass(frozen=True)
class RawNetwork:
    interface: str
    bytes_sent: int
    bytes_received: int


@dataclass(frozen=True)
class RawProcess:
    pid: int
    name: str
    cpu_seconds: float
    rss_bytes: int
    read_bytes: int | None
    write_bytes: int | None


@dataclass(frozen=True)
class RawCounters:
    monotonic_at: float
    captured_at: datetime
    cpu_busy_seconds: float
    cpu_total_seconds: float
    logical_processors: int
    memory: MemoryReading
    disks: tuple[RawDisk, ...]
    networks: tuple[RawNetwork, ...]
    processes: tuple[RawProcess, ...]
    unavailable_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class GpuReading:
    index: int
    name: str
    utilization_percent: float | None
    memory_total_bytes: int
    memory_used_bytes: int
    temperature_celsius: float | None
    power_watts: float | None
    power_limit_watts: float | None
    process_memory: tuple[tuple[int, int], ...]
    unavailable_metrics: tuple[str, ...] = ()


class CounterProvider(Protocol):
    def sample(self) -> RawCounters: ...


class GpuProvider(Protocol):
    def sample(self) -> tuple[GpuReading, ...]: ...

    def close(self) -> None: ...


def _safe_name(value: object, fallback: str) -> str:
    text = value if isinstance(value, str) else fallback
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text).strip()
    return (text or fallback)[:128]


def _rate(current: int | None, previous: int | None, elapsed: float) -> float | None:
    if current is None or previous is None or elapsed <= 0 or current < previous:
        return None
    return round((current - previous) / elapsed, 2)


class TelemetryNormalizer:
    def normalize(
        self,
        current: RawCounters,
        previous: RawCounters | None,
        gpus: tuple[GpuReading, ...],
        operation_id: UUID,
        *,
        active_process: tuple[int, str] | None = None,
        total_ms: float = 0,
    ) -> TelemetrySnapshot:
        elapsed = current.monotonic_at - previous.monotonic_at if previous else 0
        cpu_percent = None
        if previous and elapsed > 0:
            total_delta = current.cpu_total_seconds - previous.cpu_total_seconds
            busy_delta = current.cpu_busy_seconds - previous.cpu_busy_seconds
            if total_delta > 0 and 0 <= busy_delta <= total_delta:
                cpu_percent = round(min(100, busy_delta / total_delta * 100), 1)
        memory_percent = round(
            min(100, current.memory.used_bytes / current.memory.total_bytes * 100), 1
        )
        previous_disks = {item.device: item for item in previous.disks} if previous else {}
        disks = tuple(
            DiskTelemetry(
                device=_safe_name(item.device, "dysk"),
                mountpoint=_safe_name(item.mountpoint, "wolumin"),
                total_bytes=max(1, item.total_bytes),
                used_bytes=max(0, item.used_bytes),
                free_bytes=max(0, item.free_bytes),
                percent=round(
                    min(100, max(0, item.used_bytes / max(1, item.total_bytes) * 100)), 1
                ),
                read_bytes_per_second=_rate(
                    item.read_bytes,
                    previous_disks[item.device].read_bytes
                    if item.device in previous_disks
                    else None,
                    elapsed,
                ),
                write_bytes_per_second=_rate(
                    item.write_bytes,
                    previous_disks[item.device].write_bytes
                    if item.device in previous_disks
                    else None,
                    elapsed,
                ),
                rates_available=(
                    item.device in previous_disks
                    and _rate(item.read_bytes, previous_disks[item.device].read_bytes, elapsed)
                    is not None
                    and _rate(item.write_bytes, previous_disks[item.device].write_bytes, elapsed)
                    is not None
                ),
                throughput_scope=(
                    "system"
                    if item.read_bytes is not None or item.write_bytes is not None
                    else "unavailable"
                ),
            )
            for item in current.disks[:8]
            if item.total_bytes > 0
        )
        previous_network = {item.interface: item for item in previous.networks} if previous else {}
        network = tuple(
            NetworkTelemetry(
                interface=_safe_name(item.interface, "interfejs"),
                total_bytes_sent=max(0, item.bytes_sent),
                total_bytes_received=max(0, item.bytes_received),
                sent_bytes_per_second=_rate(
                    item.bytes_sent,
                    previous_network[item.interface].bytes_sent
                    if item.interface in previous_network
                    else None,
                    elapsed,
                ),
                received_bytes_per_second=_rate(
                    item.bytes_received,
                    previous_network[item.interface].bytes_received
                    if item.interface in previous_network
                    else None,
                    elapsed,
                ),
                rates_available=(
                    item.interface in previous_network
                    and _rate(item.bytes_sent, previous_network[item.interface].bytes_sent, elapsed)
                    is not None
                    and _rate(
                        item.bytes_received,
                        previous_network[item.interface].bytes_received,
                        elapsed,
                    )
                    is not None
                ),
            )
            for item in current.networks[:16]
        )
        previous_processes = {item.pid: item for item in previous.processes} if previous else {}
        processes = []
        for item in current.processes:
            if item.pid <= 0:
                continue
            old = previous_processes.get(item.pid)
            cpu = None
            if old and elapsed > 0 and item.cpu_seconds >= old.cpu_seconds:
                cpu = round(
                    min(
                        100,
                        (item.cpu_seconds - old.cpu_seconds)
                        / elapsed
                        / current.logical_processors
                        * 100,
                    ),
                    1,
                )
            processes.append(
                ProcessTelemetry(
                    pid=item.pid,
                    name=_safe_name(item.name, f"PID {item.pid}"),
                    cpu_percent=cpu,
                    rss_bytes=max(0, item.rss_bytes),
                    read_bytes_per_second=_rate(
                        item.read_bytes, old.read_bytes if old else None, elapsed
                    ),
                    write_bytes_per_second=_rate(
                        item.write_bytes, old.write_bytes if old else None, elapsed
                    ),
                )
            )
        processes.sort(key=lambda item: (-(item.cpu_percent or 0), -item.rss_bytes, item.pid))
        by_pid = {item.pid: item.name for item in processes}
        gpu_models = tuple(self._gpu(item, by_pid) for item in gpus[:8])
        facts = self._facts(cpu_percent, memory_percent, gpu_models)
        return TelemetrySnapshot(
            snapshot_id=uuid4(),
            operation_id=operation_id,
            captured_at=current.captured_at,
            cpu=CpuTelemetry(
                percent=cpu_percent,
                logical_processors=max(1, current.logical_processors),
                sample_meaningful=cpu_percent is not None,
            ),
            memory=MemoryTelemetry(
                total_bytes=max(1, current.memory.total_bytes),
                used_bytes=max(0, current.memory.used_bytes),
                available_bytes=max(0, current.memory.available_bytes),
                percent=memory_percent,
            ),
            disks=disks,
            network=network,
            gpus=gpu_models,
            top_processes=tuple(processes[:20]),
            facts=facts,
            unavailable_sources=tuple(current.unavailable_sources[:32]),
            active_process_pid=active_process[0] if active_process else None,
            active_process_name=_safe_name(active_process[1], "aktywny proces")
            if active_process
            else None,
            timings=TelemetryTimings(
                sample_interval_ms=max(0, elapsed * 1000), total_ms=max(0, total_ms)
            ),
        )

    @staticmethod
    def _gpu(item: GpuReading, names: dict[int, str]) -> GpuTelemetry:
        return GpuTelemetry(
            index=item.index,
            name=_safe_name(item.name, f"GPU {item.index}"),
            utilization_percent=item.utilization_percent,
            memory_total_bytes=max(1, item.memory_total_bytes),
            memory_used_bytes=max(0, item.memory_used_bytes),
            memory_free_bytes=max(0, item.memory_total_bytes - item.memory_used_bytes),
            temperature_celsius=item.temperature_celsius,
            power_watts=item.power_watts,
            power_limit_watts=item.power_limit_watts,
            processes=tuple(
                GpuProcessTelemetry(
                    pid=pid,
                    name=names.get(pid, f"PID {pid}"),
                    memory_used_bytes=max(0, used),
                )
                for pid, used in item.process_memory[:20]
                if pid > 0 and used >= 0
            ),
            unavailable_metrics=tuple(item.unavailable_metrics[:16]),
        )

    @staticmethod
    def _facts(
        cpu: float | None, memory: float, gpus: tuple[GpuTelemetry, ...]
    ) -> tuple[DiagnosticFact, ...]:
        facts = []
        if cpu is not None and cpu >= 85:
            facts.append(
                DiagnosticFact(
                    code="cpu_utilization_high", level="high", summary=f"CPU: {cpu:.1f}%"
                )
            )
        if memory >= 90:
            facts.append(
                DiagnosticFact(
                    code="memory_utilization_high", level="high", summary=f"RAM: {memory:.1f}%"
                )
            )
        for gpu in gpus:
            if gpu.utilization_percent is not None and gpu.utilization_percent >= 90:
                facts.append(
                    DiagnosticFact(
                        code="gpu_utilization_high",
                        level="high",
                        summary=f"GPU {gpu.index}: {gpu.utilization_percent:.1f}%",
                    )
                )
            if gpu.temperature_celsius is not None and gpu.temperature_celsius >= 90:
                facts.append(
                    DiagnosticFact(
                        code="gpu_temperature_concerning",
                        level="concerning",
                        summary=f"GPU {gpu.index}: {gpu.temperature_celsius:.1f} °C",
                    )
                )
            elif gpu.temperature_celsius is not None and gpu.temperature_celsius >= 80:
                facts.append(
                    DiagnosticFact(
                        code="gpu_temperature_elevated",
                        level="elevated",
                        summary=f"GPU {gpu.index}: {gpu.temperature_celsius:.1f} °C",
                    )
                )
        return tuple(facts[:20])


class PsutilCounterProvider:
    def sample(self) -> RawCounters:
        unavailable: list[str] = []
        cpu = psutil.cpu_times()
        total = sum(float(value) for value in cpu)
        idle = float(cpu.idle) + float(getattr(cpu, "iowait", 0))
        memory = psutil.virtual_memory()
        disks: list[RawDisk] = []
        aggregate_io = None
        try:
            aggregate_io = psutil.disk_io_counters()
        except (OSError, RuntimeError):
            unavailable.append("disk_io")
        for partition in psutil.disk_partitions(all=False)[:8]:
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                disks.append(
                    RawDisk(
                        partition.device,
                        partition.mountpoint,
                        usage.total,
                        usage.used,
                        usage.free,
                        # psutil exposes reliable system-wide counters here. Keep the
                        # scope explicit rather than pretending they belong to C:.
                        aggregate_io.read_bytes if aggregate_io and not disks else None,
                        aggregate_io.write_bytes if aggregate_io and not disks else None,
                    )
                )
            except (OSError, PermissionError):
                continue
        networks = []
        try:
            networks = [
                RawNetwork(name, value.bytes_sent, value.bytes_recv)
                for name, value in list(psutil.net_io_counters(pernic=True).items())[:16]
            ]
        except (OSError, RuntimeError):
            unavailable.append("network")
        processes = []
        process_started = time.monotonic()
        for process in psutil.process_iter(
            ["pid", "name", "cpu_times", "memory_info", "io_counters"]
        ):
            if len(processes) >= 256 or time.monotonic() - process_started >= 0.25:
                break
            try:
                info = process.info
                times = info.get("cpu_times")
                mem = info.get("memory_info")
                io = info.get("io_counters")
                if times is None or mem is None:
                    continue
                processes.append(
                    RawProcess(
                        process.pid,
                        _safe_name(info.get("name"), f"PID {process.pid}"),
                        float(times.user + times.system),
                        int(mem.rss),
                        int(io.read_bytes) if io is not None else None,
                        int(io.write_bytes) if io is not None else None,
                    )
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
        return RawCounters(
            monotonic_at=time.monotonic(),
            captured_at=datetime.now(UTC),
            cpu_busy_seconds=max(0, total - idle),
            cpu_total_seconds=max(0, total),
            logical_processors=max(1, psutil.cpu_count(logical=True) or 1),
            memory=MemoryReading(memory.total, memory.available, memory.used),
            disks=tuple(disks),
            networks=tuple(networks),
            processes=tuple(processes),
            unavailable_sources=tuple(unavailable),
        )


class NvmlGpuProvider:
    def __init__(self) -> None:
        self._initialized = False
        self.unavailable_reason: str | None = None

    def sample(self) -> tuple[GpuReading, ...]:
        try:
            import pynvml

            if not self._initialized:
                pynvml.nvmlInit()
                self._initialized = True
            readings = []
            for index in range(min(8, pynvml.nvmlDeviceGetCount())):
                try:
                    readings.append(self._device(pynvml, index))
                except Exception:
                    self.unavailable_reason = "nvidia_gpu_partial"
            if not readings and self.unavailable_reason is None:
                self.unavailable_reason = "nvidia_gpu_not_found"
            elif readings and self.unavailable_reason is None:
                self.unavailable_reason = None
            return tuple(readings)
        except Exception:
            self.unavailable_reason = "nvml_unavailable"
            return ()

    @staticmethod
    def _device(nvml, index: int) -> GpuReading:
        handle = nvml.nvmlDeviceGetHandleByIndex(index)
        unavailable = []

        def metric(name, call, divisor=1):
            try:
                value = call()
                return float(value) / divisor
            except Exception:
                unavailable.append(name)
                return None

        memory = nvml.nvmlDeviceGetMemoryInfo(handle)
        utilization = metric("utilization", lambda: nvml.nvmlDeviceGetUtilizationRates(handle).gpu)
        temperature = metric(
            "temperature",
            lambda: nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU),
        )
        power = metric("power", lambda: nvml.nvmlDeviceGetPowerUsage(handle), 1000)
        power_limit = metric(
            "power_limit", lambda: nvml.nvmlDeviceGetEnforcedPowerLimit(handle), 1000
        )
        process_memory: dict[int, int] = {}
        for function_name in (
            "nvmlDeviceGetComputeRunningProcesses_v3",
            "nvmlDeviceGetGraphicsRunningProcesses_v3",
            "nvmlDeviceGetComputeRunningProcesses",
            "nvmlDeviceGetGraphicsRunningProcesses",
        ):
            function = getattr(nvml, function_name, None)
            if function is None:
                continue
            try:
                for process in function(handle):
                    used = getattr(process, "usedGpuMemory", None)
                    if isinstance(used, int) and 0 <= used < 2**63:
                        process_memory[process.pid] = max(process_memory.get(process.pid, 0), used)
            except Exception:
                continue
        name = nvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        return GpuReading(
            index=index,
            name=_safe_name(name, f"GPU {index}"),
            utilization_percent=utilization,
            memory_total_bytes=int(memory.total),
            memory_used_bytes=int(memory.used),
            temperature_celsius=temperature,
            power_watts=power,
            power_limit_watts=power_limit,
            process_memory=tuple(sorted(process_memory.items()))[:20],
            unavailable_metrics=tuple(unavailable + ["process_gpu_utilization"]),
        )

    def close(self) -> None:
        if not self._initialized:
            return
        try:
            import pynvml

            pynvml.nvmlShutdown()
        except Exception:
            pass
        self._initialized = False


class TelemetryHistory:
    def __init__(self, *, max_samples: int = 12, retention_seconds: float = 60) -> None:
        if not 2 <= max_samples <= 60 or not 5 <= retention_seconds <= 300:
            raise ValueError("Telemetry history limits are invalid")
        self._max = max_samples
        self._retention = retention_seconds
        self._items: deque[tuple[float, TelemetrySnapshot]] = deque(maxlen=max_samples)
        self._lock = threading.Lock()

    def add(self, snapshot: TelemetrySnapshot, *, now: float | None = None) -> None:
        with self._lock:
            self._items.append((time.monotonic() if now is None else now, snapshot))

    def snapshots(self, *, now: float | None = None) -> tuple[TelemetrySnapshot, ...]:
        current = time.monotonic() if now is None else now
        with self._lock:
            while self._items and current - self._items[0][0] > self._retention:
                self._items.popleft()
            return tuple(item[1] for item in self._items)


class TelemetryCancelled(RuntimeError):
    pass


class TelemetryService:
    def __init__(
        self,
        counters: CounterProvider | None = None,
        *,
        gpu_provider: GpuProvider | None = None,
        history: TelemetryHistory | None = None,
        sample_interval_seconds: float = 0.2,
    ) -> None:
        if not 0.05 <= sample_interval_seconds <= 2:
            raise ValueError("Telemetry sampling interval is outside safe bounds")
        self._counters = counters or PsutilCounterProvider()
        self._gpu = gpu_provider if gpu_provider is not None else NvmlGpuProvider()
        self.history = history or TelemetryHistory()
        self._interval = sample_interval_seconds
        self._normalizer = TelemetryNormalizer()
        self._active: set[threading.Event] = set()
        self._lock = threading.Condition()
        self._closed = False

    def capture(
        self,
        operation_id: UUID,
        *,
        active_process: tuple[int, str] | None = None,
        cancellation: threading.Event | None = None,
    ) -> TelemetrySnapshot:
        cancel = cancellation or threading.Event()
        with self._lock:
            if self._closed:
                raise TelemetryCancelled("Telemetry service is closed")
            self._active.add(cancel)
        started = time.monotonic()
        try:
            first = self._counters.sample()
            if cancel.wait(self._interval):
                raise TelemetryCancelled
            second = self._counters.sample()
            if cancel.is_set():
                raise TelemetryCancelled
            gpus = self._gpu.sample() if self._gpu is not None else ()
            if cancel.is_set():
                raise TelemetryCancelled
            gpu_reason = getattr(self._gpu, "unavailable_reason", None)
            if isinstance(gpu_reason, str) and gpu_reason:
                second = replace(
                    second,
                    unavailable_sources=tuple(
                        dict.fromkeys((*second.unavailable_sources, gpu_reason))
                    ),
                )
            result = self._normalizer.normalize(
                second,
                first,
                gpus,
                operation_id,
                active_process=active_process,
                total_ms=(time.monotonic() - started) * 1000,
            )
            self.history.add(result)
            return result
        finally:
            with self._lock:
                self._active.discard(cancel)
                self._lock.notify_all()

    def cancel(self) -> None:
        with self._lock:
            for item in self._active:
                item.set()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for item in self._active:
                item.set()
            deadline = time.monotonic() + 2
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._lock.wait(timeout=remaining)
        if self._gpu is not None:
            self._gpu.close()
