from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

OrgRole = Literal["org_admin", "ml_engineer", "developer", "viewer"]
KeyScope = Literal["organization", "project"]


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    org_name: str | None = Field(default=None, min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class LogoutIn(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=512)


class SwitchOrgIn(BaseModel):
    org_id: UUID


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserOut(BaseModel):
    id: UUID
    email: str
    display_name: str
    is_platform_admin: bool
    created_at: datetime


class OrgOut(BaseModel):
    id: UUID
    slug: str
    name: str
    plan: str


class RegisterOut(TokenResponse):
    user: UserOut
    org: OrgOut


class MembershipOut(BaseModel):
    org_id: UUID
    org_name: str
    org_slug: str
    role: OrgRole


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
    active_org_id: UUID | None
    role: str


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: OrgRole
    created_at: datetime


class MemberCreateIn(BaseModel):
    email: EmailStr
    role: OrgRole = "viewer"
    display_name: str | None = Field(default=None, min_length=1, max_length=120)


class MemberPatchIn(BaseModel):
    role: OrgRole


class ProjectCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    description: str | None = Field(default=None, max_length=2000)


class ProjectPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class ProjectOut(BaseModel):
    id: UUID
    org_id: UUID
    slug: str
    name: str
    description: str | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class ApiKeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scope: KeyScope = "project"
    project_id: UUID | None = None
    rate_limit_rpm: int | None = Field(default=None, ge=1, le=1_000_000)
    expires_at: datetime | None = None


class ApiKeyRotateIn(BaseModel):
    grace_seconds: int | None = Field(default=None, ge=0)


class ApiKeyOut(BaseModel):
    id: UUID
    org_id: UUID
    project_id: UUID | None
    scope: KeyScope
    name: str
    prefix: str
    status: Literal["active", "grace", "revoked", "expired"]
    rate_limit_rpm: int | None
    expires_at: datetime | None
    rotated_from: UUID | None
    grace_until: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_by: UUID | None
    created_at: datetime


class ApiKeyCreatedOut(ApiKeyOut):
    key: str = Field(description="Plaintext key; shown exactly once")
    previous_key_grace_until: datetime | None = None


class UsageBucket(BaseModel):
    day: datetime
    requests: int
    prompt_tokens: int
    completion_tokens: int
    errors: int


class ApiKeyUsageOut(BaseModel):
    api_key_id: UUID
    start: datetime = Field(serialization_alias="from")
    end: datetime = Field(serialization_alias="to")
    requests: int
    prompt_tokens: int
    completion_tokens: int
    errors: int
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    daily: list[UsageBucket]

    model_config = {"populate_by_name": True}
