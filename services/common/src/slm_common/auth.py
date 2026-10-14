from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import Request

from slm_common.config import BaseServiceSettings, get_settings
from slm_common.errors import ApiError
from slm_common.logging import bind_org
from slm_common.rbac import Role
from slm_common.security import decode_token


@dataclass(frozen=True, slots=True)
class Principal:
    kind: Literal["user", "api_key"]
    user_id: UUID | None
    org_id: UUID | None
    role: Role
    project_id: UUID | None = None
    key_id: UUID | None = None

    @property
    def is_platform_admin(self) -> bool:
        return self.role == Role.platform_admin

    @property
    def actor_id(self) -> UUID | None:
        return self.key_id if self.kind == "api_key" else self.user_id

    def require_org(self) -> UUID:
        if self.org_id is None:
            raise ApiError.forbidden(
                "No active organization; switch to an organization first", "no_org"
            )
        return self.org_id


def settings_for(request: Request) -> BaseServiceSettings:
    return getattr(request.app.state, "settings", None) or get_settings()


async def get_principal(request: Request) -> Principal:
    """Resolve the caller from a JWT bearer token (control-plane dependency)."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError.unauthorized("Missing bearer token", "missing_token")
    claims = decode_token(token.strip(), settings_for(request))
    try:
        principal = Principal(
            kind="user",
            user_id=UUID(claims["sub"]),
            org_id=UUID(claims["org"]) if claims.get("org") else None,
            role=Role.parse(claims.get("role", "viewer")),
        )
    except (KeyError, ValueError) as exc:
        raise ApiError.unauthorized("Malformed token claims", "invalid_token") from exc
    request.state.principal = principal
    if principal.org_id:
        bind_org(principal.org_id)
    return principal
