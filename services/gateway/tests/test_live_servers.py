"""Real sockets (uvicorn on ephemeral ports) for behaviour in-process transports cannot show:
incremental SSE delivery / time-to-first-token, and the WebSocket bridge."""

from __future__ import annotations

import asyncio
import contextlib
import time

import httpx
import uvicorn
from asgi_lifespan import LifespanManager
from fastapi import FastAPI, WebSocket
from gw_helpers import FakeVLLM, bearer, chat
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from slm_gateway.app import create_app
from slm_gateway.settings import GatewaySettings
from slm_gateway.store import MemoryStore


@contextlib.asynccontextmanager
async def serve(app):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.should_exit = True
        await task


async def test_stream_arrives_incrementally_and_ttft_is_measured(pool, make_tenant, settings):
    fake = FakeVLLM("slow", delay=0.3)  # 3 content chunks, 0.3 s apart
    tenant = await make_tenant()
    key, _ = await tenant.add_key()
    async with serve(fake.app) as upstream_port:
        dep = await tenant.add_deployment("slow-dep", f"http://127.0.0.1:{upstream_port}")
        await tenant.add_endpoint("slow", [(dep, 100, "stable")])
        app = create_app(settings, pool=pool, store=MemoryStore())
        async with LifespanManager(app), serve(app) as gateway_port:
            arrivals: list[float] = []
            started = time.perf_counter()
            async with httpx.AsyncClient() as client, client.stream(
                "POST",
                f"http://127.0.0.1:{gateway_port}/v1/chat/completions",
                json=chat("slow", stream=True),
                headers=bearer(key),
            ) as r:
                assert r.status_code == 200
                async for _chunk in r.aiter_bytes():
                    arrivals.append(time.perf_counter() - started)
            total = arrivals[-1]
            assert arrivals[0] < 0.25, "first event must not wait for the whole generation"
            assert total >= 0.85
            assert len(arrivals) >= 5

            await app.state.gateway.drain()
            await app.state.gateway.usage.flush()
    [row] = await tenant.usage_rows()
    assert row["ttft_ms"] < 250 and row["latency_ms"] >= 850
    assert (row["prompt_tokens"], row["completion_tokens"]) == (11, 7)


def monitoring_app(seen: list[dict]) -> FastAPI:
    app = FastAPI()

    @app.websocket("/api/v1/monitoring/stream")
    async def stream(ws: WebSocket) -> None:
        await ws.accept()
        seen.append({"query": ws.url.query, "auth": ws.headers.get("authorization")})
        await ws.send_text("hello")
        while True:
            try:
                message = await ws.receive_text()
            except Exception:
                return
            await ws.send_text(f"echo:{message}")

    return app


async def test_websocket_is_bridged_to_the_monitoring_service(pool):
    seen: list[dict] = []
    async with serve(monitoring_app(seen)) as monitoring_port:
        settings = GatewaySettings(
            jwt_secret="gateway-test-secret-gateway-test-1234",
            monitoring_url=f"http://127.0.0.1:{monitoring_port}",
        )
        app = create_app(settings, pool=pool, store=MemoryStore())
        async with LifespanManager(app), serve(app) as gateway_port:
            url = f"ws://127.0.0.1:{gateway_port}/api/v1/monitoring/stream?token=abc"
            async with connect(url, additional_headers={"Authorization": "Bearer jwt"}) as ws:
                assert await asyncio.wait_for(ws.recv(), 5) == "hello"
                await ws.send("ping")
                assert await asyncio.wait_for(ws.recv(), 5) == "echo:ping"
    assert seen == [{"query": "token=abc", "auth": "Bearer jwt"}]


async def test_websocket_to_dead_service_is_closed(pool):
    settings = GatewaySettings(
        jwt_secret="gateway-test-secret-gateway-test-1234", monitoring_url="http://127.0.0.1:9"
    )
    app = create_app(settings, pool=pool, store=MemoryStore())
    async with LifespanManager(app), serve(app) as gateway_port:
        url = f"ws://127.0.0.1:{gateway_port}/api/v1/monitoring/stream"
        try:
            async with connect(url, open_timeout=5) as ws:
                await asyncio.wait_for(ws.recv(), 5)
        except (InvalidStatus, ConnectionClosed):  # handshake rejected, or closed with 1013
            pass
        else:
            raise AssertionError("expected the connection to be refused or closed")
