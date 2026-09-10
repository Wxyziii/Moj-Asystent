"""Development entry point for the local core service."""

import uvicorn

from .api import CoreSettings, create_app


def create_server(settings: CoreSettings) -> uvicorn.Server:
    return uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            ws="websockets-sansio",
            ws_max_size=32_768,
            ws_max_queue=16,
            timeout_graceful_shutdown=5,
            access_log=False,
        )
    )


def main() -> None:
    create_server(CoreSettings()).run()
