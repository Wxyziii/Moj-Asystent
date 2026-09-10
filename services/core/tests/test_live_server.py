"""Real TCP/WebSocket regression tests using the production Uvicorn configuration."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Origin, Subprotocol

from moj_asystent_core.api import CoreSettings
from moj_asystent_core.auth import SessionCredential
from moj_asystent_core.main import create_server
from moj_asystent_core.protocol import AssistantStateChanged, parse_event

TOKEN = "A" * 43
PROTOCOLS = (Subprotocol("moj-asystent.v1"), Subprotocol(f"credential.{TOKEN}"))


def hello() -> dict:
    return {
        "protocol_version": "1.1",
        "event_id": str(uuid4()),
        "occurred_at": "2026-09-09T20:00:00Z",
        "correlation_id": None,
        "type": "client.hello",
        "payload": {"client_id": "integration", "protocol_version": "1.1"},
    }


@asynccontextmanager
async def running_server(port: int = 0):
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    actual_port = listener.getsockname()[1]
    server = create_server(
        CoreSettings(port=actual_port, credential=SessionCredential.from_value(TOKEN))
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("Server failed to start")
                await asyncio.sleep(0.01)
        yield server, actual_port, task
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 8)
        listener.close()


@pytest.mark.asyncio
async def test_real_websocket_broadcast_disconnect_shutdown_and_restart() -> None:
    async with running_server() as (server, port, server_task):
        url = f"ws://127.0.0.1:{port}/ws"
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://127.0.0.1:{port}/health",
                headers={"Origin": "http://localhost:1420", "Authorization": f"Bearer {TOKEN}"},
            )
            assert response.headers["access-control-allow-origin"] == "http://localhost:1420"
        async with (
            connect(url, origin=Origin("http://localhost:1420"), subprotocols=PROTOCOLS) as first,
            connect(url, subprotocols=PROTOCOLS) as second,
        ):
            hello_ids = []
            for connection in (first, second):
                greeting = hello()
                hello_ids.append(greeting["event_id"])
                await connection.send(json.dumps(greeting))
                health = parse_event(json.loads(await asyncio.wait_for(connection.recv(), 2)))
                snapshot = parse_event(json.loads(await asyncio.wait_for(connection.recv(), 2)))
                assert isinstance(snapshot, AssistantStateChanged)
                assert (
                    str(health.correlation_id)
                    == str(snapshot.correlation_id)
                    == greeting["event_id"]
                )
                assert snapshot.payload.state == "idle"
            runtime = server.config.app.state.runtime
            runtime.transition("listening")
            one, two = await asyncio.gather(first.recv(), second.recv())
            one_event, two_event = json.loads(one), json.loads(two)
            assert one_event["event_id"] == two_event["event_id"]
            assert (
                one_event["payload"]
                == two_event["payload"]
                == {
                    "previous_state": "idle",
                    "state": "listening",
                }
            )
            assert [one_event["correlation_id"], two_event["correlation_id"]] == hello_ids
            await first.close()
            runtime.transition("transcribing")
            assert (
                json.loads(await asyncio.wait_for(second.recv(), 2))["payload"]["state"]
                == "transcribing"
            )
            server.should_exit = True
            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(second.recv(), 3)
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1012
            await asyncio.wait_for(server_task, 5)
            assert runtime.stopping and not runtime.sessions
    async with running_server(port) as (_, same_port, _):
        assert same_port == port
        async with connect(f"ws://127.0.0.1:{port}/ws", subprotocols=PROTOCOLS) as connection:
            await connection.send(json.dumps(hello()))
            await connection.recv()
            assert json.loads(await connection.recv())["payload"]["state"] == "idle"


@pytest.mark.asyncio
async def test_lifespan_shutdown_cancels_pending_handshake_and_sender() -> None:
    async with (
        running_server() as (server, port, _),
        connect(f"ws://127.0.0.1:{port}/ws", subprotocols=PROTOCOLS) as pending,
        connect(f"ws://127.0.0.1:{port}/ws", subprotocols=PROTOCOLS) as ready,
    ):
        await ready.send(json.dumps(hello()))
        await ready.recv()
        await ready.recv()
        runtime = server.config.app.state.runtime
        await asyncio.wait_for(runtime.shutdown(), 3)
        for connection in (pending, ready):
            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(connection.recv(), 2)
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1001
        assert not runtime.sessions


@pytest.mark.asyncio
async def test_authenticated_shutdown_runs_the_full_lifespan_cleanup() -> None:
    async with running_server() as (server, port, server_task):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/shutdown",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        assert response.status_code == 200
        assert response.json() == {"status": "stopping"}
        await asyncio.wait_for(server_task, 5)
        assert server.config.app.state.runtime.stopping
