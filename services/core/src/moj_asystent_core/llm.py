"""Local language-model boundary and bounded in-memory conversation context."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterator
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:4b"
SYSTEM_PROMPT = (
    "Jesteś lokalnym, prywatnym asystentem użytkownika. Odpowiadaj naturalnie i wyłącznie "
    "po polsku. Bądź konkretny, przyjazny i uczciwie zaznaczaj niepewność. Nie twierdź, "
    "że wykonałeś działanie w systemie — na tym etapie możesz tylko rozmawiać."
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderWireModel(BaseModel):
    # Ollama adds timing and implementation metadata across releases. We validate
    # every field we consume while deliberately ignoring unrelated provider fields.
    model_config = ConfigDict(extra="ignore", frozen=True)


class ChatMessage(FrozenModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=8_192)


class LanguageModelRequest(FrozenModel):
    messages: tuple[ChatMessage, ...] = Field(min_length=1, max_length=25)


class ModelStatus(FrozenModel):
    provider: Literal["ollama"] = "ollama"
    model: str = Field(min_length=1, max_length=128)
    state: Literal["unavailable", "missing", "loading", "ready", "error"]
    detail: str | None = Field(default=None, max_length=256)


class ProviderUnavailableError(RuntimeError):
    """The local model runtime cannot currently serve requests."""


class ProviderProtocolError(RuntimeError):
    """The local provider returned malformed or incomplete data."""


@runtime_checkable
class LanguageModelProvider(Protocol):
    @property
    def model(self) -> str: ...

    async def status(self) -> ModelStatus: ...

    def stream(self, request: LanguageModelRequest) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...


class _StreamMessage(ProviderWireModel):
    role: Literal["assistant"] = "assistant"
    content: str = Field(max_length=8_192)


class _StreamFrame(ProviderWireModel):
    message: _StreamMessage
    done: bool


class _TagsResponse(ProviderWireModel):
    models: list[dict[str, object]]


class OllamaLanguageModelProvider:
    """Validated streaming adapter for a loopback-only Ollama server."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _validate_loopback_origin(base_url)
        if not model or len(model) > 128:
            raise ValueError("Model name must contain between 1 and 128 characters")
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=120.0, write=10.0, pool=2.0),
            trust_env=False,
        )

    @property
    def model(self) -> str:
        return self._model

    async def status(self) -> ModelStatus:
        try:
            response = await self._client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            if len(response.content) > 1_048_576:
                raise ValueError("Ollama model list is too large")
            decoded = _TagsResponse.model_validate(response.json())
            names = {
                candidate
                for item in decoded.models
                for candidate in (item.get("name"), item.get("model"))
                if isinstance(candidate, str)
            }
            state = "ready" if self._model in names else "missing"
            detail = None if state == "ready" else "Model nie jest zainstalowany w Ollama."
            return ModelStatus(model=self._model, state=state, detail=detail)
        except (httpx.HTTPError, ValueError, ValidationError, json.JSONDecodeError):
            return ModelStatus(
                model=self._model,
                state="unavailable",
                detail="Nie można połączyć się z lokalnym Ollama.",
            )

    async def stream(self, request: LanguageModelRequest) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "messages": [message.model_dump() for message in request.messages],
            "stream": True,
            "think": False,
            "options": {"temperature": 0.3},
        }
        received_done = False
        total_characters = 0
        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if len(line.encode("utf-8")) > 32_768:
                        raise ProviderProtocolError("Ollama stream frame is too large")
                    try:
                        frame = _StreamFrame.model_validate_json(line)
                    except ValidationError as error:
                        raise ProviderProtocolError(
                            "Ollama returned an invalid stream frame"
                        ) from error
                    if frame.done:
                        received_done = True
                        break
                    if frame.message.content:
                        total_characters += len(frame.message.content)
                        if total_characters > 8_192:
                            raise ProviderProtocolError("Ollama response is too large")
                        yield frame.message.content
        except httpx.HTTPStatusError as error:
            raise ProviderUnavailableError("Ollama rejected the model request") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError("Ollama is unavailable") from error
        if not received_done:
            raise ProviderProtocolError("Ollama stream ended before completion")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class ConversationContext:
    """Small, process-local history; persistent memory belongs to Milestone 10."""

    def __init__(self, *, max_turns: int = 6, max_characters: int = 12_000) -> None:
        if not 1 <= max_turns <= 12 or not 64 <= max_characters <= 32_768:
            raise ValueError("Conversation limits are outside the supported bounds")
        self._max_turns = max_turns
        self._max_characters = max_characters
        self._turns: deque[tuple[ChatMessage, ChatMessage]] = deque()

    def remember(self, user_text: str, assistant_text: str) -> None:
        turn = (
            ChatMessage(role="user", content=user_text),
            ChatMessage(role="assistant", content=assistant_text),
        )
        self._turns.append(turn)
        while len(self._turns) > self._max_turns:
            self._turns.popleft()

    def messages_for(self, user_text: str) -> tuple[ChatMessage, ...]:
        pending = ChatMessage(role="user", content=user_text)
        selected: deque[tuple[ChatMessage, ChatMessage]] = deque()
        used = len(pending.content)
        for turn in reversed(self._turns):
            turn_size = sum(len(message.content) for message in turn)
            if used + turn_size > self._max_characters:
                break
            selected.appendleft(turn)
            used += turn_size
        history = tuple(message for turn in selected for message in turn)
        return (ChatMessage(role="system", content=SYSTEM_PROMPT), *history, pending)


def _validate_loopback_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise ValueError("Ollama URL must be an HTTP loopback origin with an explicit port")
    return value.rstrip("/")
