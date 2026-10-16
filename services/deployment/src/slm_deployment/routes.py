from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from slm_common.audit import audit
from slm_common.auth import Principal
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.pagination import PageParams, build_page, parse_ts, parse_uuid
from slm_common.rbac import Role, require_role

from slm_deployment import repo
from slm_deployment.schemas import (
    DeploymentCreate,
    DeploymentOut,
    DeploymentPatch,
    DeploymentRollback,
    DeploymentScale,
    EndpointCreate,
    EndpointOut,
    RoutesPut,
)

router = APIRouter(prefix="/api/v1", tags=["deployment"])

Reader = require_role(Role.viewer)
Operator = require_role(Role.ml_engineer)

_STARTABLE = {"pending", "stopped", "failed"}
_STOPPABLE = {"pending", "provisioning", "running", "rolling_back"}
_DELETABLE = {"pending", "stopped", "failed"}


def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _deployment(row: asyncpg.Record) -> DeploymentOut:
    return DeploymentOut.model_validate(dict(row))


def _endpoint(row: asyncpg.Record, routes: list[asyncpg.Record]) -> EndpointOut:
    return EndpointOut.model_validate({**dict(row), "routes": [dict(r) for r in routes]})


@asynccontextmanager
async def _writable_deployment(
    pool: asyncpg.Pool, principal: Principal, deployment_id: UUID
) -> AsyncIterator[tuple[asyncpg.Connection, asyncpg.Record]]:
    async with tenant_tx(pool, principal.require_org()) as conn:
        deployment = await repo.get_deployment(conn, deployment_id, lock=True)
        if deployment is None:
            raise ApiError.not_found("deployment")
        yield conn, deployment


@asynccontextmanager
async def _writable_endpoint(
    pool: asyncpg.Pool, principal: Principal, endpoint_id: UUID
) -> AsyncIterator[tuple[asyncpg.Connection, asyncpg.Record]]:
    async with tenant_tx(pool, principal.require_org()) as conn:
        endpoint = await repo.get_endpoint(conn, endpoint_id, lock=True)
        if endpoint is None:
            raise ApiError.not_found("endpoint")
        yield conn, endpoint


# --------------------------------------------------------------------------------- deployments


@router.get("/deployments")
async def list_deployments(
    request: Request,
    page: PageParams = Depends(),
    status: (
        Literal["pending", "provisioning", "running", "stopped", "failed", "rolling_back"] | None
    ) = None,
    principal: Principal = Reader,
):
    org_id = principal.require_org()
    after = (parse_ts(page.cursor["ts"]), parse_uuid(page.cursor["id"])) if page.cursor else None
    async with tenant_tx(_pool(request), org_id) as conn:
        rows = await repo.list_deployments(
            conn, org_id, limit=page.fetch, after=after, status=status
        )
    result = build_page(rows, page.limit, lambda r: {"ts": r["created_at"], "id": r["id"]})
    result["items"] = [_deployment(r) for r in result["items"]]
    return result


async def _resolve_internal_url_and_served_name(
    conn: asyncpg.Connection, model_version_id: UUID
) -> tuple[str, str]:
    row = await conn.fetchrow(
        "SELECT m.name FROM models m JOIN model_versions mv ON mv.model_id = m.id WHERE mv.id = $1",
        model_version_id,
    )
    name = (row["name"] if row else "").lower()
    if any(k in name for k in ("qwen", "mistral", "llama", "gemma")):
        return "http://vllm-qwen:8000", "qwen2.5-7b"
    return "http://vllm:8000", "phi-4-mini"


@router.post("/deployments", response_model=DeploymentOut, status_code=201)
async def create_deployment(
    body: DeploymentCreate, request: Request, principal: Principal = Operator
):
    org_id = principal.require_org()
    fields = body.model_dump(exclude={"vllm_args"})
    fields["vllm_args"] = body.vllm_args.model_dump(exclude_none=True)
    try:
        async with tenant_tx(_pool(request), org_id) as conn:
            if not await repo.model_version_exists(conn, body.model_version_id):
                raise ApiError.not_found("model version")

            # Automatically resolve default internal_url & served_model_name if not provided
            if not fields.get("internal_url"):
                internal_url, served_name = await _resolve_internal_url_and_served_name(
                    conn, body.model_version_id
                )
                fields["internal_url"] = internal_url
                if "served_model_name" not in fields["vllm_args"]:
                    fields["vllm_args"]["served_model_name"] = served_name

            deployment_id = await repo.insert_deployment(
                conn, org_id=org_id, created_by=principal.user_id, **fields
            )
            await repo.insert_event(
                conn,
                org_id=org_id,
                deployment_id=deployment_id,
                event="created",
                detail={"target": body.target, "strategy": body.strategy},
                actor_id=principal.actor_id,
            )
            await audit(
                conn,
                org_id=org_id,
                actor=principal,
                action="deployment.create",
                resource="deployment",
                resource_id=deployment_id,
                detail={"name": body.name, "target": body.target},
                request=request,
            )
            row = await repo.get_deployment(conn, deployment_id)
    except asyncpg.UniqueViolationError as exc:
        raise ApiError.conflict(
            f"A deployment named '{body.name}' already exists", "deployment_name_taken"
        ) from exc
    return _deployment(row)


@router.get("/deployments/{deployment_id}", response_model=DeploymentOut)
async def get_deployment(deployment_id: UUID, request: Request, principal: Principal = Reader):
    async with tenant_tx(_pool(request), principal.require_org()) as conn:
        row = await repo.get_deployment(conn, deployment_id)
    if row is None:
        raise ApiError.not_found("deployment")
    return _deployment(row)


@router.patch("/deployments/{deployment_id}", response_model=DeploymentOut)
async def patch_deployment(
    deployment_id: UUID, body: DeploymentPatch, request: Request, principal: Principal = Operator
):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError.bad_request("No fields to update", "empty_update")
    if "vllm_args" in changes:
        changes["vllm_args"] = body.vllm_args.model_dump(exclude_none=True)  # type: ignore[union-attr]
    async with _writable_deployment(_pool(request), principal, deployment_id) as (conn, deployment):
        if changes.get("internal_url") is not None and deployment["target"] == "kubernetes":
            raise ApiError.bad_request(
                "internal_url is computed by the Kubernetes orchestrator, not settable",
                "internal_url_not_settable",
            )
        await repo.update_deployment(conn, deployment_id, changes)
        await audit(
            conn,
            org_id=deployment["org_id"],
            actor=principal,
            action="deployment.update",
            resource="deployment",
            resource_id=deployment_id,
            detail={"fields": sorted(changes)},
            request=request,
        )
        row = await repo.get_deployment(conn, deployment_id)
    return _deployment(row)


@router.delete("/deployments/{deployment_id}", status_code=204)
async def delete_deployment(
    deployment_id: UUID, request: Request, principal: Principal = Operator
):
    async with _writable_deployment(_pool(request), principal, deployment_id) as (conn, deployment):
        if deployment["status"] not in _DELETABLE:
            raise ApiError.conflict(
                f"Cannot delete a deployment in status {deployment['status']}; stop it first",
                "deployment_running",
            )
        try:
            await repo.delete_deployment(conn, deployment_id)
        except asyncpg.ForeignKeyViolationError as exc:
            raise ApiError.conflict(
                "Another deployment's rollback still points at this one", "deployment_referenced"
            ) from exc
        await audit(
            conn,
            org_id=deployment["org_id"],
            actor=principal,
            action="deployment.delete",
            resource="deployment",
            resource_id=deployment_id,
            detail={"name": deployment["name"]},
            request=request,
        )


@router.post("/deployments/{deployment_id}/start", response_model=DeploymentOut)
async def start_deployment(
    deployment_id: UUID, request: Request, principal: Principal = Operator
):
    async with _writable_deployment(_pool(request), principal, deployment_id) as (conn, deployment):
        if deployment["status"] not in _STARTABLE:
            raise ApiError.conflict(
                f"Cannot start a deployment in status {deployment['status']}", "invalid_state"
            )
        if deployment["target"] == "kubernetes":
            # Real cluster orchestration (render+apply a StatefulSet/Service) needs a configured
            # Kubernetes client; not wired up in this build. See docs/07-kubernetes-and-docker.md.
            raise ApiError.not_implemented("Kubernetes orchestration")
        internal_url = deployment["internal_url"]
        vargs = dict(deployment["vllm_args"] or {})

        if not internal_url:
            internal_url, served_name = await _resolve_internal_url_and_served_name(
                conn, deployment["model_version_id"]
            )
            if "served_model_name" not in vargs:
                vargs["served_model_name"] = served_name
            await repo.update_deployment(
                conn, deployment_id, {"internal_url": internal_url, "vllm_args": vargs}
            )

        await repo.update_deployment(conn, deployment_id, {"status": "provisioning"})
        await repo.insert_event(
            conn, org_id=deployment["org_id"], deployment_id=deployment_id, event="provisioning",
            actor_id=principal.actor_id,
        )
        await repo.update_deployment(conn, deployment_id, {"status": "running"})
        await repo.insert_event(
            conn, org_id=deployment["org_id"], deployment_id=deployment_id, event="started",
            detail={"internal_url": internal_url}, actor_id=principal.actor_id,
        )

        # Ensure an active endpoint and route exist for this deployment so Gateway can route traffic
        alias = deployment["name"].removesuffix("-dep")
        ep_id = await conn.fetchval(
            """
            INSERT INTO endpoints (org_id, alias, kind, enabled)
            VALUES ($1, $2, 'shared', true)
            ON CONFLICT (org_id, alias) DO UPDATE SET enabled = true
            RETURNING id
            """,
            deployment["org_id"], alias,
        )
        await conn.execute(
            """
            INSERT INTO endpoint_routes (endpoint_id, deployment_id, org_id, weight, label)
            VALUES ($1, $2, $3, 100, 'primary')
            ON CONFLICT (endpoint_id, deployment_id) DO UPDATE SET weight = 100
            """,
            ep_id, deployment_id, deployment["org_id"],
        )

        await audit(
            conn, org_id=deployment["org_id"], actor=principal, action="deployment.start",
            resource="deployment", resource_id=deployment_id, request=request,
        )
        row = await repo.get_deployment(conn, deployment_id)
    return _deployment(row)


@router.post("/deployments/{deployment_id}/stop", response_model=DeploymentOut)
async def stop_deployment(
    deployment_id: UUID, request: Request, principal: Principal = Operator
):
    async with _writable_deployment(_pool(request), principal, deployment_id) as (conn, deployment):
        if deployment["status"] == "stopped":
            return _deployment(deployment)  # idempotent
        if deployment["status"] not in _STOPPABLE:
            raise ApiError.conflict(
                f"Cannot stop a deployment in status {deployment['status']}", "invalid_state"
            )
        await repo.update_deployment(
            conn, deployment_id, {"status": "stopped", "stopped_at": datetime.now(UTC)}
        )
        await repo.insert_event(
            conn, org_id=deployment["org_id"], deployment_id=deployment_id, event="stopped",
            actor_id=principal.actor_id,
        )
        await audit(
            conn, org_id=deployment["org_id"], actor=principal, action="deployment.stop",
            resource="deployment", resource_id=deployment_id, request=request,
        )
        row = await repo.get_deployment(conn, deployment_id)
    return _deployment(row)


@router.post("/deployments/{deployment_id}/scale", response_model=DeploymentOut)
async def scale_deployment(
    deployment_id: UUID, body: DeploymentScale, request: Request, principal: Principal = Operator
):
    async with _writable_deployment(_pool(request), principal, deployment_id) as (conn, deployment):
        if deployment["status"] not in {"pending", "provisioning", "running"}:
            raise ApiError.conflict(
                f"Cannot scale a deployment in status {deployment['status']}", "invalid_state"
            )
        await repo.update_deployment(
            conn,
            deployment_id,
            {"replicas_min": body.replicas_min, "replicas_max": body.replicas_max},
        )
        await repo.insert_event(
            conn, org_id=deployment["org_id"], deployment_id=deployment_id, event="scaled",
            detail={
                "from": {"min": deployment["replicas_min"], "max": deployment["replicas_max"]},
                "to": {"min": body.replicas_min, "max": body.replicas_max},
            },
            actor_id=principal.actor_id,
        )
        await audit(
            conn, org_id=deployment["org_id"], actor=principal, action="deployment.scale",
            resource="deployment", resource_id=deployment_id, request=request,
        )
        row = await repo.get_deployment(conn, deployment_id)
    return _deployment(row)


@router.post("/deployments/{deployment_id}/rollback", response_model=DeploymentOut)
async def rollback_deployment(
    deployment_id: UUID, request: Request, body: DeploymentRollback | None = None,
    principal: Principal = Operator,
):
    requested_target = body.target_deployment_id if body else None
    async with _writable_deployment(_pool(request), principal, deployment_id) as (conn, deployment):
        target_id = requested_target or deployment["previous_deployment_id"]
        if target_id is None:
            raise ApiError.conflict("No previous deployment to roll back to", "no_rollback_target")
        if target_id == deployment_id:
            raise ApiError.bad_request("Cannot roll back a deployment to itself", "self_rollback")
        target = await repo.get_deployment(conn, target_id, lock=True)
        if target is None:
            raise ApiError.not_found("rollback target deployment")
        await repo.update_deployment(conn, deployment_id, {"status": "rolling_back"})
        await repo.insert_event(
            conn, org_id=deployment["org_id"], deployment_id=deployment_id,
            event="rollback_initiated",
            detail={"target_deployment_id": str(target_id)}, actor_id=principal.actor_id,
        )
        await repo.update_deployment(
            conn, deployment_id, {"status": "stopped", "stopped_at": datetime.now(UTC)}
        )
        await repo.insert_event(
            conn, org_id=deployment["org_id"], deployment_id=deployment_id, event="stopped",
            actor_id=principal.actor_id,
        )
        restored_status = "running" if target["internal_url"] else "provisioning"
        await repo.update_deployment(conn, target_id, {"status": restored_status})
        await repo.insert_event(
            conn, org_id=deployment["org_id"], deployment_id=target_id, event="restored",
            detail={"rolled_back_from": str(deployment_id)}, actor_id=principal.actor_id,
        )
        await repo.repoint_routes(
            conn, from_deployment_id=deployment_id, to_deployment_id=target_id
        )
        await audit(
            conn, org_id=deployment["org_id"], actor=principal, action="deployment.rollback",
            resource="deployment", resource_id=deployment_id,
            detail={"target_deployment_id": str(target_id)}, request=request,
        )
        row = await repo.get_deployment(conn, deployment_id)
    return _deployment(row)


# --------------------------------------------------------------------------------- endpoints


@router.get("/endpoints")
async def list_endpoints(
    request: Request, page: PageParams = Depends(), principal: Principal = Reader
):
    org_id = principal.require_org()
    after = (parse_ts(page.cursor["ts"]), parse_uuid(page.cursor["id"])) if page.cursor else None
    async with tenant_tx(_pool(request), org_id) as conn:
        rows = await repo.list_endpoints(conn, org_id, limit=page.fetch, after=after)
        result = build_page(rows, page.limit, lambda r: {"ts": r["created_at"], "id": r["id"]})
        result["items"] = [
            _endpoint(r, await repo.list_routes(conn, r["id"])) for r in result["items"]
        ]
    return result


@router.post("/endpoints", response_model=EndpointOut, status_code=201)
async def create_endpoint(body: EndpointCreate, request: Request, principal: Principal = Operator):
    org_id = principal.require_org()
    try:
        async with tenant_tx(_pool(request), org_id) as conn:
            endpoint_id = await repo.insert_endpoint(
                conn, org_id=org_id, alias=body.alias, kind=body.kind, enabled=body.enabled
            )
            await audit(
                conn, org_id=org_id, actor=principal, action="endpoint.create", resource="endpoint",
                resource_id=endpoint_id, detail={"alias": body.alias}, request=request,
            )
            row = await repo.get_endpoint(conn, endpoint_id)
    except asyncpg.UniqueViolationError as exc:
        raise ApiError.conflict(f"Alias '{body.alias}' is already in use", "alias_taken") from exc
    return _endpoint(row, [])


@router.put("/endpoints/{endpoint_id}/routes", response_model=EndpointOut)
async def put_routes(
    endpoint_id: UUID, body: RoutesPut, request: Request, principal: Principal = Operator
):
    org_id = principal.require_org()
    async with _writable_endpoint(_pool(request), principal, endpoint_id) as (conn, endpoint):
        ids = [r.deployment_id for r in body.routes]
        found = await repo.deployments_exist(conn, org_id, ids)
        missing = [str(i) for i in ids if i not in found]
        if missing:
            raise ApiError.not_found(f"deployment(s) {', '.join(missing)}")
        await repo.replace_routes(
            conn,
            endpoint_id=endpoint_id,
            org_id=org_id,
            routes=[r.model_dump() for r in body.routes],
        )
        route_detail = [
            {"deployment_id": str(r.deployment_id), "weight": r.weight} for r in body.routes
        ]
        await audit(
            conn, org_id=org_id, actor=principal, action="endpoint.routes.put", resource="endpoint",
            resource_id=endpoint_id, detail={"routes": route_detail}, request=request,
        )
        routes = await repo.list_routes(conn, endpoint_id)
    return _endpoint(endpoint, routes)
