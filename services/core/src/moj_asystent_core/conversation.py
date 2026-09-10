"""Streaming local conversation orchestration for voice and typed chat."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from .llm import (
    ConversationContext,
    LanguageModelProvider,
    LanguageModelRequest,
    ModelStatus,
    ProviderProtocolError,
    ProviderUnavailableError,
)
from .protocol import ModelStatusChangedPayload
from .runtime import CoreRuntime


@dataclass(frozen=True)
class ConversationReply:
    text: str
    spoken_text: str


class LocalConversationService:
    def __init__(
        self,
        runtime: CoreRuntime,
        provider: LanguageModelProvider,
        context: ConversationContext | None = None,
    ) -> None:
        self._runtime = runtime
        self._provider = provider
        self._context = context or ConversationContext()
        self._lock = asyncio.Lock()

    @property
    def model(self) -> str:
        return self._provider.model

    async def refresh_status(self) -> ModelStatus:
        status = await self._provider.status()
        self._publish_status(status)
        return status

    async def respond(
        self, operation_id: UUID, user_text: str, *, mode: Literal["voice", "text"]
    ) -> ConversationReply:
        async with self._lock:
            status = await self.refresh_status()
            if status.state != "ready":
                raise ProviderUnavailableError(status.detail or "Local model is unavailable")
            self._publish_status(
                ModelStatus(provider="ollama", model=self.model, state="loading", detail=None)
            )
            self._runtime.publish_response_started(operation_id, model=self.model, mode=mode)
            chunks: list[str] = []
            try:
                request = LanguageModelRequest(messages=self._context.messages_for(user_text))
                async for chunk in self._provider.stream(request):
                    chunks.append(chunk)
                    self._runtime.publish_response_delta(operation_id, len(chunks) - 1, chunk)
                answer = "".join(chunks).strip()
                if not answer:
                    raise ProviderProtocolError("Local model returned an empty response")
                spoken = concise_spoken_response(answer)
                self._context.remember(user_text, answer)
                self._runtime.publish_response_completed(
                    operation_id,
                    answer,
                    spoken_text=spoken if mode == "voice" else None,
                    model=self.model,
                )
                self._publish_status(
                    ModelStatus(provider="ollama", model=self.model, state="ready", detail=None)
                )
                return ConversationReply(text=answer, spoken_text=spoken)
            except asyncio.CancelledError:
                self._publish_status(
                    ModelStatus(provider="ollama", model=self.model, state="ready", detail=None)
                )
                raise
            except Exception:
                self._publish_status(
                    ModelStatus(
                        provider="ollama",
                        model=self.model,
                        state="error",
                        detail="Lokalny model nie ukończył odpowiedzi.",
                    )
                )
                raise

    async def close(self) -> None:
        await self._provider.close()

    def _publish_status(self, status: ModelStatus) -> None:
        self._runtime.set_model_status(
            ModelStatusChangedPayload(
                provider=status.provider,
                model=status.model,
                status=status.state,
                detail=status.detail,
            )
        )


class TextChatController:
    """Owns at most one typed generation and cancels it during shutdown/replacement."""

    def __init__(self, runtime: CoreRuntime, conversation: LocalConversationService) -> None:
        self._runtime = runtime
        self._conversation = conversation
        self._task: asyncio.Task[None] | None = None
        self._operation_id: UUID | None = None

    async def start(self, text: str) -> UUID:
        await self.cancel()
        operation_id = uuid4()
        self._operation_id = operation_id
        if self._runtime.state == "error":
            self._runtime.transition("idle", expected_state="error")
        self._runtime.transition("thinking", expected_state="idle")
        self._task = asyncio.create_task(self._run(operation_id, text))
        return operation_id

    async def cancel(self) -> None:
        task = self._task
        self._task = None
        self._operation_id = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._runtime.state in {"thinking", "error"}:
            self._runtime.transition("idle", expected_state=self._runtime.state)

    async def shutdown(self) -> None:
        await self.cancel()

    async def _run(self, operation_id: UUID, text: str) -> None:
        try:
            await self._conversation.respond(operation_id, text, mode="text")
            if self._operation_id == operation_id and self._runtime.state == "thinking":
                self._runtime.transition("idle", expected_state="thinking")
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._operation_id == operation_id and self._runtime.state == "thinking":
                self._runtime.transition("error", expected_state="thinking")
        finally:
            if self._operation_id == operation_id:
                self._operation_id = None
            if self._task is asyncio.current_task():
                self._task = None


def concise_spoken_response(text: str, maximum: int = 360) -> str:
    normalized = " ".join(text.split())
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    selected = " ".join(sentences[:2]).strip()
    if len(selected) <= maximum:
        return selected
    shortened = selected[: maximum - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{shortened}…"
