"""Authenticated entry point for the local core service."""

import os
from pathlib import Path

import uvicorn

from .api import CoreSettings, create_app
from .audio import AudioConfig
from .auth import SessionCredential
from .context import ContextSettings
from .vision import VisionSettings


def create_server(settings: CoreSettings) -> uvicorn.Server:
    app = create_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            ws="websockets-sansio",
            ws_max_size=32_768,
            ws_max_queue=16,
            timeout_graceful_shutdown=5,
            access_log=False,
        )
    )
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    return server


def main() -> None:
    raw_credential = os.environ.pop("MOJ_ASYSTENT_SESSION_CREDENTIAL", None)
    raw_action_credential = os.environ.pop("MOJ_ASYSTENT_ACTION_CREDENTIAL", None)
    raw_openrouter_key = os.environ.pop("MOJ_ASYSTENT_OPENROUTER_API_KEY", None)
    if raw_credential is None:
        raise RuntimeError("Core requires a per-launch session credential")
    if raw_action_credential is None:
        raise RuntimeError("Core requires a separate per-launch action credential")
    create_server(
        CoreSettings(
            credential=SessionCredential.from_value(raw_credential),
            action_credential=SessionCredential.from_value(raw_action_credential),
            audio=AudioConfig.from_environment(),
            audio_enabled=True,
            ollama_url=os.environ.get("MOJ_ASYSTENT_OLLAMA_URL", "http://127.0.0.1:11434"),
            llm_model=os.environ.get("MOJ_ASYSTENT_LLM_MODEL", "qwen3.5:4b"),
            quality_model=os.environ.get("MOJ_ASYSTENT_QUALITY_MODEL", "qwen3.5:9b"),
            llama_cpp_url=os.environ.get("MOJ_ASYSTENT_LLAMA_CPP_URL", "http://127.0.0.1:11435"),
            llama_cpp_model=os.environ.get("MOJ_ASYSTENT_LLAMA_CPP_MODEL", "Qwen3.5 27B GGUF"),
            llama_cpp_model_path=_optional_path("MOJ_ASYSTENT_LLAMA_CPP_MODEL_PATH"),
            llama_cpp_executable_path=_optional_path("MOJ_ASYSTENT_LLAMA_CPP_EXECUTABLE"),
            llama_cpp_gpu_layers=_bounded_int(
                "MOJ_ASYSTENT_LLAMA_CPP_GPU_LAYERS", 20, minimum=0, maximum=256
            ),
            llama_cpp_context_size=_bounded_int(
                "MOJ_ASYSTENT_LLAMA_CPP_CONTEXT",
                16_384,
                minimum=2_048,
                maximum=65_536,
            ),
            openrouter_model=os.environ.get("MOJ_ASYSTENT_OPENROUTER_MODEL") or None,
            openrouter_api_key=raw_openrouter_key,
            high_load_processes=tuple(
                item.strip()
                for item in os.environ.get("MOJ_ASYSTENT_HIGH_LOAD_PROCESSES", "").split(",")
                if item.strip()
            ),
            context=ContextSettings.from_environment(),
            vision=VisionSettings.from_environment(),
        )
    ).run()


def _optional_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} is outside supported bounds")
    return value
