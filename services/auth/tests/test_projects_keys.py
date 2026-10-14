import uuid
from datetime import UTC, datetime, timedelta

from slm_common.security import hash_api_key, looks_like_api_key


async def add_member(client, owner, other, role):
    r = await client.post(
        "/api/v1/orgs/members", headers=owner.headers, json={"email": other.email, "role": role}
    )
    assert r.status_code == 201, r.text
    sw = await client.post("/api/v1/orgs/switch", headers=other.headers, json={"org_id": owner.org_id})
    return {"Authorization": f"Bearer {sw.json()['access_token']}"}


async def mk_project(client, headers, name="Chatbot"):
    r = await client.post("/api/v1/projects", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def test_project_crud_rbac_and_audit(client, register, admin_pool):
    owner, dev, viewer = await register(), await register(), await register()
    dev_h = await add_member(client, owner, dev, "developer")
    viewer_h = await add_member(client, owner, viewer, "viewer")

    p = await mk_project(client, owner.headers, "Support Bot")
    assert p["slug"] == "support-bot" and p["org_id"] == owner.org_id
    dup = await client.post("/api/v1/projects", headers=owner.headers, json={"name": "Support Bot"})
    assert dup.status_code == 409 and dup.json()["code"] == "slug_taken"

    assert (await client.post("/api/v1/projects", headers=dev_h, json={"name": "x"})).status_code == 403
    assert (await client.get(f"/api/v1/projects/{p['id']}", headers=viewer_h)).status_code == 200
    r = await client.patch(
        f"/api/v1/projects/{p['id']}", headers=owner.headers, json={"description": "tier-1 support"}
    )
    assert r.json()["description"] == "tier-1 support"
    assert (await client.patch(f"/api/v1/projects/{p['id']}", headers=owner.headers, json={})).status_code == 400
    assert (await client.delete(f"/api/v1/projects/{p['id']}", headers=dev_h)).status_code == 403
    assert (await client.delete(f"/api/v1/projects/{p['id']}", headers=owner.headers)).status_code == 204
    assert (await client.get(f"/api/v1/projects/{p['id']}", headers=owner.headers)).status_code == 404
    again = await client.post("/api/v1/projects", headers=owner.headers, json={"name": "Support Bot"})
    assert again.status_code == 201  # slug was released

    actions = [
        r["action"]
        for r in await admin_pool.fetch(
            "SELECT action FROM audit_logs WHERE org_id = $1 AND resource = 'project' ORDER BY id",
            uuid.UUID(owner.org_id),
        )
    ]
    assert actions == ["project.create", "project.update", "project.delete", "project.create"]


async def test_project_pagination(client, register):
    owner = await register()
    for i in range(5):
        await mk_project(client, owner.headers, f"proj {i}")
    seen, cursor = [], None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = (await client.get("/api/v1/projects", headers=owner.headers, params=params)).json()
        seen += [p["name"] for p in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == [f"proj {i}" for i in reversed(range(5))]


async def test_tenant_isolation_for_projects_and_keys(client, register):
    a, b = await register(), await register()
    pa = await mk_project(client, a.headers)
    key = (await client.post(
        "/api/v1/api-keys", headers=a.headers,
        json={"name": "k", "scope": "project", "project_id": pa["id"]},
    )).json()
    assert (await client.get(f"/api/v1/projects/{pa['id']}", headers=b.headers)).status_code == 404
    assert (await client.delete(f"/api/v1/api-keys/{key['id']}", headers=b.headers)).status_code == 404
    assert (await client.get("/api/v1/projects", headers=b.headers)).json()["items"] == []
    r = await client.post(
        "/api/v1/api-keys", headers=b.headers,
        json={"name": "k", "scope": "project", "project_id": pa["id"]},
    )
    assert r.status_code == 404  # cannot mint a key against another org's project


async def test_api_key_lifecycle(client, register, admin_pool):
    owner, dev = await register(), await register()
    dev_h = await add_member(client, owner, dev, "developer")
    project = await mk_project(client, owner.headers)

    r = await client.post(
        "/api/v1/api-keys", headers=dev_h,
        json={"name": "ci", "scope": "project", "project_id": project["id"], "rate_limit_rpm": 30},
    )
    assert r.status_code == 201
    created = r.json()
    plaintext = created["key"]
    assert looks_like_api_key(plaintext) and created["prefix"] == plaintext[:12]
    row = await admin_pool.fetchrow("SELECT key_hash FROM api_keys WHERE id = $1", uuid.UUID(created["id"]))
    assert bytes(row["key_hash"]) == hash_api_key(plaintext)

    listing = (await client.get("/api/v1/api-keys", headers=dev_h)).json()
    assert [k["id"] for k in listing["items"]] == [created["id"]]
    assert "key" not in listing["items"][0] and plaintext not in str(listing)

    # developers cannot mint org-level keys; org_admin can
    r = await client.post("/api/v1/api-keys", headers=dev_h, json={"name": "org", "scope": "organization"})
    assert r.status_code == 403
    r = await client.post("/api/v1/api-keys", headers=owner.headers, json={"name": "org", "scope": "organization"})
    assert r.status_code == 201 and r.json()["project_id"] is None
    org_key = r.json()
    assert (await client.delete(f"/api/v1/api-keys/{org_key['id']}", headers=dev_h)).status_code in (403, 404)

    # rotation: successor issued, old key lives on only for the grace period
    r = await client.post(f"/api/v1/api-keys/{created['id']}/rotate", headers=dev_h, json={"grace_seconds": 600})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["key"] != plaintext and new["rotated_from"] == created["id"]
    assert new["project_id"] == project["id"] and new["rate_limit_rpm"] == 30
    grace_until = datetime.fromisoformat(new["previous_key_grace_until"])
    assert timedelta(seconds=500) < grace_until - datetime.now(UTC) <= timedelta(seconds=600)
    old = (await client.get("/api/v1/api-keys", headers=owner.headers)).json()["items"]
    statuses = {k["id"]: k["status"] for k in old}
    assert statuses[created["id"]] == "grace" and statuses[new["id"]] == "active"
    again = await client.post(f"/api/v1/api-keys/{created['id']}/rotate", headers=dev_h)
    assert again.status_code == 409 and again.json()["code"] == "already_rotated"

    # revoke
    assert (await client.delete(f"/api/v1/api-keys/{new['id']}", headers=dev_h)).status_code == 204
    assert (await client.delete(f"/api/v1/api-keys/{new['id']}", headers=dev_h)).status_code == 204  # idempotent
    revoked = await admin_pool.fetchval("SELECT revoked_at FROM api_keys WHERE id = $1", uuid.UUID(new["id"]))
    assert revoked is not None
    r = await client.post(f"/api/v1/api-keys/{new['id']}/rotate", headers=dev_h)
    assert r.status_code == 409 and r.json()["code"] == "key_not_active"

    actions = [
        r["action"]
        for r in await admin_pool.fetch(
            "SELECT action FROM audit_logs WHERE org_id = $1 AND resource = 'api_key' ORDER BY id",
            uuid.UUID(owner.org_id),
        )
    ]
    assert actions[:2] == ["api_key.create", "api_key.create"]
    assert "api_key.rotate" in actions and "api_key.revoke" in actions


async def test_api_key_validation(client, register):
    owner = await register()
    p = await mk_project(client, owner.headers)
    bad = [
        {"name": "k", "scope": "project"},  # missing project
        {"name": "k", "scope": "organization", "project_id": p["id"]},
        {"name": "k", "scope": "project", "project_id": p["id"], "expires_at": "2001-01-01T00:00:00Z"},
        {"name": "k", "scope": "project", "project_id": p["id"], "rate_limit_rpm": 0},
    ]
    for payload in bad:
        r = await client.post("/api/v1/api-keys", headers=owner.headers, json=payload)
        assert r.status_code == 422, payload


async def test_api_key_usage(client, register, admin_pool):
    owner = await register()
    p = await mk_project(client, owner.headers)
    key = (await client.post(
        "/api/v1/api-keys", headers=owner.headers,
        json={"name": "k", "scope": "project", "project_id": p["id"]},
    )).json()
    org, kid = uuid.UUID(owner.org_id), uuid.UUID(key["id"])
    for status, prompt, completion, latency in [(200, 10, 20, 100), (200, 5, 5, 300), (500, 1, 0, 50)]:
        await admin_pool.execute(
            "INSERT INTO usage_logs (org_id, api_key_id, request_id, status_code, prompt_tokens, "
            "completion_tokens, latency_ms) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            org, kid, uuid.uuid4().hex, status, prompt, completion, latency,
        )
    r = await client.get(f"/api/v1/api-keys/{key['id']}/usage", headers=owner.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["requests"], body["prompt_tokens"], body["completion_tokens"], body["errors"]) == (3, 16, 25, 1)
    assert body["avg_latency_ms"] == 150 and len(body["daily"]) == 1 and body["daily"][0]["requests"] == 3
    assert {"from", "to"} <= body.keys()

    bad = await client.get(
        f"/api/v1/api-keys/{key['id']}/usage", headers=owner.headers,
        params={"from": "2030-01-02T00:00:00Z", "to": "2030-01-01T00:00:00Z"},
    )
    assert bad.status_code == 400
    assert (await client.get(f"/api/v1/api-keys/{uuid.uuid4()}/usage", headers=owner.headers)).status_code == 404
