import asyncio
from uuid import uuid4

import pytest

from moj_asystent_core.conversation import LocalConversationService, concise_spoken_response
from moj_asystent_core.llm import LanguageModelRequest, ModelStatus, ModelTextDelta
from moj_asystent_core.runtime import CoreRuntime


class FakeLanguageModel:
    model = "qwen3.5:4b"

    def __init__(self) -> None:
        self.requests: list[LanguageModelRequest] = []
        self.chunks = ["Pierwsze zdanie. ", "Drugie zdanie. Trzecie zdanie."]
        self.closed = False

    async def status(self) -> ModelStatus:
        return ModelStatus(model=self.model, state="ready")

    async def stream_turn(self, request: LanguageModelRequest):
        self.requests.append(request)
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield ModelTextDelta(text=chunk)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_conversation_streams_ordered_events_and_commits_context() -> None:
    runtime = CoreRuntime()
    provider = FakeLanguageModel()
    service = LocalConversationService(runtime, provider)
    queue = runtime.subscribe(uuid4())
    queue.get_nowait()
    queue.get_nowait()
    operation_id = uuid4()

    reply = await service.respond(operation_id, "Co słychać?", mode="voice")
    events = []
    while not queue.empty():
        event = queue.get_nowait()
        assert event is not None
        events.append(event)

    assert [event.type for event in events] == [
        "model.status.changed",
        "model.status.changed",
        "assistant.response.started",
        "assistant.response.delta",
        "assistant.response.delta",
        "assistant.response.completed",
        "model.status.changed",
    ]
    assert reply.text == "Pierwsze zdanie. Drugie zdanie. Trzecie zdanie."
    assert reply.spoken_text == "Pierwsze zdanie. Drugie zdanie."
    assert provider.requests[0].messages[0].role == "system"
    assert "po polsku" in provider.requests[0].messages[0].content

    provider.chunks = ["Dobrze."]
    await service.respond(uuid4(), "A wcześniej?", mode="text")
    roles = [message.role for message in provider.requests[1].messages]
    assert roles == ["system", "user", "assistant", "user"]
    await service.close()
    assert provider.closed
    await runtime.shutdown()


def test_voice_summary_is_short_without_cutting_inside_a_word() -> None:
    text = "Bardzo długie zdanie " * 30
    spoken = concise_spoken_response(text, maximum=80)
    assert len(spoken) <= 80
    assert spoken.endswith("…")
    assert not spoken.endswith(" …")


class CancellableLanguageModel(FakeLanguageModel):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def stream_turn(self, request: LanguageModelRequest):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield ModelTextDelta(text="niedokończona część")
            await self.release.wait()
        else:
            yield ModelTextDelta(text="Nowa odpowiedź.")


@pytest.mark.asyncio
async def test_cancelled_generation_is_not_remembered() -> None:
    runtime = CoreRuntime()
    provider = CancellableLanguageModel()
    service = LocalConversationService(runtime, provider)
    task = asyncio.create_task(service.respond(uuid4(), "porzucone pytanie", mode="text"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await service.respond(uuid4(), "nowe pytanie", mode="text")
    contents = [message.content for message in provider.requests[1].messages]
    assert "porzucone pytanie" not in contents
    assert "niedokończona część" not in contents
    await service.close()
    await runtime.shutdown()
