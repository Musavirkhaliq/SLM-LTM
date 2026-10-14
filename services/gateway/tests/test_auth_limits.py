import uuid

from gw_helpers import bearer, cache_key, chat, future, past

from slm_common.security import generate_api_key


async def serve(make_tenant, register_upstream, **tenant_kwargs):
    tenant = await make_tenant(**tenant_kwargs)
    register_upstream("vllm-a")
    dep = await tenant.add_deployment("dep-a", "http://vllm-a:8000")
    await tenant.add_endpoint("bot", [(dep, 100, "stable")])
    return tenant


async def call(client, key, **extra):
    return await client.post("/v1/chat/completions", json=chat("bot", **extra), headers=bearer(key))


async def test_bad_keys_get_openai_401(client, make_tenant, register_upstream):
    await serve(make_tenant, register_upstream)
    cases = [
        ({}, "missing_api_key"),
        ({"Authorization": "Basic abc"}, "missing_api_key"),
        ({"Authorization": "Bearer not-a-key"}, "invalid_api_key"),
        ({"Authorization": f"Bearer {generate_api_key()[0]}"}, "invalid_api_key"),
    ]
    for headers, code in cases:
        r = await client.post("/v1/chat/completions", json=chat("bot"), headers=headers)
        assert r.status_code == 401, headers
        body = r.json()
        assert body["error"]["code"] == code and body["error"]["type"] == "authentication_error"
        assert set(body["error"]) == {"message", "type", "param", "code"}
        assert r.headers["www-authenticate"] == "Bearer" and r.headers["x-request-id"]


async def test_revoked_expired_and_rotation_grace(client, make_tenant, register_upstream):
    tenant = await serve(make_tenant, register_upstream)
    revoked, _ = await tenant.add_key(revoked_at=past())
    expired, _ = await tenant.add_key(expires_at=past())
    in_grace, _ = await tenant.add_key(grace_until=future())
    grace_over, _ = await tenant.add_key(grace_until=past())
    fresh, _ = await tenant.add_key(expires_at=future())

    for key, code in [(revoked, "api_key_revoked"), (expired, "api_key_expired"), (grace_over, "api_key_expired")]:
        r = await call(client, key)
        assert r.status_code == 401 and r.json()["error"]["code"] == code, code
    assert "grace period" in (await call(client, grace_over)).json()["error"]["message"]
    assert (await call(client, in_grace)).status_code == 200
    assert (await call(client, fresh)).status_code == 200


async def test_key_cache_and_invalidation(client, make_tenant, register_upstream, store):
    tenant = await serve(make_tenant, register_upstream)
    key, key_id = await tenant.add_key()
    assert (await call(client, key)).status_code == 200
    assert await store.get(cache_key(key)) is not None  # cached after first lookup

    # revoked in the DB: honoured only once the cached entry is dropped (what auth does on revoke)
    await tenant.pool.execute("UPDATE api_keys SET revoked_at = now() WHERE id = $1", key_id)
    assert (await call(client, key)).status_code == 200
    await store.delete(cache_key(key))
    r = await call(client, key)
    assert r.status_code == 401 and r.json()["error"]["code"] == "api_key_revoked"


async def test_unknown_key_is_negatively_cached(client, make_tenant, register_upstream, store):
    await serve(make_tenant, register_upstream)
    ghost = generate_api_key()[0]
    assert (await call(client, ghost)).status_code == 401
    assert await store.get(cache_key(ghost)) == "null"


async def test_per_key_rpm_limit_returns_429(client, make_tenant, register_upstream, flush):
    tenant = await serve(make_tenant, register_upstream)
    key, _ = await tenant.add_key(rate_limit_rpm=3)
    other, _ = await tenant.add_key()
    for i in range(3):
        r = await call(client, key)
        assert r.status_code == 200
        assert r.headers["x-ratelimit-limit-requests"] == "3"
        assert r.headers["x-ratelimit-remaining-requests"] == str(2 - i)
    r = await call(client, key)
    assert r.status_code == 429
    assert r.json()["error"]["type"] == "rate_limit_error" and r.json()["error"]["code"] == "rate_limit_exceeded"
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["x-ratelimit-remaining-requests"] == "0"
    assert (await call(client, other)).status_code == 200  # other keys of the org are unaffected

    await flush()
    codes = [row["status_code"] for row in await tenant.usage_rows(api_key_id=(await tenant.pool.fetchval(
        "SELECT id FROM api_keys WHERE rate_limit_rpm = 3 AND org_id = $1", tenant.org_id)))]
    assert codes == [200, 200, 200, 429]


async def test_org_rpm_quota_is_shared_by_all_keys(client, make_tenant, register_upstream):
    tenant = await serve(make_tenant, register_upstream, rpm=4)
    k1, _ = await tenant.add_key()
    k2, _ = await tenant.add_key()
    statuses = [(await call(client, k)).status_code for k in (k1, k2, k1, k2, k1)]
    assert statuses == [200, 200, 200, 200, 429]


async def test_orgs_do_not_share_limits(client, make_tenant, register_upstream):
    a = await serve(make_tenant, register_upstream, rpm=1)
    b = await make_tenant(rpm=1)
    dep = await b.add_deployment("dep-b", "http://vllm-a:8000")
    await b.add_endpoint("bot", [(dep, 100, None)])
    ka, _ = await a.add_key()
    kb, _ = await b.add_key()
    assert (await call(client, ka)).status_code == 200
    assert (await call(client, ka)).status_code == 429
    assert (await call(client, kb)).status_code == 200


async def test_tpm_limit_admits_on_estimate_and_charges_actual(client, gw, make_tenant, register_upstream):
    # each request is charged 18 real tokens (11 + 7); the limit allows two of them
    tenant = await serve(make_tenant, register_upstream, tpm=40)
    key, _ = await tenant.add_key()
    r1 = await call(client, key, max_tokens=5)
    assert r1.status_code == 200 and r1.headers["x-ratelimit-limit-tokens"] == "40"
    await gw.state.gateway.drain()
    r2 = await call(client, key, max_tokens=5)
    assert r2.status_code == 200
    await gw.state.gateway.drain()
    r3 = await call(client, key, max_tokens=5)  # 36 used, +estimate > 40
    assert r3.status_code == 429 and "tokens" in r3.json()["error"]["message"]
    assert r3.headers["x-ratelimit-limit-tokens"] == "40" and int(r3.headers["retry-after"]) >= 1


async def test_monthly_token_quota(client, make_tenant, register_upstream, admin_pool):
    tenant = await serve(make_tenant, register_upstream, monthly=100)
    key, key_id = await tenant.add_key()
    await admin_pool.execute(
        "INSERT INTO usage_logs (org_id, api_key_id, request_id, status_code, prompt_tokens, completion_tokens) "
        "VALUES ($1, $2, $3, 200, 60, 40)", tenant.org_id, key_id, uuid.uuid4().hex,
    )
    r = await call(client, key)
    assert r.status_code == 429 and r.json()["error"]["code"] == "insufficient_quota"

    roomy = await serve(make_tenant, register_upstream, monthly=10_000)
    key2, key2_id = await roomy.add_key()
    assert (await call(client, key2)).status_code == 200
