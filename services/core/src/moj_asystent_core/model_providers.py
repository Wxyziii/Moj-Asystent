"""Optional llama.cpp and OpenRouter adapters behind the common model interface."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from .llm import (
    LanguageModelRequest,
    ModelStatus,
    ModelStreamEvent,
    ModelTextDelta,
    ModelUsageEvent,
    ProviderProtocolError,
    ProviderUnavailableError,
)

DEFAULT_LLAMA_CPP_URL = "http://127.0.0.1:11435"
OPENROUTER_URL = "https://openrouter.ai/api/v1"


class OpenRouterAuthenticationError(ProviderUnavailableError):
    """OpenRouter rejected the configured credential."""


class OpenRouterRateLimitError(ProviderUnavailableError):
    """OpenRouter rate-limited the current request."""


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _CloudModel(_WireModel):
    id: str = Field(min_length=1, max_length=256)


class _CloudCatalog(_WireModel):
    data: tuple[_CloudModel, ...] = Field(max_length=10_000)


class _Delta(_WireModel):
    content: str | None = Field(default=None, max_length=8_192)


class _Choice(_WireModel):
    delta: _Delta
    finish_reason: str | None = Field(default=None, max_length=64)


class _Usage(_WireModel):
    prompt_tokens: StrictInt = Field(ge=0, le=10_000_000)
    completion_tokens: StrictInt = Field(ge=0, le=10_000_000)
    total_tokens: StrictInt = Field(ge=0, le=20_000_000)
    cost: float | None = Field(default=None, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_total(self) -> _Usage:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("Provider usage total is inconsistent")
        return self


class _CompletionFrame(_WireModel):
    choices: tuple[_Choice, ...] = Field(default=(), max_length=8)
    usage: _Usage | None = None

    @model_validator(mode="after")
    def validate_content(self) -> _CompletionFrame:
        if not self.choices and self.usage is None:
            raise ValueError("Completion frame requires a choice or usage")
        return self


class OpenRouterLanguageModelProvider:
    """Opt-in, allowlisted HTTPS provider; the secret never crosses into React."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = OPENROUTER_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _validate_openrouter_origin(base_url)
        self._model = _validate_model_id(model)
        checked_key = api_key.strip() if api_key else None
        if checked_key is not None and not 16 <= len(checked_key) <= 512:
            raise ValueError("OpenRouter API key length is invalid")
        self._api_key = checked_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=90, write=10, pool=2),
            trust_env=False,
            follow_redirects=False,
        )

    @property
    def model(self) -> str:
        return self._model

    async def status(self) -> ModelStatus:
        if self._api_key is None:
            return ModelStatus(
                provider="openrouter",
                model=self._model,
                state="unavailable",
                detail="Brak klucza OpenRouter w bezpiecznym środowisku rdzenia.",
            )
        try:
            response = await self._client.get(f"{self._base_url}/models", headers=self._headers())
            _raise_cloud_status(response)
            if len(response.content) > 2_000_000:
                raise ProviderProtocolError("OpenRouter model catalog is too large")
            catalog = _CloudCatalog.model_validate(response.json())
        except OpenRouterAuthenticationError:
            return ModelStatus(
                provider="openrouter",
                model=self._model,
                state="unavailable",
                detail="Klucz OpenRouter został odrzucony.",
            )
        except OpenRouterRateLimitError:
            return ModelStatus(
                provider="openrouter",
                model=self._model,
                state="unavailable",
                detail="OpenRouter chwilowo ogranicza liczbę zapytań.",
            )
        except (httpx.HTTPError, ValueError, ValidationError, ProviderProtocolError):
            return ModelStatus(
                provider="openrouter",
                model=self._model,
                state="unavailable",
                detail="Nie można zweryfikować modelu OpenRouter.",
            )
        if any(item.id == self._model for item in catalog.data):
            return ModelStatus(provider="openrouter", model=self._model, state="ready", detail=None)
        return ModelStatus(
            provider="openrouter",
            model=self._model,
            state="missing",
            detail="Skonfigurowany model OpenRouter nie jest już dostępny.",
        )

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelStreamEvent]:
        if self._api_key is None:
            raise OpenRouterAuthenticationError("OpenRouter API key is unavailable")
        if request.images or request.tools:
            raise ProviderProtocolError(
                "This OpenRouter configuration accepts normal text requests only"
            )
        payload = {
            "model": self._model,
            "messages": _plain_messages(request),
            "stream": True,
            "max_tokens": 2_048,
            "temperature": 0.3,
        }
        async for event in _stream_openai(
            self._client,
            f"{self._base_url}/chat/completions",
            payload,
            headers=self._headers(),
            cloud=True,
        ):
            yield event

    async def close(self) -> None:
        self._api_key = None
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if self._api_key is None:
            return {}
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


class LlamaCppLanguageModelProvider:
    """Demand-loaded loopback llama-server for an explicitly configured GGUF."""

    def __init__(
        self,
        *,
        model: str,
        model_path: Path | None = None,
        executable_path: Path | None = None,
        base_url: str = DEFAULT_LLAMA_CPP_URL,
        gpu_layers: int = 20,
        context_size: int = 16_384,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = _validate_model_id(model)
        self._base_url = _validate_loopback_origin(base_url, "llama.cpp")
        self._model_path = _validate_optional_file(model_path, suffix=".gguf")
        self._executable_path = _validate_optional_file(executable_path, suffix=".exe")
        if not 0 <= gpu_layers <= 256:
            raise ValueError("llama.cpp GPU layer count is outside safe bounds")
        if not 2_048 <= context_size <= 65_536:
            raise ValueError("llama.cpp context size is outside safe bounds")
        self._gpu_layers = gpu_layers
        self._context_size = context_size
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2, read=300, write=10, pool=2),
            trust_env=False,
            follow_redirects=False,
        )
        self._process: asyncio.subprocess.Process | None = None
        self._process_lock = asyncio.Lock()

    @property
    def model(self) -> str:
        return self._model

    async def status(self) -> ModelStatus:
        if self._model_path is None or not self._model_path.is_file():
            return ModelStatus(
                provider="llama_cpp",
                model=self._model,
                state="missing",
                detail="Nie skonfigurowano istniejącego pliku modelu GGUF.",
            )
        try:
            response = await self._client.get(f"{self._base_url}/health")
            if response.status_code == 200:
                return ModelStatus(
                    provider="llama_cpp", model=self._model, state="ready", detail=None
                )
            if response.status_code == 503:
                return ModelStatus(
                    provider="llama_cpp",
                    model=self._model,
                    state="loading",
                    detail="Model Deep jest ładowany przez llama.cpp.",
                )
        except httpx.HTTPError:
            if self._executable_path is not None and self._executable_path.is_file():
                return await self._start_owned_server()
        return ModelStatus(
            provider="llama_cpp",
            model=self._model,
            state="unavailable",
            detail="Lokalny serwer llama.cpp jest niedostępny.",
        )

    async def stream_turn(self, request: LanguageModelRequest) -> AsyncIterator[ModelStreamEvent]:
        if request.images or request.tools:
            raise ProviderProtocolError(
                "Configured llama.cpp deep tier does not accept image or tool requests"
            )
        payload = {
            "model": self._model,
            "messages": _plain_messages(request),
            "stream": True,
            "max_tokens": 2_048,
            "temperature": 0.3,
        }
        async for event in _stream_openai(
            self._client,
            f"{self._base_url}/v1/chat/completions",
            payload,
            headers={"Content-Type": "application/json"},
            cloud=False,
        ):
            yield event

    async def unload(self) -> None:
        async with self._process_lock:
            process = self._process
            self._process = None
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    async def close(self) -> None:
        await self.unload()
        if self._owns_client:
            await self._client.aclose()

    async def _start_owned_server(self) -> ModelStatus:
        async with self._process_lock:
            if self._process is not None and self._process.returncode is None:
                return ModelStatus(
                    provider="llama_cpp",
                    model=self._model,
                    state="loading",
                    detail="Model Deep jest ładowany przez llama.cpp.",
                )
            assert self._executable_path is not None
            assert self._model_path is not None
            port = urlsplit(self._base_url).port
            assert port is not None
            try:
                self._process = await asyncio.create_subprocess_exec(
                    str(self._executable_path),
                    "--model",
                    str(self._model_path),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--ctx-size",
                    str(self._context_size),
                    "--n-gpu-layers",
                    str(self._gpu_layers),
                    "--jinja",
                    "--no-webui",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError:
                return ModelStatus(
                    provider="llama_cpp",
                    model=self._model,
                    state="error",
                    detail="Nie udało się uruchomić llama.cpp.",
                )
        return ModelStatus(
            provider="llama_cpp",
            model=self._model,
            state="loading",
            detail="Model Deep jest uruchamiany. Spróbuj ponownie za chwilę.",
        )


async def _stream_openai(
    client: httpx.AsyncClient,
    url: str,
    payload: Mapping[str, object],
    *,
    headers: dict[str, str],
    cloud: bool,
) -> AsyncIterator[ModelStreamEvent]:
    received_finish = False
    total_characters = 0
    try:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if cloud:
                _raise_cloud_status(response)
            else:
                response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or line.startswith(":"):
                    continue
                if len(line.encode("utf-8")) > 32_768:
                    raise ProviderProtocolError("Provider stream frame is too large")
                if not line.startswith("data: "):
                    raise ProviderProtocolError("Provider returned an invalid SSE frame")
                data = line[6:]
                if data == "[DONE]":
                    received_finish = True
                    break
                try:
                    frame = _CompletionFrame.model_validate_json(data)
                except ValidationError as error:
                    raise ProviderProtocolError("Provider returned malformed JSON") from error
                if frame.choices:
                    choice = frame.choices[0]
                    if choice.delta.content:
                        total_characters += len(choice.delta.content)
                        if total_characters > 8_192:
                            raise ProviderProtocolError("Provider response is too large")
                        yield ModelTextDelta(text=choice.delta.content)
                    if choice.finish_reason is not None:
                        received_finish = True
                if frame.usage is not None:
                    yield ModelUsageEvent(
                        input_tokens=frame.usage.prompt_tokens,
                        output_tokens=frame.usage.completion_tokens,
                        total_tokens=frame.usage.total_tokens,
                        provider_cost=frame.usage.cost,
                        cost_unit="openrouter_credits" if frame.usage.cost is not None else None,
                    )
                if received_finish:
                    break
    except (OpenRouterAuthenticationError, OpenRouterRateLimitError):
        raise
    except httpx.HTTPStatusError as error:
        raise ProviderUnavailableError("Model provider rejected the request") from error
    except httpx.HTTPError as error:
        raise ProviderUnavailableError("Model provider is unavailable") from error
    if not received_finish:
        raise ProviderProtocolError("Provider stream ended before completion")


def _raise_cloud_status(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise OpenRouterAuthenticationError("OpenRouter authentication failed")
    if response.status_code == 429:
        raise OpenRouterRateLimitError("OpenRouter rate limit reached")
    response.raise_for_status()


def _plain_messages(request: LanguageModelRequest) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    total = 0
    for message in request.messages:
        if message.tool_calls or message.tool_name is not None:
            raise ProviderProtocolError("Plain provider request contains tool state")
        total += len(message.content)
        if total > 32_768:
            raise ProviderProtocolError("Provider prompt is too large")
        messages.append({"role": message.role, "content": message.content})
    return messages


def _validate_openrouter_origin(value: str) -> str:
    if value.rstrip("/") != OPENROUTER_URL:
        raise ValueError("OpenRouter URL must use the allowlisted official HTTPS endpoint")
    return OPENROUTER_URL


def _validate_loopback_origin(value: str, label: str) -> str:
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
        raise ValueError(f"{label} URL must be an HTTP loopback origin with an explicit port")
    return value.rstrip("/")


def _validate_model_id(value: str) -> str:
    checked = value.strip()
    if not checked or len(checked) > 128 or any(ord(character) < 32 for character in checked):
        raise ValueError("Model identifier is invalid")
    return checked


def _validate_optional_file(value: Path | None, *, suffix: Literal[".gguf", ".exe"]) -> Path | None:
    if value is None:
        return None
    resolved = value.expanduser().resolve(strict=False)
    if resolved.suffix.casefold() != suffix:
        raise ValueError(f"Configured path must point to a {suffix} file")
    return resolved
