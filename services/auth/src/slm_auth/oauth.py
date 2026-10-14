"""OAuth 2.0 authorization-code flow (with PKCE) for Azure AD, Google and GitHub."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from slm_auth.settings import AuthSettings, ResolvedProvider
from slm_common.errors import ApiError

STATE_COOKIE = "slm_oauth"


@dataclass(frozen=True)
class Profile:
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _key(settings: AuthSettings) -> bytes:
    return settings.jwt_secret.get_secret_value().encode()


def seal_state(settings: AuthSettings, payload: dict[str, str | float]) -> str:
    """HMAC-signed browser-bound blob holding the CSRF state and PKCE verifier."""
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(settings), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def open_state(settings: AuthSettings, sealed: str | None) -> dict[str, str | float]:
    bad = ApiError.bad_request("OAuth state is missing or invalid", "invalid_oauth_state")
    if not sealed or sealed.count(".") != 1:
        raise bad
    body, sig = sealed.split(".")
    expected = _b64(hmac.new(_key(settings), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise bad
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError as exc:
        raise bad from exc
    if float(payload.get("exp", 0)) < time.time():
        raise ApiError.bad_request("OAuth state has expired", "oauth_state_expired")
    return payload


def new_authorization_request(
    settings: AuthSettings, provider: ResolvedProvider
) -> tuple[str, str]:
    """Return (authorize_url, sealed cookie value)."""
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    params = {
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri(settings, provider.name),
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    sealed = seal_state(
        settings,
        {"p": provider.name, "s": state, "v": verifier, "exp": time.time() + settings.oauth_state_ttl_seconds},
    )
    return f"{provider.authorize_url}?{urlencode(params)}", sealed


def redirect_uri(settings: AuthSettings, provider: str) -> str:
    return f"{settings.redirect_base}/{provider}/callback"


async def exchange_code(
    client: httpx.AsyncClient,
    settings: AuthSettings,
    provider: ResolvedProvider,
    code: str,
    verifier: str,
) -> str:
    resp = await client.post(
        provider.token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(settings, provider.name),
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
            "code_verifier": verifier,
        },
        headers={"Accept": "application/json"},
    )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    token = body.get("access_token") if isinstance(body, dict) else None
    if resp.status_code != 200 or not token:
        raise ApiError(502, "oauth_token_exchange_failed", f"{provider.name} rejected the code exchange")
    return token


async def fetch_profile(
    client: httpx.AsyncClient, provider: ResolvedProvider, access_token: str
) -> Profile:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    resp = await client.get(provider.userinfo_url, headers=headers)
    if resp.status_code != 200:
        raise ApiError(502, "oauth_userinfo_failed", f"{provider.name} userinfo request failed")
    info = resp.json()
    if provider.name == "github":
        return await _github_profile(client, provider, headers, info)
    subject = info.get("sub")
    if not subject:
        raise ApiError(502, "oauth_userinfo_invalid", "Provider returned no subject")
    email = info.get("email") or info.get("preferred_username")
    if email and "@" not in email:
        email = None
    # Azure AD omits email_verified; its `email` claim is not guaranteed verified, so it
    # never counts as verified unless the provider says so.
    verified = info.get("email_verified") in (True, "true")
    return Profile(str(subject), email, bool(email and verified), info.get("name"))


async def _github_profile(
    client: httpx.AsyncClient,
    provider: ResolvedProvider,
    headers: dict[str, str],
    info: dict,
) -> Profile:
    if "id" not in info:
        raise ApiError(502, "oauth_userinfo_invalid", "Provider returned no subject")
    email, verified = info.get("email"), False
    if provider.emails_url:
        resp = await client.get(provider.emails_url, headers=headers)
        if resp.status_code == 200:
            primary = next(
                (e for e in resp.json() if e.get("primary") and e.get("verified")), None
            )
            if primary:
                email, verified = primary["email"], True
    return Profile(str(info["id"]), email, bool(email and verified), info.get("name") or info.get("login"))
