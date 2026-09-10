import asyncio
import threading
from collections.abc import AsyncIterator

import pytest

from moj_asystent_core.audio import (
    AudioConfig,
    AudioPipeline,
    PcmFrame,
    SpeechBoundary,
    SpeechBoundaryDetector,
    normalize_pcm,
)
from moj_asystent_core.audio_providers import ThreadSafeAudioIngress
from moj_asystent_core.providers import (
    SpeechToTextRequest,
    SpeechToTextResponse,
    TextToSpeechRequest,
    TextToSpeechResponse,
)
from moj_asystent_core.runtime import CoreRuntime


def frame(milliseconds: int = 80) -> PcmFrame:
    return PcmFrame(pcm_s16le=b"\0\0" * (16_000 * milliseconds // 1_000), sample_rate=16_000)


class FakeWake:
    def __init__(self, scores: list[float] | None = None) -> None:
        self.scores = iter(scores or [])
        self.calls = 0

    async def score(self, audio: PcmFrame) -> float:
        self.calls += 1
        return next(self.scores, 0.0)

    def reset(self) -> None:
        pass


class FakeVad:
    def __init__(self, scores: list[float]) -> None:
        self.scores = iter(scores)

    async def probability(self, audio: PcmFrame) -> float:
        return next(self.scores, 0.0)

    def reset(self) -> None:
        pass


class FakeStt:
    def __init__(self, transcript: str = "Włącz światło") -> None:
        self.transcript = transcript
        self.requests: list[SpeechToTextRequest] = []

    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        self.requests.append(request)
        return SpeechToTextResponse(
            transcript=self.transcript,
            language="pl",
            duration_ms=request.duration_ms,
            segments=(),
        )

    async def cancel(self) -> None:
        pass


class FakeTts:
    def __init__(self) -> None:
        self.requests: list[TextToSpeechRequest] = []

    async def speak(self, request: TextToSpeechRequest) -> TextToSpeechResponse:
        self.requests.append(request)
        return TextToSpeechResponse(duration_ms=250)

    async def cancel(self) -> None:
        pass


def config(**updates: object) -> AudioConfig:
    values: dict[str, object] = dict(
        frame_duration_ms=80,
        min_speech_ms=80,
        trailing_silence_ms=160,
        follow_up_timeout_seconds=0.05,
        error_recovery_seconds=0.01,
    )
    values.update(updates)
    return AudioConfig.model_validate(values)


@pytest.mark.asyncio
async def test_wake_detection_enters_listening() -> None:
    runtime = CoreRuntime()
    pipeline = AudioPipeline(runtime, config(), FakeWake([0.9]), FakeVad([]), FakeStt(), FakeTts())

    await pipeline.handle_frame(frame())
    assert runtime.health_payload().assistant_state == "listening"
    await pipeline.shutdown()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_rapid_wake_frames_do_not_reactivate_an_active_session() -> None:
    runtime = CoreRuntime()
    wake = FakeWake([0.9, 0.9, 0.9])
    pipeline = AudioPipeline(runtime, config(), wake, FakeVad([0, 0]), FakeStt(), FakeTts())
    for _ in range(3):
        await pipeline.handle_frame(frame())
    assert runtime.state == "listening"
    assert wake.calls == 1
    await pipeline.cancel()
    assert runtime.state == "idle"
    await pipeline.shutdown()
    await runtime.shutdown()


class WorkerThreadSource:
    def __init__(self, ingress: ThreadSafeAudioIngress) -> None:
        self.ingress = ingress

    async def frames(self) -> AsyncIterator[PcmFrame]:
        while (item := await self.ingress.next()) is not None:
            yield item

    async def close(self) -> None:
        self.ingress.close()


@pytest.mark.asyncio
async def test_worker_thread_audio_is_scheduled_on_runtime_event_loop() -> None:
    runtime = CoreRuntime()
    ingress = ThreadSafeAudioIngress(asyncio.get_running_loop())
    pipeline = AudioPipeline(
        runtime,
        config(),
        FakeWake([0.9]),
        FakeVad([]),
        FakeStt(),
        FakeTts(),
        WorkerThreadSource(ingress),
    )
    await pipeline.start()
    worker = threading.Thread(target=ingress.push_from_thread, args=(frame(),))
    worker.start()
    worker.join()
    async with asyncio.timeout(1):
        while runtime.state != "listening":
            await asyncio.sleep(0)
    await pipeline.shutdown()
    await runtime.shutdown()


def test_vad_detects_speech_start_end_and_maximum_duration() -> None:
    detector = SpeechBoundaryDetector(config(maximum_utterance_seconds=1))
    assert detector.push(0.8, frame()) is SpeechBoundary.STARTED
    assert detector.push(0.8, frame()) is SpeechBoundary.NONE
    assert detector.push(0.1, frame()) is SpeechBoundary.NONE
    assert detector.push(0.1, frame()) is SpeechBoundary.COMPLETED

    detector = SpeechBoundaryDetector(config(maximum_utterance_seconds=0.24))
    assert detector.push(0.8, frame()) is SpeechBoundary.STARTED
    assert detector.push(0.8, frame()) is SpeechBoundary.NONE
    assert detector.push(0.8, frame()) is SpeechBoundary.COMPLETED


def test_audio_input_is_normalized_to_engine_sample_rate() -> None:
    source = PcmFrame(pcm_s16le=b"\0\0" * 640, sample_rate=8_000)
    normalized = normalize_pcm(source)
    assert normalized.sample_rate == 16_000
    assert normalized.duration_ms == pytest.approx(source.duration_ms, abs=0.1)


@pytest.mark.asyncio
async def test_complete_polish_voice_cycle_and_follow_up() -> None:
    runtime = CoreRuntime()
    stt, tts = FakeStt(), FakeTts()
    pipeline = AudioPipeline(
        runtime,
        config(follow_up_timeout_seconds=1),
        FakeWake([0.9]),
        FakeVad([0.8, 0.1, 0.1, 0.8]),
        stt,
        tts,
    )
    await pipeline.handle_frame(frame())
    await pipeline.handle_frame(frame())
    await pipeline.handle_frame(frame())
    await pipeline.handle_frame(frame())
    await pipeline.wait_current_operation()
    assert runtime.health_payload().assistant_state == "follow_up"
    assert stt.requests[0].language == "pl"
    assert stt.requests[0].pcm_s16le
    assert tts.requests[0].text.startswith("Usłyszałem:")

    await pipeline.handle_frame(frame())
    assert runtime.health_payload().assistant_state == "listening"
    await pipeline.shutdown()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_follow_up_timeout_returns_to_idle() -> None:
    runtime = CoreRuntime()
    pipeline = AudioPipeline(
        runtime,
        config(),
        FakeWake(),
        FakeVad([0.8, 0.1, 0.1]),
        FakeStt(),
        FakeTts(),
    )
    await pipeline.manual_listen()
    for _ in range(3):
        await pipeline.handle_frame(frame())
    await pipeline.wait_current_operation()
    await asyncio.sleep(0.08)
    assert runtime.health_payload().assistant_state == "idle"
    await pipeline.shutdown()
    await runtime.shutdown()


class BlockingStt(FakeStt):
    def __init__(self, swallow_cancellation: bool = False) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.swallow_cancellation = swallow_cancellation

    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            if not self.swallow_cancellation:
                raise
            await self.release.wait()
        return await super().transcribe(request)


class FailingStt(FakeStt):
    async def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResponse:
        raise RuntimeError("private provider detail")


@pytest.mark.asyncio
async def test_provider_exception_uses_safe_error_recovery() -> None:
    runtime = CoreRuntime()
    pipeline = AudioPipeline(
        runtime,
        config(),
        FakeWake(),
        FakeVad([0.8, 0.1, 0.1]),
        FailingStt(),
        FakeTts(),
    )
    await pipeline.manual_listen()
    for _ in range(3):
        await pipeline.handle_frame(frame())
    await pipeline.wait_current_operation()
    assert runtime.state == "idle"
    assert pipeline.last_error_code == "provider_unavailable"
    await pipeline.shutdown()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_cancellation_and_stale_stt_result_cannot_change_new_session() -> None:
    runtime = CoreRuntime()
    stt = BlockingStt(swallow_cancellation=True)
    pipeline = AudioPipeline(
        runtime,
        config(),
        FakeWake(),
        FakeVad([0.8, 0.1, 0.1]),
        stt,
        FakeTts(),
    )
    await pipeline.manual_listen()
    for _ in range(3):
        await pipeline.handle_frame(frame())
    await stt.started.wait()
    cancel = asyncio.create_task(pipeline.cancel())
    await asyncio.sleep(0)
    stt.release.set()
    await cancel
    assert runtime.health_payload().assistant_state == "idle"
    assert pipeline.current_operation_id is None
    await pipeline.shutdown()
    await runtime.shutdown()


class BlockingTts(FakeTts):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def speak(self, request: TextToSpeechRequest) -> TextToSpeechResponse:
        self.started.set()
        await self.release.wait()
        return await super().speak(request)


class FollowUpRaceVad(FakeVad):
    def __init__(self) -> None:
        super().__init__([0.8, 0.1, 0.1])
        self.follow_up_started = asyncio.Event()
        self.release_follow_up = asyncio.Event()

    async def probability(self, audio: PcmFrame) -> float:
        try:
            return next(self.scores)
        except StopIteration:
            self.follow_up_started.set()
            await self.release_follow_up.wait()
            return 0.9


@pytest.mark.asyncio
async def test_follow_up_timeout_wins_cleanly_over_a_late_vad_result() -> None:
    runtime = CoreRuntime()
    vad = FollowUpRaceVad()
    pipeline = AudioPipeline(
        runtime,
        config(follow_up_timeout_seconds=0.01),
        FakeWake(),
        vad,
        FakeStt(),
        FakeTts(),
    )
    await pipeline.manual_listen()
    for _ in range(3):
        await pipeline.handle_frame(frame())
    await pipeline.wait_current_operation()
    late_frame = asyncio.create_task(pipeline.handle_frame(frame()))
    await vad.follow_up_started.wait()
    await asyncio.sleep(0.02)
    assert runtime.state == "idle"
    vad.release_follow_up.set()
    await late_frame
    assert runtime.state == "idle"
    await pipeline.shutdown()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_tts_playback_suppresses_wake_feedback_and_is_cancellable() -> None:
    runtime = CoreRuntime()
    wake, tts = FakeWake([0.9]), BlockingTts()
    pipeline = AudioPipeline(
        runtime,
        config(),
        wake,
        FakeVad([0.8, 0.1, 0.1]),
        FakeStt(),
        tts,
    )
    await pipeline.manual_listen()
    for _ in range(3):
        await pipeline.handle_frame(frame())
    await tts.started.wait()
    assert runtime.health_payload().assistant_state == "speaking"
    await pipeline.handle_frame(frame())
    assert wake.calls == 0
    await pipeline.cancel()
    assert runtime.health_payload().assistant_state == "idle"
    await pipeline.shutdown()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_shutdown_during_tts_cancels_playback() -> None:
    runtime = CoreRuntime()
    tts = BlockingTts()
    pipeline = AudioPipeline(
        runtime,
        config(),
        FakeWake(),
        FakeVad([0.8, 0.1, 0.1]),
        FakeStt(),
        tts,
    )
    await pipeline.manual_listen()
    for _ in range(3):
        await pipeline.handle_frame(frame())
    await tts.started.wait()
    await pipeline.shutdown()
    assert pipeline.closed
    await runtime.shutdown()


class FailingSource:
    async def frames(self) -> AsyncIterator[PcmFrame]:
        raise OSError("microphone disappeared")
        yield frame()

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_microphone_failure_recovers_without_stuck_state() -> None:
    runtime = CoreRuntime()
    pipeline = AudioPipeline(
        runtime, config(), FakeWake(), FakeVad([]), FakeStt(), FakeTts(), FailingSource()
    )
    await pipeline.start()
    await asyncio.sleep(0.03)
    assert runtime.health_payload().assistant_state == "idle"
    assert pipeline.last_error_code == "microphone_unavailable"
    await pipeline.shutdown()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_active_capture_and_processing() -> None:
    runtime = CoreRuntime()
    stt = BlockingStt()
    pipeline = AudioPipeline(
        runtime,
        config(),
        FakeWake(),
        FakeVad([0.8, 0.1, 0.1]),
        stt,
        FakeTts(),
    )
    await pipeline.manual_listen()
    for _ in range(3):
        await pipeline.handle_frame(frame())
    await stt.started.wait()
    await pipeline.shutdown()
    assert pipeline.closed
    await runtime.shutdown()
