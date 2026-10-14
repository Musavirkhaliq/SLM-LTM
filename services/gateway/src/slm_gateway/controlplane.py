"""Reverse proxy for the control-plane REST API: `/api/v1/*` -> owning service by prefix."""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import structlog
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect

from slm_common.errors import ApiError
from slm_gateway.settings import GatewaySettings

router = APIRouter(tags=["control-plane"])
_log = structlog.get_logger(__name__)

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def resolve_service(settings: GatewaySettings, path: str) -> str | None:
    """Longest matching prefix wins; a prefix only matches whole path segments."""
    best: tuple[int, str] | None = None
    for prefix, base in settings.service_map().items():
        if (path == prefix or path.startswith(prefix + "/")) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), base)
    return best[1] if best else None


def _forward_headers(request: Request | WebSocket, settings: GatewaySettings) -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    client_host = request.client.host if request.client else ""
    if settings.trust_forwarded_for and (prior := headers.get("x-forwarded-for")):
        headers["x-forwarded-for"] = f"{prior}, {client_host}"
    else:
        headers["x-forwarded-for"] = client_host  # never trust a client-supplied value by default
    headers["x-forwarded-proto"] = request.url.scheme
    headers["x-forwarded-host"] = request.headers.get("host", "")
    headers["x-request-id"] = request.scope["state"]["request_id"]
    return headers


@router.api_route("/api/v1/{path:path}", methods=_METHODS, include_in_schema=False)
async def proxy(request: Request, path: str) -> Response:
    settings: GatewaySettings = request.app.state.settings
    base = resolve_service(settings, request.url.path)
    if base is None:
        raise ApiError.not_found("route", "route_not_found")
    client: httpx.AsyncClient = request.app.state.gateway.control_http
    has_body = request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
        "content-length" in request.headers or "transfer-encoding" in request.headers
    )
    upstream_request = client.build_request(
        request.method,
        base.rstrip("/") + request.url.path,
        params=request.query_params.multi_items(),
        headers=_forward_headers(request, settings),
        content=request.stream() if has_body else None,
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.TimeoutException as exc:
        raise ApiError(504, "upstream_timeout", "The upstream service timed out.") from exc
    except httpx.TransportError as exc:
        _log.warning("control_plane_unreachable", upstream=base, error=str(exc))
        raise ApiError(503, "upstream_unavailable", "The upstream service is unavailable.") from exc

    response = StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        background=BackgroundTask(upstream.aclose),
    )
    for name, value in upstream.headers.multi_items():
        if name.lower() not in _HOP_BY_HOP:
            response.headers.append(name, value)  # append keeps repeated Set-Cookie headers
    return response


@router.websocket("/api/v1/{path:path}")
async def proxy_websocket(websocket: WebSocket, path: str) -> None:
    """Bridge a client WebSocket (e.g. the monitoring stream) to the owning service."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    settings: GatewaySettings = websocket.app.state.settings
    base = resolve_service(settings, websocket.url.path)
    if base is None:
        await websocket.close(code=4404)
        return
    target = base.replace("http", "ws", 1).rstrip("/") + websocket.url.path
    if websocket.url.query:
        target += "?" + websocket.url.query
    headers = {
        k: v
        for k, v in _forward_headers(websocket, settings).items()
        if k.lower() in {"authorization", "x-request-id", "x-forwarded-for", "cookie"}
    }
    try:
        upstream = await connect(target, additional_headers=headers, open_timeout=5)
    except (OSError, TimeoutError, Exception) as exc:  # handshake refused or unreachable
        _log.warning("ws_upstream_connect_failed", target=target, error=str(exc))
        await websocket.close(code=1013)
        return
    await websocket.accept(subprotocol=upstream.subprotocol)

    async def client_to_upstream() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                await upstream.send(message["text"] if message.get("text") is not None else message["bytes"])
        except (WebSocketDisconnect, ConnectionClosed):
            return

    async def upstream_to_client() -> None:
        try:
            async for message in upstream:
                if isinstance(message, str):
                    await websocket.send_text(message)
                else:
                    await websocket.send_bytes(message)
        except (WebSocketDisconnect, ConnectionClosed):
            return

    tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await upstream.close()
        with contextlib.suppress(RuntimeError):
            await websocket.close()
