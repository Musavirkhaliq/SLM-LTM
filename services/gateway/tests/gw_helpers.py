"""Test doubles and helpers for the gateway tests (a real module so test files can import it)."""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from slm_common.security import API_KEY_CACHE_PREFIX, generate_api_key, hash_api_key

PROMPT_TOKENS, COMPLETION_TOKENS, CACHED_TOKENS = 11, 7, 4


class FakeVLLM:
    """Minimal OpenAI-compatible server standing in for a vLLM instance."""

    def __init__(self, name: str, *, delay: float = 0.0, fail_status: int | None = None) -> None:
        self.name = name
        self.delay = delay
        self.fail_status = fail_status
        self.calls: list[dict] = []
        self.app = FastAPI()
        self.app.post("/v1/chat/completions")(self._chat)
        self.app.post("/v1/completions")(self._completion)
        self.app.post("/v1/embeddings")(self._embeddings)

    def _record(self, request: Request, body: dict) -> None:
        self.calls.append({"body": body, "headers": dict(request.headers), "path": request.url.path})

    def _usage(self, completion: int = COMPLETION_TOKENS) -> dict:
        return {
            "prompt_tokens": PROMPT_TOKENS,
            "completion_tokens": completion,
            "total_tokens": PROMPT_TOKENS + completion,
            "prompt_tokens_details": {"cached_tokens": CACHED_TOKENS},
        }

    def _maybe_fail(self) -> JSONResponse | None:
        if self.fail_status:
            return JSONResponse(
                {"error": {"message": "boom", "type": "server_error", "code": "boom"}},
                status_code=self.fail_status,
            )
        return None

    async def _chat(self, request: Request):
        body = await request.json()
        self._record(request, body)
        if (failure := self._maybe_fail()) is not None:
            return failure
        if not body.get("stream"):
            return {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": body["model"],
                "system_fingerprint": self.name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"hello from {self.name}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": self._usage(),
            }
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        return StreamingResponse(self._events(body, include_usage), media_type="text/event-stream")

    async def _events(self, body: dict, include_usage: bool) -> AsyncIterator[bytes]:
        def chunk(delta: dict, finish: str | None = None) -> bytes:
            payload = {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "model": body["model"],
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(payload)}\n\n".encode()

        yield chunk({"role": "assistant", "content": ""})
        for word in ("hel", "lo ", self.name):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk({"content": word})
        yield chunk({}, "stop")
        if include_usage:
            final = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [], "usage": self._usage()}
            yield f"data: {json.dumps(final)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def _completion(self, request: Request):
        body = await request.json()
        self._record(request, body)
        return {
            "id": "cmpl-1",
            "object": "text_completion",
            "model": body["model"],
            "choices": [{"index": 0, "text": f"hello from {self.name}", "finish_reason": "stop"}],
            "usage": self._usage(),
        }

    async def _embeddings(self, request: Request):
        body = await request.json()
        self._record(request, body)
        return {
            "object": "list",
            "model": body["model"],
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        }


class HostTransport(httpx.AsyncBaseTransport):
    """Routes each upstream hostname to its own in-process ASGI app; unknown hosts refuse."""

    def __init__(self) -> None:
        self.hosts: dict[str, httpx.AsyncBaseTransport] = {}

    def add(self, host: str, app: FastAPI) -> None:
        self.hosts[host] = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self.hosts.get(request.url.host)
        if transport is None:
            raise httpx.ConnectError(f"connection refused: {request.url.host}", request=request)
        return await transport.handle_async_request(request)


class FakeControlPlane(httpx.AsyncBaseTransport):
    """Stands in for every control-plane service: records requests, answers via `respond`."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail: type[httpx.TransportError] | None = None
        self.respond: Callable[[Request, bytes], Response] = lambda request, body: JSONResponse({"ok": True})
        self.app = FastAPI()
        self.app.api_route(
            "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
        )(self._handle)
        self._inner = httpx.ASGITransport(app=self.app)

    async def _handle(self, request: Request) -> Response:
        body = await request.body()
        self.calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
            }
        )
        return self.respond(request, body)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.fail is not None:
            raise self.fail("scripted failure", request=request)
        return await self._inner.handle_async_request(request)


@dataclass
class Tenant:
    org_id: uuid.UUID
    pool: object
    version_id: uuid.UUID | None = None
    keys: dict[str, str] = field(default_factory=dict)

    async def add_key(
        self,
        *,
        rate_limit_rpm: int | None = None,
        expires_at: datetime | None = None,
        grace_until: datetime | None = None,
        revoked_at: datetime | None = None,
        project: bool = False,
    ) -> tuple[str, uuid.UUID]:
        plaintext, prefix, digest = generate_api_key()
        key_id = uuid.uuid4()
        project_id = None
        if project:
            project_id = uuid.uuid4()
            await self.pool.execute(
                "INSERT INTO projects (id, org_id, slug, name) VALUES ($1, $2, $3::text, 'p')",
                project_id, self.org_id, f"p-{project_id.hex[:8]}",
            )
        await self.pool.execute(
            """INSERT INTO api_keys (id, org_id, project_id, scope, name, prefix, key_hash,
                                     rate_limit_rpm, expires_at, grace_until, revoked_at)
               VALUES ($1, $2, $3, $4::api_key_scope, 'k', $5, $6, $7, $8, $9, $10)""",
            key_id, self.org_id, project_id, "project" if project else "organization",
            f"slm_{prefix}", digest, rate_limit_rpm, expires_at, grace_until, revoked_at,
        )
        return plaintext, key_id

    async def add_deployment(
        self, name: str, url: str | None, status: str = "running", vllm_args: dict | None = None
    ) -> uuid.UUID:
        if self.version_id is None:
            model_id, self.version_id = uuid.uuid4(), uuid.uuid4()
            await self.pool.execute(
                "INSERT INTO models (id, org_id, name, source) VALUES ($1, $2, $3, 'upload')",
                model_id, self.org_id, f"m-{model_id.hex[:8]}",
            )
            await self.pool.execute(
                "INSERT INTO model_versions (id, model_id, org_id, version, format, artifact_uri) "
                "VALUES ($1, $2, $3, 1, 'hf', 's3://x/y')",
                self.version_id, model_id, self.org_id,
            )
        dep_id = uuid.uuid4()
        await self.pool.execute(
            """INSERT INTO deployments (id, org_id, name, model_version_id, status, internal_url, vllm_args)
               VALUES ($1, $2, $3, $4, $5::deploy_status, $6, $7)""",
            dep_id, self.org_id, name, self.version_id, status, url, vllm_args or {},
        )
        return dep_id

    async def add_endpoint(
        self,
        alias: str,
        routes: list[tuple[uuid.UUID, int, str | None]],
        *,
        kind: str = "private",
        enabled: bool = True,
    ) -> uuid.UUID:
        endpoint_id = uuid.uuid4()
        await self.pool.execute(
            "INSERT INTO endpoints (id, org_id, alias, kind, enabled) VALUES ($1, $2, $3, $4::endpoint_kind, $5)",
            endpoint_id, self.org_id, alias, kind, enabled,
        )
        for dep_id, weight, label in routes:
            await self.pool.execute(
                "INSERT INTO endpoint_routes (endpoint_id, deployment_id, org_id, weight, label) "
                "VALUES ($1, $2, $3, $4, $5)",
                endpoint_id, dep_id, self.org_id, weight, label,
            )
        return endpoint_id

    async def usage_rows(self, **where) -> list:
        clause = "".join(f" AND {k} = ${i}" for i, k in enumerate(where, start=2))
        return await self.pool.fetch(
            f"SELECT * FROM usage_logs WHERE org_id = $1{clause} ORDER BY id", self.org_id, *where.values()
        )




def chat(model: str, **extra) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]} | extra


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def past(seconds: int = 60) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


def future(seconds: int = 3600) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


def cache_key(plaintext: str) -> str:
    return API_KEY_CACHE_PREFIX + hash_api_key(plaintext).hex()

