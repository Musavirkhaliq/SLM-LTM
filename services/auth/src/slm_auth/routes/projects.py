from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request, Response

from slm_auth import repo
from slm_auth.deps import Pool, invalidate_key_cache
from slm_auth.schemas import ProjectCreateIn, ProjectOut, ProjectPatchIn
from slm_common.audit import audit
from slm_common.auth import Principal
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.pagination import PageParams, build_page, parse_ts, parse_uuid
from slm_common.rbac import Role, require_role

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def _out(row) -> ProjectOut:
    return ProjectOut.model_validate(dict(row))


@router.get("")
async def list_projects(
    pool: Pool,
    page: PageParams = Depends(),
    principal: Principal = require_role(Role.viewer),
):
    org_id = principal.require_org()
    after = (parse_ts(page.cursor["ts"]), parse_uuid(page.cursor["id"])) if page.cursor else None
    async with tenant_tx(pool, org_id) as conn:
        rows = await repo.list_projects(conn, org_id, page.fetch, after)
    result = build_page(rows, page.limit, lambda r: {"ts": r["created_at"], "id": r["id"]})
    result["items"] = [_out(r) for r in result["items"]]
    return result


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreateIn,
    request: Request,
    pool: Pool,
    principal: Principal = require_role(Role.ml_engineer),
):
    org_id = principal.require_org()
    slug = body.slug or repo.slugify(body.name, "project")
    try:
        async with tenant_tx(pool, org_id) as conn:
            row = await repo.insert_project(
                conn,
                org_id,
                slug=slug,
                name=body.name,
                description=body.description,
                created_by=principal.user_id,
            )
            await audit(
                conn,
                org_id=org_id,
                actor=principal,
                action="project.create",
                resource="project",
                resource_id=row["id"],
                detail={"slug": slug, "name": body.name},
                request=request,
            )
    except asyncpg.UniqueViolationError as exc:
        raise ApiError.conflict(f"A project with slug '{slug}' already exists", "slug_taken") from exc
    return _out(row)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: UUID, pool: Pool, principal: Principal = require_role(Role.viewer)
):
    org_id = principal.require_org()
    async with tenant_tx(pool, org_id) as conn:
        row = await repo.get_project(conn, org_id, project_id)
    if row is None:
        raise ApiError.not_found("project")
    return _out(row)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: UUID,
    body: ProjectPatchIn,
    request: Request,
    pool: Pool,
    principal: Principal = require_role(Role.ml_engineer),
):
    org_id = principal.require_org()
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError.bad_request("No fields to update", "empty_update")
    async with tenant_tx(pool, org_id) as conn:
        row = await repo.update_project(conn, org_id, project_id, changes)
        if row is None:
            raise ApiError.not_found("project")
        await audit(
            conn,
            org_id=org_id,
            actor=principal,
            action="project.update",
            resource="project",
            resource_id=project_id,
            detail={"fields": sorted(changes)},
            request=request,
        )
    return _out(row)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: UUID,
    request: Request,
    pool: Pool,
    principal: Principal = require_role(Role.org_admin),
):
    org_id = principal.require_org()
    async with tenant_tx(pool, org_id) as conn:
        if not await repo.soft_delete_project(conn, org_id, project_id):
            raise ApiError.not_found("project")
        revoked = await repo.revoke_project_keys(conn, org_id, project_id)
        await audit(
            conn,
            org_id=org_id,
            actor=principal,
            action="project.delete",
            resource="project",
            resource_id=project_id,
            detail={"revoked_keys": len(revoked)},
            request=request,
        )
    await invalidate_key_cache(request, revoked)
    return Response(status_code=204)
