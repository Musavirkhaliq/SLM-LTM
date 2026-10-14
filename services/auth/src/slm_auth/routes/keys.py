from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response

from slm_auth import repo
from slm_auth.deps import Pool, Settings, invalidate_key_cache
from slm_auth.schemas import (
    ApiKeyCreatedOut,
    ApiKeyCreateIn,
    ApiKeyOut,
    ApiKeyRotateIn,
    ApiKeyUsageOut,
    UsageBucket,
)
from slm_common.audit import audit
from slm_common.auth import Principal
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.pagination import PageParams, build_page, parse_ts, parse_uuid
from slm_common.rbac import Role, require_role
from slm_common.security import generate_api_key

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])

MAX_USAGE_WINDOW = timedelta(days=366)


def _out(row) -> ApiKeyOut:
    return ApiKeyOut.model_validate(dict(row))


def _created(row, plaintext: str, **extra) -> ApiKeyCreatedOut:
    return ApiKeyCreatedOut(**_out(row).model_dump(), key=plaintext, **extra)


def _display_prefix(prefix: str) -> str:
    return f"slm_{prefix}"


def _can_manage(principal: Principal, key) -> bool:
    if principal.role >= Role.org_admin:
        return True
    return key["scope"] == "project" or key["created_by"] == principal.user_id


async def _load_manageable(conn: asyncpg.Connection, principal: Principal, key_id: UUID, *, lock=False):
    key = await repo.get_api_key(conn, principal.require_org(), key_id, lock=lock)
    if key is None:
        raise ApiError.not_found("api key")
    if not _can_manage(principal, key):
        raise ApiError.forbidden("Only an org_admin can manage organization-level keys")
    return key


@router.get("")
async def list_keys(
    pool: Pool,
    page: PageParams = Depends(),
    principal: Principal = require_role(Role.developer),
):
    org_id = principal.require_org()
    after = (parse_ts(page.cursor["ts"]), parse_uuid(page.cursor["id"])) if page.cursor else None
    visible_to = None if principal.role >= Role.org_admin else principal.user_id
    async with tenant_tx(pool, org_id) as conn:
        rows = await repo.list_api_keys(conn, org_id, page.fetch, after, visible_to=visible_to)
    result = build_page(rows, page.limit, lambda r: {"ts": r["created_at"], "id": r["id"]})
    result["items"] = [_out(r) for r in result["items"]]
    return result


@router.post("", response_model=ApiKeyCreatedOut, status_code=201)
async def create_key(
    body: ApiKeyCreateIn,
    request: Request,
    pool: Pool,
    principal: Principal = require_role(Role.developer),
):
    org_id = principal.require_org()
    if body.scope == "organization":
        if principal.role < Role.org_admin:
            raise ApiError.forbidden("Organization-level keys require org_admin")
        if body.project_id is not None:
            raise ApiError(422, "validation_error", "project_id is only valid for project keys")
    elif body.project_id is None:
        raise ApiError(422, "validation_error", "project_id is required for project keys")
    if body.expires_at and body.expires_at <= datetime.now(UTC):
        raise ApiError(422, "validation_error", "expires_at must be in the future")

    plaintext, prefix, digest = generate_api_key()
    async with tenant_tx(pool, org_id) as conn:
        if body.project_id and await repo.get_project(conn, org_id, body.project_id) is None:
            raise ApiError.not_found("project")
        row = await repo.insert_api_key(
            conn,
            org_id=org_id,
            project_id=body.project_id,
            scope=body.scope,
            name=body.name,
            prefix=_display_prefix(prefix),
            key_hash=digest,
            created_by=principal.user_id,
            rate_limit_rpm=body.rate_limit_rpm,
            expires_at=body.expires_at,
        )
        await audit(
            conn,
            org_id=org_id,
            actor=principal,
            action="api_key.create",
            resource="api_key",
            resource_id=row["id"],
            detail={"scope": body.scope, "project_id": body.project_id, "name": body.name},
            request=request,
        )
    return _created(row, plaintext)


@router.post("/{key_id}/rotate", response_model=ApiKeyCreatedOut, status_code=201)
async def rotate_key(
    key_id: UUID,
    request: Request,
    pool: Pool,
    settings: Settings,
    body: ApiKeyRotateIn | None = None,
    principal: Principal = require_role(Role.developer),
):
    """Issue a successor key; the old one keeps working until `grace_until`."""
    org_id = principal.require_org()
    grace = (
        body.grace_seconds
        if body and body.grace_seconds is not None
        else settings.api_key_default_grace_seconds
    )
    if grace > settings.api_key_max_grace_seconds:
        raise ApiError(422, "validation_error", f"grace_seconds must be <= {settings.api_key_max_grace_seconds}")
    plaintext, prefix, digest = generate_api_key()
    async with tenant_tx(pool, org_id) as conn:
        old = await _load_manageable(conn, principal, key_id, lock=True)
        if old["status"] in ("revoked", "expired"):
            raise ApiError.conflict(f"Key is {old['status']} and cannot be rotated", "key_not_active")
        if old["grace_until"] is not None:
            raise ApiError.conflict("Key was already rotated", "already_rotated")
        new = await repo.insert_api_key(
            conn,
            org_id=org_id,
            project_id=old["project_id"],
            scope=old["scope"],
            name=old["name"],
            prefix=_display_prefix(prefix),
            key_hash=digest,
            created_by=principal.user_id,
            rate_limit_rpm=old["rate_limit_rpm"],
            expires_at=old["expires_at"],
            rotated_from=old["id"],
        )
        grace_until = await repo.set_grace(conn, org_id, key_id, grace)
        await audit(
            conn,
            org_id=org_id,
            actor=principal,
            action="api_key.rotate",
            resource="api_key",
            resource_id=key_id,
            detail={"new_key_id": new["id"], "grace_until": grace_until.isoformat()},
            request=request,
        )
    await invalidate_key_cache(request, [old["key_hash"]])
    return _created(new, plaintext, previous_key_grace_until=grace_until)


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: UUID,
    request: Request,
    pool: Pool,
    principal: Principal = require_role(Role.developer),
):
    org_id = principal.require_org()
    async with tenant_tx(pool, org_id) as conn:
        key = await _load_manageable(conn, principal, key_id, lock=True)
        if key["revoked_at"] is None:
            await repo.revoke_api_key(conn, org_id, key_id)
            await audit(
                conn,
                org_id=org_id,
                actor=principal,
                action="api_key.revoke",
                resource="api_key",
                resource_id=key_id,
                detail={"name": key["name"]},
                request=request,
            )
    await invalidate_key_cache(request, [key["key_hash"]])
    return Response(status_code=204)


@router.get("/{key_id}/usage", response_model=ApiKeyUsageOut, response_model_by_alias=True)
async def key_usage(
    key_id: UUID,
    pool: Pool,
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    principal: Principal = require_role(Role.viewer),
):
    org_id = principal.require_org()
    end = end or datetime.now(UTC)
    start = start or end - timedelta(days=7)
    if start.tzinfo is None or end.tzinfo is None:
        raise ApiError.bad_request("from/to must include a timezone offset", "invalid_range")
    if start >= end or end - start > MAX_USAGE_WINDOW:
        raise ApiError.bad_request("Invalid range: need from < to, at most 366 days", "invalid_range")
    async with tenant_tx(pool, org_id) as conn:
        if await repo.get_api_key(conn, org_id, key_id) is None:
            raise ApiError.not_found("api key")
        totals, daily = await repo.key_usage(conn, org_id, key_id, start, end)
    return ApiKeyUsageOut(
        api_key_id=key_id,
        start=start,
        end=end,
        **dict(totals),
        daily=[UsageBucket.model_validate(dict(d)) for d in daily],
    )
