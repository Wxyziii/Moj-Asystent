# System diagnostics

Milestone 9 adds request-driven, local-only diagnostics through the existing
typed `get_system_stats` tool. The desktop never polls operating-system APIs;
the core collects a bounded snapshot only for an authorized read request.

Each snapshot carries IDs, a UTC timestamp and collection timings. It includes
CPU, RAM, mounted-volume capacity, network counters, optional NVIDIA NVML
utilization/VRAM/temperature/power, top processes and GPU process VRAM
correlation. Deterministic facts flag high CPU/RAM/GPU load and elevated
temperature. Missing sources and metrics remain explicit.

Disk capacity is per volume. Disk throughput is marked with a scope so a
system-wide counter is never presented as a particular volume's activity.
Rates are calculated from two samples; the first sample and counter resets are
truthfully reported as unavailable.

The service uses `psutil` and an optional NVML adapter behind provider
interfaces. Captures are cancellable, process enumeration has a short time
budget, names are control-character sanitized, and all collections are capped.
The in-memory history contains at most twelve
snapshots and expires entries after sixty seconds; it is not a database and is
lost on shutdown. No command lines, executable paths, audio or screenshots are
returned. Telemetry and process names are untrusted observations and cannot
authorize tools or be treated as instructions.
