from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from slm_auth import repo
from slm_auth.deps import Pool
from slm_auth.schemas import MemberCreateIn, MemberOut, MemberPatchIn
from slm_common.audit import audit
from slm_common.auth import Principal
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.pagination import PageParams, build_page, parse_ts, parse_uuid
from slm_common.rbac import Role, require_role

router = APIRouter(prefix="/api/v1/orgs", tags=["orgs"])


def _member(row) -> MemberOut:
    return MemberOut.model_validate(dict(row))


@router.get("/members")
async def list_members(
    pool: Pool,
    page: PageParams = Depends(),
    principal: Principal = require_role(Role.org_admin),
):
    org_id = principal.require_org()
    after = (parse_ts(page.cursor["ts"]), parse_uuid(page.cursor["id"])) if page.cursor else None
    async with tenant_tx(pool, org_id) as conn:
        rows = await repo.list_members(conn, org_id, page.fetch, after)
    result = build_page(rows, page.limit, lambda r: {"ts": r["created_at"], "id": r["user_id"]})
    result["items"] = [_member(r) for r in result["items"]]
    return result


@router.post("/members", response_model=MemberOut, status_code=201)
async def add_member(
    body: MemberCreateIn,
    request: Request,
    pool: Pool,
    principal: Principal = require_role(Role.org_admin),
):
    """Add an existing user, or pre-provision a password-less user who then signs in via SSO."""
    org_id = principal.require_org()
    async with tenant_tx(pool, org_id) as conn:
        user = await repo.get_user_by_email(conn, body.email)
        provisioned = user is None
        if user is None:
            local_part = body.email.split("@")[0]
            user = await repo.create_user(
                conn,
                email=body.email,
                display_name=body.display_name or local_part,
                password_hash=None,
            )
        elif await repo.get_membership(conn, org_id, user["id"]):
            raise ApiError.conflict("User is already a member of this organization", "already_member")
        await repo.add_member(conn, org_id, user["id"], body.role)
        await audit(
            conn,
            org_id=org_id,
            actor=principal,
            action="member.add",
            resource="membership",
            resource_id=user["id"],
            detail={"role": body.role, "provisioned_user": provisioned},
            request=request,
        )
        row = await repo.get_member(conn, org_id, user["id"])
    return _member(row)


async def _guard_last_admin(conn, org_id: UUID, target) -> None:
    if target["role"] == "org_admin" and await repo.count_admins(conn, org_id) <= 1:
        raise ApiError.conflict("An organization must keep at least one org_admin", "last_admin")


@router.patch("/members/{user_id}", response_model=MemberOut)
async def update_member(
    user_id: UUID,
    body: MemberPatchIn,
    request: Request,
    pool: Pool,
    principal: Principal = require_role(Role.org_admin),
):
    org_id = principal.require_org()
    async with tenant_tx(pool, org_id) as conn:
        target = await repo.get_member(conn, org_id, user_id)
        if target is None:
            raise ApiError.not_found("member")
        if body.role != "org_admin":
            await _guard_last_admin(conn, org_id, target)
        await repo.set_member_role(conn, org_id, user_id, body.role)
        await audit(
            conn,
            org_id=org_id,
            actor=principal,
            action="member.update",
            resource="membership",
            resource_id=user_id,
            detail={"from": target["role"], "to": body.role},
            request=request,
        )
        row = await repo.get_member(conn, org_id, user_id)
    return _member(row)


@router.delete("/members/{user_id}", status_code=204)
async def remove_member(
    user_id: UUID,
    request: Request,
    pool: Pool,
    principal: Principal = require_role(Role.org_admin),
):
    org_id = principal.require_org()
    async with tenant_tx(pool, org_id) as conn:
        target = await repo.get_member(conn, org_id, user_id)
        if target is None:
            raise ApiError.not_found("member")
        await _guard_last_admin(conn, org_id, target)
        await repo.remove_member(conn, org_id, user_id)
        await audit(
            conn,
            org_id=org_id,
            actor=principal,
            action="member.remove",
            resource="membership",
            resource_id=user_id,
            detail={"role": target["role"]},
            request=request,
        )
    return Response(status_code=204)
