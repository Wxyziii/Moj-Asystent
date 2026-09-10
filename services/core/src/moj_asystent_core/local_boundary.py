"""Reject foreign Host headers on both HTTP and WebSocket requests."""

import re

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class LocalHostMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            hosts = Headers(scope=scope).getlist("host")
            if len(hosts) != 1 or not re.fullmatch(
                r"(127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?", hosts[0], re.IGNORECASE
            ):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await PlainTextResponse("Invalid host", status_code=400)(scope, receive, send)
                return
        await self.app(scope, receive, send)
