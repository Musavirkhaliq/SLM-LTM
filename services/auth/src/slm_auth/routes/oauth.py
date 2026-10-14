from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

import asyncpg
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from slm_auth import oauth, repo, sessions
from slm_auth.deps import Http, Pool, Settings
from slm_auth.settings import PROVIDERS, ResolvedProvider
from slm_common.audit import audit
from slm_common.db import tenant_tx
from slm_common.errors import ApiError

router = APIRouter(prefix="/api/v1/auth/oauth", tags=["oauth"])
Provider = Literal["azure_ad", "google", "github"]
assert set(Provider.__args__) == set(PROVIDERS)


def _provider(settings, name: str) -> ResolvedProvider:
    provider = settings.provider(name)
    if provider is None:
        raise ApiError.not_found(f"OAuth provider '{name}' (not configured)", "oauth_provider_not_configured")
    return provider


@router.get("/{provider}/start", status_code=302)
async def oauth_start(provider: Provider, settings: Settings):
    cfg = _provider(settings, provider)
    url, sealed = oauth.new_authorization_request(settings, cfg)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        oauth.STATE_COOKIE,
        sealed,
        max_age=settings.oauth_state_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.public_base_url.startswith("https://"),
        path="/api/v1/auth/oauth",
    )
    return response


async def _resolve_user(
    conn: asyncpg.Connection, provider: str, profile: oauth.Profile
) -> tuple[asyncpg.Record, bool]:
    """Find or create the local user for an external identity. Returns (user, created)."""
    identity = await repo.find_identity(conn, provider, profile.subject)
    if identity:
        return await repo.get_user(conn, identity["user_id"]), False
    if not profile.email:
        raise ApiError.bad_request("The provider did not share an email address", "oauth_email_missing")
    existing = await repo.get_user_by_email(conn, profile.email)
    if existing:
        if not profile.email_verified:
            raise ApiError.conflict(
                "An account with this email exists but the provider did not verify the address",
                "oauth_email_unverified",
            )
        await repo.link_identity(conn, existing["id"], provider, profile.subject)
        return existing, False
    display = profile.name or profile.email.split("@")[0]
    user = await repo.create_user(conn, email=profile.email, display_name=display, password_hash=None)
    org = await repo.create_org(conn, f"{display}'s workspace")
    await repo.add_member(conn, org["id"], user["id"], "org_admin")
    await repo.ensure_quota(conn, org["id"])
    await repo.link_identity(conn, user["id"], provider, profile.subject)
    return user, True


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: Provider,
    request: Request,
    pool: Pool,
    settings: Settings,
    http: Http,
    code: str | None = Query(None, max_length=2048),
    state: str | None = Query(None, max_length=512),
    error: str | None = Query(None, max_length=200),
):
    cfg = _provider(settings, provider)
    if error:
        raise ApiError.bad_request(f"Provider returned an error: {error}", "oauth_provider_error")
    sealed = oauth.open_state(settings, request.cookies.get(oauth.STATE_COOKIE))
    if not code or not state or sealed["p"] != provider or sealed["s"] != state:
        raise ApiError.bad_request("OAuth state mismatch", "invalid_oauth_state")

    access_token = await oauth.exchange_code(http, settings, cfg, code, str(sealed["v"]))
    profile = await oauth.fetch_profile(http, cfg, access_token)

    async with tenant_tx(pool, None, bypass=True) as conn:
        user, created = await _resolve_user(conn, provider, profile)
        if not user["is_active"]:
            raise ApiError(403, "account_disabled", "Account is disabled")
        org_id = await sessions.default_org(conn, user["id"])
        await repo.touch_login(conn, user["id"])
        tokens = await sessions.issue_tokens(conn, settings, user_id=user["id"], org_id=org_id)
        await audit(
            conn,
            org_id=org_id,
            actor=user["id"],
            action="auth.oauth_register" if created else "auth.oauth_login",
            resource="user",
            resource_id=user["id"],
            detail={"provider": provider},
            request=request,
        )

    if settings.oauth_success_redirect:
        fragment = urlencode(tokens.model_dump())
        response = RedirectResponse(f"{settings.oauth_success_redirect}#{fragment}", status_code=302)
    else:
        response = JSONResponse(tokens.model_dump())
    response.delete_cookie(oauth.STATE_COOKIE, path="/api/v1/auth/oauth")
    return response
