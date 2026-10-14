import uuid

from slm_common.security import decode_token

PASSWORD = "correct-horse-battery"


async def audit_actions(admin_pool, org_id: str) -> list[str]:
    rows = await admin_pool.fetch(
        "SELECT action FROM audit_logs WHERE org_id = $1 ORDER BY id", uuid.UUID(org_id)
    )
    return [r["action"] for r in rows]


async def test_register_login_me_flow(client, register, admin_pool):
    s = await register(org_name="Acme Corp")
    r = await client.get("/api/v1/me", headers=s.headers)
    assert r.status_code == 200
    me = r.json()
    assert me["user"]["email"] == s.email and me["role"] == "org_admin"
    assert me["active_org_id"] == s.org_id
    assert me["memberships"][0]["org_slug"] == "acme-corp"
    assert "password_hash" not in r.text

    r = await client.post("/api/v1/auth/login", json={"email": s.email.upper(), "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert {"access_token", "refresh_token"} <= body.keys()
    assert "auth.register" in await audit_actions(admin_pool, s.org_id)
    assert "auth.login" in await audit_actions(admin_pool, s.org_id)

    quota = await admin_pool.fetchval("SELECT rpm_limit FROM quotas WHERE org_id = $1", uuid.UUID(s.org_id))
    assert quota == 600


async def test_access_token_claims(client, register, settings):
    s = await register()
    claims = decode_token(s.access, settings)
    assert claims["sub"] == s.user_id and claims["org"] == s.org_id
    assert claims["role"] == "org_admin" and {"exp", "iat", "jti"} <= claims.keys()


async def test_bad_credentials_and_duplicates(client, register):
    s = await register()
    r = await client.post("/api/v1/auth/login", json={"email": s.email, "password": "wrong-password"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"
    r = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": s.email, "password": PASSWORD, "display_name": "Dup"},
    )
    assert r.status_code == 409 and r.json()["code"] == "email_taken"
    r = await client.post(
        "/api/v1/auth/register", json={"email": "not-an-email", "password": "short", "display_name": ""}
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_me_requires_token(client):
    assert (await client.get("/api/v1/me")).status_code == 401
    r = await client.get("/api/v1/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_token"


async def test_refresh_rotation_and_reuse_detection(client, register, admin_pool):
    s = await register()
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": s.refresh})
    assert r1.status_code == 200
    second = r1.json()["refresh_token"]
    assert second != s.refresh

    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": second})
    assert r2.status_code == 200
    third = r2.json()["refresh_token"]

    # replaying an already-rotated token is theft: reject it and kill the whole family
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": s.refresh})
    assert replay.status_code == 401 and replay.json()["code"] == "refresh_token_reused"
    for token in (second, third):
        r = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert r.status_code == 401, "family must be revoked after reuse"
    assert "auth.refresh_reuse_detected" in await audit_actions(admin_pool, s.org_id)

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": "slr1.garbage.token-value"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_refresh_token"


async def test_refresh_keeps_active_org_after_switch(client, register):
    owner = await register()
    other = await register()
    r = await client.post(
        "/api/v1/orgs/members",
        headers=owner.headers,
        json={"email": other.email, "role": "developer"},
    )
    assert r.status_code == 201
    r = await client.post("/api/v1/orgs/switch", headers=other.headers, json={"org_id": owner.org_id})
    assert r.status_code == 200
    switched = r.json()
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": switched["refresh_token"]})
    assert r.status_code == 200
    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["active_org_id"] == owner.org_id and me.json()["role"] == "developer"


async def test_logout_revokes_sessions(client, register):
    s = await register()
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": s.refresh})
    assert r.status_code == 204
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": s.refresh})
    assert r.status_code == 401

    s2 = await register()
    assert (await client.post("/api/v1/auth/logout")).status_code == 401
    assert (await client.post("/api/v1/auth/logout", headers=s2.headers)).status_code == 204
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": s2.refresh})
    assert r.status_code == 401


async def test_org_switch_requires_membership(client, register):
    a, b = await register(), await register()
    r = await client.post("/api/v1/orgs/switch", headers=b.headers, json={"org_id": a.org_id})
    assert r.status_code == 403 and r.json()["code"] == "not_a_member"
    r = await client.post("/api/v1/orgs/switch", headers=b.headers, json={"org_id": b.org_id})
    assert r.status_code == 200


async def test_members_rbac_and_last_admin_guard(client, register):
    owner, dev = await register(), await register()
    r = await client.post(
        "/api/v1/orgs/members", headers=owner.headers, json={"email": dev.email, "role": "developer"}
    )
    assert r.status_code == 201 and r.json()["role"] == "developer"
    dup = await client.post(
        "/api/v1/orgs/members", headers=owner.headers, json={"email": dev.email, "role": "viewer"}
    )
    assert dup.status_code == 409

    # the developer, once switched into the org, cannot administer members
    sw = (await client.post("/api/v1/orgs/switch", headers=dev.headers, json={"org_id": owner.org_id})).json()
    dev_h = {"Authorization": f"Bearer {sw['access_token']}"}
    assert (await client.get("/api/v1/orgs/members", headers=dev_h)).status_code == 403

    listing = (await client.get("/api/v1/orgs/members", headers=owner.headers)).json()
    assert {m["email"] for m in listing["items"]} == {owner.email, dev.email}

    r = await client.patch(
        f"/api/v1/orgs/members/{dev.user_id}", headers=owner.headers, json={"role": "ml_engineer"}
    )
    assert r.json()["role"] == "ml_engineer"
    r = await client.patch(
        f"/api/v1/orgs/members/{owner.user_id}", headers=owner.headers, json={"role": "viewer"}
    )
    assert r.status_code == 409 and r.json()["code"] == "last_admin"
    assert (await client.delete(f"/api/v1/orgs/members/{owner.user_id}", headers=owner.headers)).status_code == 409
    assert (await client.delete(f"/api/v1/orgs/members/{dev.user_id}", headers=owner.headers)).status_code == 204
    assert (await client.delete(f"/api/v1/orgs/members/{dev.user_id}", headers=owner.headers)).status_code == 404


async def test_invited_unknown_email_is_provisioned_without_password(client, register):
    owner = await register()
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post(
        "/api/v1/orgs/members", headers=owner.headers, json={"email": email, "role": "viewer"}
    )
    assert r.status_code == 201 and r.json()["display_name"] == email.split("@")[0]
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "whatever-password"})
    assert r.status_code == 401  # no password: must sign in through SSO
    r = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "display_name": "Squatter"}
    )
    assert r.status_code == 409  # an invite cannot be hijacked by self-registration
