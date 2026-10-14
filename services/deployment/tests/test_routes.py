import uuid

import pytest


def deployment_body(model_version_id, **overrides) -> dict:
    body = {
        "name": "phi-prod",
        "model_version_id": str(model_version_id),
        "gpu_count": 2,
        "replicas_max": 3,
        "vllm_args": {
            "max_model_len": 8192,
            "enable_prefix_caching": True,
            "speculative_config": {"method": "ngram", "num_speculative_tokens": 4},
        },
    }
    return body | overrides


async def create_deployment(client, actor, model_version_id, **overrides) -> dict:
    r = await client.post(
        "/api/v1/deployments", headers=actor.headers, json=deployment_body(model_version_id, **overrides)
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_probe_endpoints(client):
    assert (await client.get("/healthz")).json() == {"status": "ok", "service": "deployment"}
    assert (await client.get("/readyz")).status_code == 200
    assert "slm_http_requests_total" in (await client.get("/metrics")).text


async def test_deployment_crud_and_rbac(client, make_actor, make_model_version):
    eng = await make_actor("ml_engineer")
    viewer = await make_actor("viewer", eng.org_id)
    dev = await make_actor("developer", eng.org_id)
    mv = await make_model_version(eng.org_id)

    assert (await client.get("/api/v1/deployments")).status_code == 401
    assert (await client.get("/api/v1/deployments", headers=viewer.headers)).json()["items"] == []
    for actor in (viewer, dev):
        r = await client.post("/api/v1/deployments", headers=actor.headers, json=deployment_body(mv))
        assert r.status_code == 403, actor.role

    d = await create_deployment(client, eng, mv, target="dgx_spark")
    assert d["status"] == "pending" and d["target"] == "dgx_spark"

    dup = await client.post("/api/v1/deployments", headers=eng.headers, json=deployment_body(mv))
    assert dup.status_code == 409 and dup.json()["code"] == "deployment_name_taken"

    missing_mv = await client.post(
        "/api/v1/deployments", headers=eng.headers, json=deployment_body(uuid.uuid4(), name="other")
    )
    assert missing_mv.status_code == 404

    got = await client.get(f"/api/v1/deployments/{d['id']}", headers=viewer.headers)
    assert got.status_code == 200 and got.json()["id"] == d["id"]
    assert (await client.get(f"/api/v1/deployments/{uuid.uuid4()}", headers=viewer.headers)).status_code == 404

    r = await client.patch(
        f"/api/v1/deployments/{d['id']}", headers=eng.headers, json={"strategy": "canary"}
    )
    assert r.status_code == 200 and r.json()["strategy"] == "canary"
    assert (await client.patch(f"/api/v1/deployments/{d['id']}", headers=eng.headers, json={})).status_code == 400
    assert (await client.patch(f"/api/v1/deployments/{d['id']}", headers=viewer.headers, json={"strategy": "direct"})).status_code == 403


async def test_tenant_isolation(client, make_actor, make_model_version):
    a, b = await make_actor("ml_engineer"), await make_actor("ml_engineer")
    mv = await make_model_version(a.org_id)
    d = await create_deployment(client, a, mv)
    assert (await client.get(f"/api/v1/deployments/{d['id']}", headers=b.headers)).status_code == 404
    assert (await client.get("/api/v1/deployments", headers=b.headers)).json()["items"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"name": "Bad Name"},
        {"model_version_id": "nope"},
        {"gpu_count": 0},
        {"replicas_min": 5, "replicas_max": 2},
        {"strategy": "yolo"},
        {"vllm_args": {"gpu_memory_utilization": 2}},
        {"vllm_args": {"speculative_config": {"num_speculative_tokens": 2}}},
        {"vllm_args": {"surprise": 1}},
        {"target": "kubernetes", "internal_url": "http://x:8000"},
    ],
)
async def test_deployment_body_validation(client, make_actor, mutation):
    eng = await make_actor("ml_engineer")
    body = deployment_body(uuid.uuid4()) | mutation
    r = await client.post("/api/v1/deployments", headers=eng.headers, json=body)
    assert r.status_code == 422, mutation
    assert r.json()["code"] == "validation_error"


async def test_start_stop_lifecycle(client, make_actor, make_model_version):
    eng = await make_actor("ml_engineer")
    mv = await make_model_version(eng.org_id)
    d = await create_deployment(client, eng, mv)

    no_url = await client.post(f"/api/v1/deployments/{d['id']}/start", headers=eng.headers)
    assert no_url.status_code == 409 and no_url.json()["code"] == "internal_url_required"

    patched = await client.patch(
        f"/api/v1/deployments/{d['id']}", headers=eng.headers, json={"internal_url": "http://vllm-stub:8000"}
    )
    assert patched.status_code == 200 and patched.json()["internal_url"] == "http://vllm-stub:8000"

    started = await client.post(f"/api/v1/deployments/{d['id']}/start", headers=eng.headers)
    assert started.status_code == 200 and started.json()["status"] == "running"

    again = await client.post(f"/api/v1/deployments/{d['id']}/start", headers=eng.headers)
    assert again.status_code == 409 and again.json()["code"] == "invalid_state"

    stopped = await client.post(f"/api/v1/deployments/{d['id']}/stop", headers=eng.headers)
    assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
    # stop is idempotent
    assert (await client.post(f"/api/v1/deployments/{d['id']}/stop", headers=eng.headers)).json()["status"] == "stopped"

    deleted = await client.delete(f"/api/v1/deployments/{d['id']}", headers=eng.headers)
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/deployments/{d['id']}", headers=eng.headers)).status_code == 404


async def test_start_kubernetes_target_is_not_implemented(client, make_actor, make_model_version):
    eng = await make_actor("ml_engineer")
    mv = await make_model_version(eng.org_id)
    d = await create_deployment(client, eng, mv, target="kubernetes")
    r = await client.post(f"/api/v1/deployments/{d['id']}/start", headers=eng.headers)
    assert r.status_code == 501 and r.json()["code"] == "not_implemented"


async def test_delete_blocked_while_running(client, make_actor, make_model_version):
    eng = await make_actor("ml_engineer")
    mv = await make_model_version(eng.org_id)
    d = await create_deployment(client, eng, mv, internal_url="http://vllm-stub:8000")
    await client.post(f"/api/v1/deployments/{d['id']}/start", headers=eng.headers)
    r = await client.delete(f"/api/v1/deployments/{d['id']}", headers=eng.headers)
    assert r.status_code == 409 and r.json()["code"] == "deployment_running"


async def test_scale(client, make_actor, make_model_version):
    eng = await make_actor("ml_engineer")
    mv = await make_model_version(eng.org_id)
    d = await create_deployment(client, eng, mv)
    r = await client.post(
        f"/api/v1/deployments/{d['id']}/scale", headers=eng.headers,
        json={"replicas_min": 2, "replicas_max": 5},
    )
    assert r.status_code == 200 and r.json()["replicas_min"] == 2 and r.json()["replicas_max"] == 5
    bad = await client.post(
        f"/api/v1/deployments/{d['id']}/scale", headers=eng.headers,
        json={"replicas_min": 5, "replicas_max": 2},
    )
    assert bad.status_code == 422


async def test_rollback_restores_previous_and_repoints_routes(client, make_actor, make_model_version, admin_pool):
    eng = await make_actor("ml_engineer")
    mv = await make_model_version(eng.org_id)
    old = await create_deployment(client, eng, mv, name="old", internal_url="http://vllm-stub:8000")
    await client.post(f"/api/v1/deployments/{old['id']}/start", headers=eng.headers)
    new = await create_deployment(client, eng, mv, name="new", internal_url="http://vllm:8000")
    await client.post(f"/api/v1/deployments/{new['id']}/start", headers=eng.headers)
    await admin_pool.execute(
        "UPDATE deployments SET previous_deployment_id = $1 WHERE id = $2", uuid.UUID(old["id"]), uuid.UUID(new["id"])
    )

    ep = await client.post("/api/v1/endpoints", headers=eng.headers, json={"alias": "support-bot"})
    assert ep.status_code == 201
    routes = await client.put(
        f"/api/v1/endpoints/{ep.json()['id']}/routes", headers=eng.headers,
        json={"routes": [{"deployment_id": new["id"], "weight": 100, "label": "stable"}]},
    )
    assert routes.status_code == 200

    r = await client.post(f"/api/v1/deployments/{new['id']}/rollback", headers=eng.headers)
    assert r.status_code == 200 and r.json()["status"] == "stopped"
    restored = (await client.get(f"/api/v1/deployments/{old['id']}", headers=eng.headers)).json()
    assert restored["status"] == "running"

    final_routes = (await client.get("/api/v1/endpoints", headers=eng.headers)).json()["items"][0]["routes"]
    assert [rt["deployment_id"] for rt in final_routes] == [old["id"]]

    no_prev = await client.post(f"/api/v1/deployments/{old['id']}/rollback", headers=eng.headers)
    assert no_prev.status_code == 409 and no_prev.json()["code"] == "no_rollback_target"


async def test_endpoints_and_routes(client, make_actor, make_model_version):
    eng = await make_actor("ml_engineer")
    mv = await make_model_version(eng.org_id)
    a = await create_deployment(client, eng, mv, name="a")
    b = await create_deployment(client, eng, mv, name="b")

    dup_alias = await client.post("/api/v1/endpoints", headers=eng.headers, json={"alias": "chat"})
    assert dup_alias.status_code == 201
    again = await client.post("/api/v1/endpoints", headers=eng.headers, json={"alias": "chat"})
    assert again.status_code == 409 and again.json()["code"] == "alias_taken"
    endpoint_id = dup_alias.json()["id"]

    missing_dep = await client.put(
        f"/api/v1/endpoints/{endpoint_id}/routes", headers=eng.headers,
        json={"routes": [{"deployment_id": str(uuid.uuid4()), "weight": 100}]},
    )
    assert missing_dep.status_code == 404

    ok = await client.put(
        f"/api/v1/endpoints/{endpoint_id}/routes", headers=eng.headers,
        json={"routes": [{"deployment_id": a["id"], "weight": 90, "label": "stable"}, {"deployment_id": b["id"], "weight": 10, "label": "canary"}]},
    )
    assert ok.status_code == 200
    assert {rt["deployment_id"]: rt["weight"] for rt in ok.json()["routes"]} == {a["id"]: 90, b["id"]: 10}

    for bad in (
        {"routes": [{"deployment_id": a["id"], "weight": 90}, {"deployment_id": b["id"], "weight": 20}]},
        {"routes": [{"deployment_id": a["id"], "weight": 50}, {"deployment_id": a["id"], "weight": 50}]},
        {"routes": [{"deployment_id": a["id"], "weight": 101}]},
        {"routes": []},
    ):
        r = await client.put(f"/api/v1/endpoints/{endpoint_id}/routes", headers=eng.headers, json=bad)
        assert r.status_code == 422, bad

