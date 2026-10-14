from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import httpx
import pytest
from slm_common.config import BaseServiceSettings
from slm_common.security import create_access_token
from slm_deployment.app import create_app

SETTINGS = BaseServiceSettings(jwt_secret="deployment-test-secret-deployment-123456")


@dataclass
class Actor:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str

    @property
    def headers(self) -> dict[str, str]:
        token = create_access_token(
            user_id=self.user_id, org_id=self.org_id, role=self.role, settings=SETTINGS
        )
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client(pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(SETTINGS, pool=pool)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def make_actor(admin_pool) -> Callable[..., Awaitable[Actor]]:
    """Create a real user (FKs require one) in a fresh or given org with the given role."""

    async def _make(role: str = "ml_engineer", org_id: uuid.UUID | None = None) -> Actor:
        uid = uuid.uuid4()
        if org_id is None:
            org_id = uuid.uuid4()
            await admin_pool.execute(
                "INSERT INTO organizations (id, slug, name) VALUES ($1, $2::text, $2::text)",
                org_id,
                f"org-{org_id.hex[:12]}",
            )
        await admin_pool.execute(
            "INSERT INTO users (id, email, display_name) VALUES ($1, $2::text, 'Test')",
            uid,
            f"{uid.hex}@example.com",
        )
        if role != "platform_admin":
            await admin_pool.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) VALUES ($1, $2, $3::org_role)",
                org_id,
                uid,
                role,
            )
        return Actor(uid, org_id, role)

    return _make


@pytest.fixture
def make_model_version(admin_pool) -> Callable[..., Awaitable[uuid.UUID]]:
    """A model + active version in `org_id`, so deployments have a valid `model_version_id` FK."""

    async def _make(org_id: uuid.UUID) -> uuid.UUID:
        model_id = uuid.uuid4()
        await admin_pool.execute(
            "INSERT INTO models (id, org_id, name, source) VALUES ($1, $2, $3::text, 'upload'::model_source)",
            model_id,
            org_id,
            f"m-{model_id.hex[:8]}",
        )
        version_id = uuid.uuid4()
        await admin_pool.execute(
            """INSERT INTO model_versions (id, model_id, org_id, version, status, format, artifact_uri)
               VALUES ($1, $2, $3, 1, 'active'::version_status, 'safetensors'::model_format, 's3://models/x')""",
            version_id,
            model_id,
            org_id,
        )
        return version_id

    return _make
