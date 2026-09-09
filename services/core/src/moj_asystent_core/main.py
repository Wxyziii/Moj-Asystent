"""Development entry point for the local core service."""

import uvicorn

from .api import CoreSettings, create_app


def main() -> None:
    settings = CoreSettings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
