"""OpenAI-compatible inference endpoints proxied to vLLM deployments."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import Counter, Histogram
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from slm_common.errors import ApiError
from slm_common.logging import bind_org
from slm_gateway.keys import KeyInfo
from slm_gateway.limits import RateInfo
from slm_gateway.routing import Resolved, Target
from slm_gateway.sse import SSEInspector
from slm_gateway.state import Gateway
from slm_gateway.usage import UsageRecord

router = APIRouter(tags=["inference"])
_log = structlog.get_logger(__name__)

REQUESTS = Counter("slm_gateway_inference_requests_total", "Inference requests", ["kind", "status"])
TOKENS = Counter("slm_gateway_tokens_total", "Tokens processed", ["type"])
TTFT = Histogram(
    "slm_gateway_ttft_seconds",
    "Time to first streamed event",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
LATENCY = Histogram(
    "slm_gateway_inference_latency_seconds",
    "End-to-end inference latency",
    ["kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

_RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class _Body(BaseModel):
    model_config = ConfigDict(extra="allow")  # unknown parameters pass through to vLLM

    model: str = Field(min_length=1, max_length=256)


class ChatBody(_Body):
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    stream_options: dict[str, Any] | None = None


class CompletionBody(_Body):
    prompt: str | list[Any]
    stream: bool = False
    max_tokens: int | None = Field(default=None, ge=1)
    stream_options: dict[str, Any] | None = None


class EmbeddingBody(_Body):
    input: str | list[Any]


_KINDS: dict[str, tuple[str, type[_Body]]] = {
    "chat": ("/v1/chat/completions", ChatBody),
    "completions": ("/v1/completions", CompletionBody),
    "embeddings": ("/v1/embeddings", EmbeddingBody),
}


def _gateway(request: Request) -> Gateway:
    return request.app.state.gateway


def _text_length(value: Any) -> int:
    return len(value) if isinstance(value, str) else len(json.dumps(value, default=str))


def _estimate(kind: str, payload: dict[str, Any], default_completion: int) -> tuple[int, int]:
    """Rough (prompt, completion) token counts (~4 chars/token) for admission control only."""
    source = {"chat": "messages", "completions": "prompt", "embeddings": "input"}[kind]
    prompt = max(_text_length(payload.get(source, "")) // 4, 1)
    if kind == "embeddings":
        return prompt, 0
    completion = payload.get("max_completion_tokens") or payload.get("max_tokens")
    return prompt, int(completion or default_completion)


async def _read_json(request: Request, limit: int) -> dict[str, Any]:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiError(413, "request_too_large", f"Request body exceeds {limit} bytes")
    raw = await request.body()
    if len(raw) > limit:
        raise ApiError(413, "request_too_large", f"Request body exceeds {limit} bytes")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_json", "Request body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_request_body", "Request body must be a JSON object")
    return payload


def _validate(kind: str, payload: dict[str, Any]) -> _Body:
    try:
        return _KINDS[kind][1].model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ApiError(
            400, "invalid_request_body", f"{loc}: {first['msg']}", param=loc or None
        ) from exc


def _usage_numbers(usage: dict[str, Any] | None) -> tuple[int, int, int]:
    if not usage:
        return 0, 0, 0
    details = usage.get("prompt_tokens_details") or {}
    return (
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        int(details.get("cached_tokens") or 0),
    )


@dataclass
class _Call:
    gw: Gateway
    request_id: str
    key: KeyInfo
    kind: str
    alias: str
    payload: dict[str, Any]
    stream: bool
    inject_usage: bool
    started: float
    rate: RateInfo
    resolved: Resolved
    prompt_estimate: int

    @property
    def path(self) -> str:
        return _KINDS[self.kind][0]

    def _elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def response_headers(self, target: Target | None) -> dict[str, str]:
        headers = self.rate.headers()
        if target is not None:
            headers["x-slm-route"] = target.label
        return headers

    def finish(
        self,
        target: Target | None,
        status: int,
        *,
        usage: dict[str, Any] | None = None,
        ttft_ms: int | None = None,
        streamed_chunks: int = 0,
    ) -> None:
        prompt, completion, cached = _usage_numbers(usage)
        if usage is None and (status < 400 or streamed_chunks):  # no upstream usage: estimate
            prompt, completion = self.prompt_estimate, streamed_chunks
        latency_ms = self._elapsed_ms()
        self.gw.usage.record(
            UsageRecord(
                org_id=self.key.org_id,
                project_id=self.key.project_id,
                api_key_id=self.key.key_id,
                deployment_id=target.deployment_id if target else None,
                model_version_id=target.model_version_id if target else None,
                endpoint_alias=self.alias,
                route_label=target.label if target else None,
                request_id=self.request_id,
                status_code=status,
                prompt_tokens=prompt,
                completion_tokens=completion,
                latency_ms=latency_ms,
                ttft_ms=ttft_ms,
                cached_tokens=cached,
            )
        )
        REQUESTS.labels(self.kind, str(status)).inc()
        LATENCY.labels(self.kind).observe(latency_ms / 1000)
        if ttft_ms is not None:
            TTFT.observe(ttft_ms / 1000)
        if status < 400 or prompt or completion:
            TOKENS.labels("prompt").inc(prompt)
            TOKENS.labels("completion").inc(completion)
            self.gw.background(self.gw.limiter.charge(self.key, prompt + completion))

    def _upstream_body(self, target: Target) -> dict[str, Any]:
        body = dict(self.payload)
        body["model"] = target.served_name
        if self.inject_usage:
            body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
        return body

    async def _connect(self, *, stream: bool) -> tuple[Target, httpx.Response]:
        """Try weighted targets until one accepts the connection. Only connect-level failures
        are retried: once a request may have reached a server it is never replayed."""
        tried: set = set()
        last: Exception | None = None
        while (target := self.gw.router.pick(self.resolved, frozenset(tried))) is not None:
            tried.add(target.deployment_id)
            request = self.gw.inference_http.build_request(
                "POST",
                target.base_url + self.path,
                json=self._upstream_body(target),
                headers={"x-request-id": self.request_id},
            )
            try:
                return target, await self.gw.inference_http.send(request, stream=stream)
            except _RETRYABLE as exc:
                last = exc
                _log.warning("upstream_connect_failed", deployment=str(target.deployment_id), error=str(exc))
            except httpx.TimeoutException as exc:
                self.finish(target, 504)
                raise ApiError(504, "upstream_timeout", "The model server timed out.") from exc
            except httpx.TransportError as exc:
                self.finish(target, 502)
                raise ApiError(502, "upstream_error", "The model server connection failed.") from exc
        self.finish(None, 502)
        raise ApiError(
            502, "upstream_unavailable", "No model server could be reached."
        ) from last

    async def run(self) -> Response:
        target, upstream = await self._connect(stream=self.stream)
        headers = self.response_headers(target)
        media_type = upstream.headers.get("content-type", "application/json")

        if not self.stream or upstream.status_code >= 400:
            content = await upstream.aread()
            await upstream.aclose()
            usage = None
            if upstream.status_code < 400:
                try:
                    usage = json.loads(content).get("usage")
                except (ValueError, AttributeError):
                    usage = None
            self.finish(target, upstream.status_code, usage=usage)
            return Response(
                content, status_code=upstream.status_code, media_type=media_type, headers=headers
            )

        return StreamingResponse(
            self._relay(target, upstream),
            media_type="text/event-stream",
            headers=headers | {"cache-control": "no-cache, no-transform", "x-accel-buffering": "no"},
        )

    async def _relay(self, target: Target, upstream: httpx.Response):
        inspector = SSEInspector(drop_usage_chunk=self.inject_usage)
        ttft_ms: int | None = None
        status = 200
        try:
            async for chunk in upstream.aiter_bytes():
                events = inspector.feed(chunk)
                if events and ttft_ms is None:
                    ttft_ms = self._elapsed_ms()
                for event in events:
                    yield event
            if tail := inspector.tail():
                yield tail
        except asyncio.CancelledError:
            status = 499  # client went away; the upstream connection is closed below
            raise
        except httpx.HTTPError as exc:
            status = 502
            _log.warning("upstream_stream_failed", error=str(exc))
            error = {"error": {"message": "The model server dropped the stream.", "type": "server_error", "code": "upstream_error"}}
            yield f"data: {json.dumps(error)}\n\n".encode()
        finally:
            await upstream.aclose()
            self.finish(
                target,
                status,
                usage=inspector.usage,
                ttft_ms=ttft_ms,
                streamed_chunks=inspector.content_events,
            )


async def _serve(request: Request, kind: str) -> Response:
    gw = _gateway(request)
    started = time.perf_counter()
    request_id = request.state.request_id
    key = await gw.keys.authenticate(request.headers.get("authorization"))
    bind_org(key.org_id)

    alias: str | None = None
    try:
        payload = await _read_json(request, gw.settings.max_request_bytes)
        body = _validate(kind, payload)
        alias = body.model
        prompt_est, completion_est = _estimate(kind, payload, gw.settings.default_completion_estimate)
        rate = await gw.limiter.admit(key, prompt_est + completion_est)
        resolved = await gw.router.resolve(key.org_id, alias)
    except ApiError as exc:
        # Rejections after authentication are logged too, so error rates show up in usage.
        gw.usage.record(
            UsageRecord(
                org_id=key.org_id,
                project_id=key.project_id,
                api_key_id=key.key_id,
                deployment_id=None,
                model_version_id=None,
                endpoint_alias=alias,
                route_label=None,
                request_id=request_id,
                status_code=exc.status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        )
        REQUESTS.labels(kind, str(exc.status_code)).inc()
        raise

    stream = bool(payload.get("stream")) and kind != "embeddings"
    options = payload.get("stream_options")
    client_wants_usage = isinstance(options, dict) and bool(options.get("include_usage"))
    call = _Call(
        gw=gw,
        request_id=request_id,
        key=key,
        kind=kind,
        alias=alias,
        payload=payload,
        stream=stream,
        # Ask vLLM for the usage chunk so streamed tokens are billed from real counts; the chunk
        # is withheld from clients that did not request it.
        inject_usage=stream and not client_wants_usage,
        started=started,
        rate=rate,
        resolved=resolved,
        prompt_estimate=prompt_est,
    )
    try:
        return await call.run()
    except ApiError as exc:
        exc.headers = {**rate.headers(), **exc.headers}
        raise


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    return await _serve(request, "chat")


@router.post("/v1/completions")
async def completions(request: Request) -> Response:
    return await _serve(request, "completions")


@router.post("/v1/embeddings")
async def embeddings(request: Request) -> Response:
    return await _serve(request, "embeddings")


@router.get("/v1/models")
async def list_models(request: Request) -> JSONResponse:
    gw = _gateway(request)
    key = await gw.keys.authenticate(request.headers.get("authorization"))
    rows = await gw.router.list_aliases(key.org_id)
    data = [
        {
            "id": r["alias"],
            "object": "model",
            "created": int(r["created_at"].timestamp()),
            "owned_by": "organization" if r["org_id"] == key.org_id else "slm-studio",
        }
        for r in rows
    ]
    return JSONResponse({"object": "list", "data": data})
