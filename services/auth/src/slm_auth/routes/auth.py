from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from slm_auth import repo, sessions
from slm_auth.deps import Pool, Settings
from slm_auth.schemas import (
    LoginIn,
    LogoutIn,
    MembershipOut,
    MeOut,
    OrgOut,
    RefreshIn,
    RegisterIn,
    RegisterOut,
    SwitchOrgIn,
    TokenResponse,
    UserOut,
)
from slm_common.audit import audit
from slm_common.auth import Principal, get_principal
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.security import hash_password, verify_password

router = APIRouter(prefix="/api/v1", tags=["auth"])

# Verified against when the account does not exist so login timing does not reveal accounts.
_DUMMY_HASH = hash_password("timing-equaliser")


def _user_out(row) -> UserOut:
    return UserOut.model_validate(dict(row))


@router.post("/auth/register", response_model=RegisterOut, status_code=201)
async def register(body: RegisterIn, request: Request, pool: Pool, settings: Settings):
    password_hash = hash_password(body.password)
    async with tenant_tx(pool, None, bypass=True) as conn:
        if await repo.get_user_by_email(conn, body.email):
            raise ApiError.conflict("An account with this email already exists", "email_taken")
        user = await repo.create_user(
            conn,
            email=body.email,
            display_name=body.display_name,
            password_hash=password_hash,
        )
        org = await repo.create_org(conn, body.org_name or f"{body.display_name}'s workspace")
        await repo.add_member(conn, org["id"], user["id"], "org_admin")
        await repo.ensure_quota(conn, org["id"])
        await repo.touch_login(conn, user["id"])
        tokens = await sessions.issue_tokens(
            conn, settings, user_id=user["id"], org_id=org["id"]
        )
        await audit(
            conn,
            org_id=org["id"],
            actor=user["id"],
            action="auth.register",
            resource="user",
            resource_id=user["id"],
            request=request,
        )
    return RegisterOut(**tokens.model_dump(), user=_user_out(user), org=OrgOut.model_validate(dict(org)))


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginIn, request: Request, pool: Pool, settings: Settings):
    async with tenant_tx(pool, None, bypass=True) as conn:
        user = await repo.get_user_by_email(conn, body.email)
        valid = verify_password(body.password, user["password_hash"] if user else _DUMMY_HASH)
        if user is None or not valid:
            raise ApiError(401, "invalid_credentials", "Invalid email or password")
        if not user["is_active"]:
            raise ApiError(403, "account_disabled", "Account is disabled")
        org_id = await sessions.default_org(conn, user["id"])
        await repo.touch_login(conn, user["id"])
        tokens = await sessions.issue_tokens(conn, settings, user_id=user["id"], org_id=org_id)
        await audit(
            conn,
            org_id=org_id,
            actor=user["id"],
            action="auth.login",
            resource="user",
            resource_id=user["id"],
            request=request,
        )
    return tokens


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(body: RefreshIn, request: Request, pool: Pool, settings: Settings):
    return await sessions.rotate_refresh_token(pool, settings, body.refresh_token, request)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, pool: Pool, body: LogoutIn | None = None):
    """With `refresh_token`: end that session. Without it: end all sessions of the bearer."""
    if body and body.refresh_token:
        user_id = await sessions.revoke_family_of(pool, body.refresh_token)
        org_id = sessions.org_from_refresh_token(body.refresh_token)
    else:
        principal = await get_principal(request)
        user_id, org_id = principal.user_id, principal.org_id
        async with tenant_tx(pool, None, bypass=True) as conn:
            await conn.execute(
                "UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
                user_id,
            )
    if user_id is not None:
        async with tenant_tx(pool, None, bypass=True) as conn:
            await audit(
                conn, org_id=org_id, actor=user_id, action="auth.logout", resource="user",
                resource_id=user_id, request=request,
            )
    return Response(status_code=204)


@router.get("/me", response_model=MeOut)
async def me(pool: Pool, principal: Principal = Depends(get_principal)):
    async with tenant_tx(pool, None, bypass=True) as conn:
        user = await repo.get_user(conn, principal.user_id)
        if user is None or not user["is_active"]:
            raise ApiError.unauthorized("Account no longer available", "account_disabled")
        memberships = await repo.memberships_for_user(conn, principal.user_id)
    return MeOut(
        user=_user_out(user),
        memberships=[MembershipOut.model_validate(dict(m)) for m in memberships],
        active_org_id=principal.org_id,
        role=principal.role.name,
    )


@router.post("/orgs/switch", response_model=TokenResponse)
async def switch_org(
    body: SwitchOrgIn,
    request: Request,
    pool: Pool,
    settings: Settings,
    principal: Principal = Depends(get_principal),
):
    async with tenant_tx(pool, None, bypass=True) as conn:
        if principal.is_platform_admin:
            if not await repo.org_exists(conn, body.org_id):
                raise ApiError.not_found("organization")
        elif await repo.get_membership(conn, body.org_id, principal.user_id) is None:
            raise ApiError.forbidden("You are not a member of that organization", "not_a_member")
        tokens = await sessions.issue_tokens(
            conn, settings, user_id=principal.user_id, org_id=body.org_id
        )
        await audit(
            conn,
            org_id=body.org_id,
            actor=principal,
            action="org.switch",
            resource="organization",
            resource_id=body.org_id,
            detail={"from": str(principal.org_id) if principal.org_id else None},
            request=request,
        )
    return tokens
