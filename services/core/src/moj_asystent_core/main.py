"""Authenticated entry point for the local core service."""

import os

import uvicorn

from .api import CoreSettings, create_app
from .audio import AudioConfig
from .auth import SessionCredential


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
    raw_credential = os.environ.get("MOJ_ASYSTENT_SESSION_CREDENTIAL")
    if raw_credential is None:
        raise RuntimeError("Core requires a per-launch session credential")
    create_server(
        CoreSettings(
            credential=SessionCredential.from_value(raw_credential),
            audio=AudioConfig.from_environment(),
            audio_enabled=True,
            ollama_url=os.environ.get("MOJ_ASYSTENT_OLLAMA_URL", "http://127.0.0.1:11434"),
            llm_model=os.environ.get("MOJ_ASYSTENT_LLM_MODEL", "qwen3.5:4b"),
        )
    ).run()
