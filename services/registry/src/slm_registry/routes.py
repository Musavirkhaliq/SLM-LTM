from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query, Request

from slm_common.audit import audit
from slm_common.auth import Principal
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.pagination import PageParams, build_page, parse_ts, parse_uuid
from slm_common.rbac import Role, require_role
from slm_registry import repo
from slm_registry.schemas import (
    ModelCreateIn,
    ModelOut,
    ModelPatchIn,
    VersionCreateIn,
    VersionOut,
    VersionPatchIn,
)

router = APIRouter(prefix="/api/v1/models", tags=["registry"])

_ALLOWED_TRANSITIONS = {
    ("registered", "active"),
    ("registered", "archived"),
    ("archived", "active"),
    ("archived", "registered"),
    ("active", "archived"),
}


def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _model(row) -> ModelOut:
    return ModelOut.model_validate(dict(row))


def _version(row) -> VersionOut:
    return VersionOut.model_validate(dict(row))


@asynccontextmanager
async def _writable_model(
    pool: asyncpg.Pool, principal: Principal, model_id: UUID
) -> AsyncIterator[tuple[asyncpg.Connection, asyncpg.Record]]:
    """Transaction positioned to mutate one model, with the row locked.

    Org models use the caller's tenant context. Global catalogue rows are writable only
    through the RLS bypass, which is granted here solely to platform admins.
    """
    async with tenant_tx(pool, principal.org_id) as peek:
        probe = await repo.get_model(peek, model_id)
    if probe is None:
        raise ApiError.not_found("model")
    is_global = probe["org_id"] is None
    if is_global and not principal.is_platform_admin:
        raise ApiError.forbidden("Global catalogue models can only be changed by a platform admin")
    org_id = None if is_global else principal.require_org()
    async with tenant_tx(pool, org_id, bypass=is_global) as conn:
        model = await repo.get_model(conn, model_id, lock=True)
        if model is None:
            raise ApiError.not_found("model")
        yield conn, model


def _audit_org(model: asyncpg.Record) -> UUID | None:
    return model["org_id"]


@router.get("")
async def list_models(
    request: Request,
    page: PageParams = Depends(),
    scope: Literal["all", "org", "global"] = "all",
    family: str | None = Query(None, max_length=64),
    source: Literal["huggingface", "upload", "finetune"] | None = None,
    q: str | None = Query(None, max_length=128, description="Name substring"),
    include_archived: bool = False,
    principal: Principal = require_role(Role.viewer),
):
    org_id = principal.require_org()
    after = (parse_ts(page.cursor["ts"]), parse_uuid(page.cursor["id"])) if page.cursor else None
    async with tenant_tx(_pool(request), org_id) as conn:
        rows = await repo.list_models(
            conn,
            org_id,
            limit=page.fetch,
            after=after,
            scope=scope,
            family=family,
            source=source,
            query=q,
            include_archived=include_archived,
        )
    result = build_page(rows, page.limit, lambda r: {"ts": r["created_at"], "id": r["id"]})
    result["items"] = [_model(r) for r in result["items"]]
    return result


@router.post("", response_model=ModelOut, status_code=201)
async def create_model(
    body: ModelCreateIn,
    request: Request,
    principal: Principal = require_role(Role.ml_engineer),
):
    if body.global_catalogue and not principal.is_platform_admin:
        raise ApiError.forbidden("Only a platform admin can publish to the global catalogue")
    owner = None if body.global_catalogue else principal.require_org()
    fields = body.model_dump(exclude={"global_catalogue"})
    try:
        async with tenant_tx(_pool(request), owner, bypass=body.global_catalogue) as conn:
            model_id = await repo.insert_model(
                conn, org_id=owner, created_by=principal.user_id, **fields
            )
            await audit(
                conn,
                org_id=owner,
                actor=principal,
                action="model.create",
                resource="model",
                resource_id=model_id,
                detail={"name": body.name, "scope": "global" if owner is None else "org"},
                request=request,
            )
            row = await repo.get_model(conn, model_id)
    except asyncpg.UniqueViolationError as exc:
        raise ApiError.conflict(
            f"A model named '{body.name}' already exists", "model_name_taken"
        ) from exc
    return _model(row)


@router.get("/{model_id}", response_model=ModelOut)
async def get_model(
    model_id: UUID, request: Request, principal: Principal = require_role(Role.viewer)
):
    async with tenant_tx(_pool(request), principal.require_org()) as conn:
        row = await repo.get_model(conn, model_id)
    if row is None:
        raise ApiError.not_found("model")
    return _model(row)


@router.patch("/{model_id}", response_model=ModelOut)
async def patch_model(
    model_id: UUID,
    body: ModelPatchIn,
    request: Request,
    principal: Principal = require_role(Role.ml_engineer),
):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError.bad_request("No fields to update", "empty_update")
    async with _writable_model(_pool(request), principal, model_id) as (conn, model):
        if model["archived_at"] is not None:
            raise ApiError.conflict("Model is archived", "model_archived")
        await repo.update_model(conn, model_id, changes)
        await audit(
            conn,
            org_id=_audit_org(model),
            actor=principal,
            action="model.update",
            resource="model",
            resource_id=model_id,
            detail={"fields": sorted(changes)},
            request=request,
        )
        row = await repo.get_model(conn, model_id)
    return _model(row)


@router.post("/{model_id}/archive", response_model=ModelOut)
async def archive_model(
    model_id: UUID, request: Request, principal: Principal = require_role(Role.ml_engineer)
):
    async with _writable_model(_pool(request), principal, model_id) as (conn, model):
        if model["archived_at"] is not None:
            return _model(model)  # idempotent
        in_use = await repo.live_deployments(conn, model_id)
        if in_use:
            raise ApiError.conflict(
                f"{in_use} active deployment(s) still serve this model; stop them first",
                "model_in_use",
            )
        await repo.archive_model(conn, model_id)
        await audit(
            conn,
            org_id=_audit_org(model),
            actor=principal,
            action="model.archive",
            resource="model",
            resource_id=model_id,
            detail={"name": model["name"]},
            request=request,
        )
        row = await repo.get_model(conn, model_id)
    return _model(row)


@router.get("/{model_id}/versions")
async def list_versions(
    model_id: UUID, request: Request, principal: Principal = require_role(Role.viewer)
):
    async with tenant_tx(_pool(request), principal.require_org()) as conn:
        if await repo.get_model(conn, model_id) is None:
            raise ApiError.not_found("model")
        rows = await repo.list_versions(conn, model_id)
    # a model has few versions, so the full list is returned in a single page
    return {"items": [_version(r) for r in rows], "next_cursor": None}


@router.post("/{model_id}/versions", response_model=VersionOut, status_code=201)
async def create_version(
    model_id: UUID,
    body: VersionCreateIn,
    request: Request,
    principal: Principal = require_role(Role.ml_engineer),
):
    fields = body.model_dump(exclude={"activate"})
    async with _writable_model(_pool(request), principal, model_id) as (conn, model):
        if model["archived_at"] is not None:
            raise ApiError.conflict("Cannot add versions to an archived model", "model_archived")
        if body.base_version_id and not await repo.visible(
            conn, "model_versions", body.base_version_id
        ):
            raise ApiError.not_found("base version")
        if body.training_job_id and not await repo.visible(
            conn, "training_jobs", body.training_job_id
        ):
            raise ApiError.not_found("training job")
        number = await repo.next_version_number(conn, model_id)
        if body.activate:
            current = await repo.active_version(conn, model_id)
            if current:
                await repo.set_version_status(conn, current["id"], "archived")
        row = await repo.insert_version(
            conn,
            model_id=model_id,
            org_id=model["org_id"],
            version=number,
            status="active" if body.activate else "registered",
            created_by=principal.user_id,
            fields=fields,
        )
        await audit(
            conn,
            org_id=_audit_org(model),
            actor=principal,
            action="model_version.create",
            resource="model_version",
            resource_id=row["id"],
            detail={"model_id": model_id, "version": number, "activated": body.activate},
            request=request,
        )
    return _version(row)


@router.patch("/{model_id}/versions/{version}", response_model=VersionOut)
async def patch_version(
    model_id: UUID,
    version: int,
    body: VersionPatchIn,
    request: Request,
    principal: Principal = require_role(Role.ml_engineer),
):
    if body.benchmark_scores is None and body.status is None and body.metadata is None:
        raise ApiError.bad_request("No fields to update", "empty_update")
    async with _writable_model(_pool(request), principal, model_id) as (conn, model):
        target = await repo.get_version(conn, model_id, version, lock=True)
        if target is None:
            raise ApiError.not_found("model version")
        detail: dict[str, object] = {"model_id": model_id, "version": version}
        row = target
        if body.status is not None and body.status != target["status"]:
            if (target["status"], body.status) not in _ALLOWED_TRANSITIONS:
                raise ApiError.conflict(
                    f"Cannot move a version from {target['status']} to {body.status}",
                    "invalid_status_transition",
                )
            if body.status == "active":
                if model["archived_at"] is not None:
                    raise ApiError.conflict("Model is archived", "model_archived")
                current = await repo.active_version(conn, model_id)
                if current:
                    await repo.set_version_status(conn, current["id"], "archived")
            row = await repo.set_version_status(conn, target["id"], body.status)
            detail["status"] = {"from": target["status"], "to": body.status}
        if body.benchmark_scores is not None or body.metadata is not None:
            row = await repo.merge_version_fields(
                conn,
                target["id"],
                benchmark_scores=body.benchmark_scores,
                metadata=body.metadata,
            )
            if body.benchmark_scores is not None:
                detail["benchmark_scores"] = body.benchmark_scores
        await audit(
            conn,
            org_id=_audit_org(model),
            actor=principal,
            action="model_version.update",
            resource="model_version",
            resource_id=target["id"],
            detail=detail,
            request=request,
        )
    return _version(row)


@router.post("/{model_id}/versions/{version}/rollback", response_model=VersionOut)
async def rollback_version(
    model_id: UUID,
    version: int,
    request: Request,
    principal: Principal = require_role(Role.ml_engineer),
):
    """Re-activate an older version; the currently active one is archived."""
    async with _writable_model(_pool(request), principal, model_id) as (conn, model):
        if model["archived_at"] is not None:
            raise ApiError.conflict("Model is archived", "model_archived")
        target = await repo.get_version(conn, model_id, version, lock=True)
        if target is None:
            raise ApiError.not_found("model version")
        current = await repo.active_version(conn, model_id)
        if current is not None:
            if current["version"] == version:
                raise ApiError.conflict("Version is already active", "already_active")
            if version > current["version"]:
                raise ApiError.conflict(
                    f"Version {version} is newer than the active version {current['version']}; "
                    "rollback only re-activates older versions",
                    "not_an_older_version",
                )
            await repo.set_version_status(conn, current["id"], "archived")
        row = await repo.set_version_status(conn, target["id"], "active")
        await audit(
            conn,
            org_id=_audit_org(model),
            actor=principal,
            action="model_version.rollback",
            resource="model_version",
            resource_id=target["id"],
            detail={
                "model_id": model_id,
                "from_version": current["version"] if current else None,
                "to_version": version,
            },
            request=request,
        )
    return _version(row)
