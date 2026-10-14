import uuid


def version_body(**overrides) -> dict:
    body = {
        "format": "safetensors",
        "param_count": 3_800_000_000,
        "quantization": "none",
        "context_length": 4096,
        "artifact_uri": "s3://models/phi-4-mini/v1",
        "benchmark_scores": {"accuracy": 0.7},
    }
    return body | overrides


def model_body(name: str | None = None, **overrides) -> dict:
    return {"name": name or f"m-{uuid.uuid4().hex[:8]}", "source": "upload", "family": "phi"} | overrides


async def create_model(client, actor, **overrides) -> dict:
    r = await client.post("/api/v1/models", headers=actor.headers, json=model_body(**overrides))
    assert r.status_code == 201, r.text
    return r.json()


async def create_version(client, actor, model_id, **overrides) -> dict:
    r = await client.post(
        f"/api/v1/models/{model_id}/versions", headers=actor.headers, json=version_body(**overrides)
    )
    assert r.status_code == 201, r.text
    return r.json()


async def actions(admin_pool, resource: str, org_id=None) -> list[str]:
    rows = await admin_pool.fetch(
        "SELECT action FROM audit_logs WHERE resource = $1 AND org_id IS NOT DISTINCT FROM $2 ORDER BY id",
        resource,
        org_id,
    )
    return [r["action"] for r in rows]


async def test_model_crud_and_rbac(client, make_actor, admin_pool):
    eng = await make_actor("ml_engineer")
    viewer = await make_actor("viewer", eng.org_id)
    dev = await make_actor("developer", eng.org_id)

    model = await create_model(client, eng, name="my-phi")
    assert model["scope"] == "org" and model["org_id"] == str(eng.org_id)
    assert model["version_count"] == 0 and model["active_version"] is None

    for actor in (viewer, dev):
        r = await client.post("/api/v1/models", headers=actor.headers, json=model_body())
        assert r.status_code == 403
    assert (await client.get(f"/api/v1/models/{model['id']}", headers=viewer.headers)).status_code == 200
    assert (await client.post("/api/v1/models", json=model_body())).status_code == 401

    dup = await client.post("/api/v1/models", headers=eng.headers, json=model_body("my-phi"))
    assert dup.status_code == 409 and dup.json()["code"] == "model_name_taken"
    bad = await client.post("/api/v1/models", headers=eng.headers, json={"name": "bad name!", "source": "x"})
    assert bad.status_code == 422

    r = await client.patch(
        f"/api/v1/models/{model['id']}", headers=eng.headers, json={"description": "tuned phi"}
    )
    assert r.json()["description"] == "tuned phi"
    assert (await client.patch(f"/api/v1/models/{model['id']}", headers=eng.headers, json={})).status_code == 400
    assert (await client.patch(f"/api/v1/models/{model['id']}", headers=viewer.headers, json={"family": "x"})).status_code == 403
    assert await actions(admin_pool, "model", eng.org_id) == ["model.create", "model.update"]


async def test_catalogue_visibility_and_tenant_isolation(client, make_actor):
    a, b = await make_actor("ml_engineer"), await make_actor("ml_engineer")
    mine = await create_model(client, a, name="a-private")

    listing = (await client.get("/api/v1/models", headers=b.headers, params={"limit": 200})).json()
    names = {m["name"]: m for m in listing["items"]}
    assert "a-private" not in names
    assert {"phi-4", "phi-4-mini", "llama-3.1-8b"} <= names.keys()
    assert names["phi-4"]["scope"] == "global" and names["phi-4"]["org_id"] is None

    assert (await client.get(f"/api/v1/models/{mine['id']}", headers=b.headers)).status_code == 404
    assert (await client.get(f"/api/v1/models/{mine['id']}/versions", headers=b.headers)).status_code == 404
    assert (await client.post(f"/api/v1/models/{mine['id']}/archive", headers=b.headers)).status_code == 404

    only_org = (await client.get("/api/v1/models", headers=a.headers, params={"scope": "org"})).json()
    assert [m["name"] for m in only_org["items"]] == ["a-private"]
    only_global = (await client.get("/api/v1/models", headers=a.headers, params={"scope": "global", "q": "PHI"})).json()
    assert {m["name"] for m in only_global["items"]} == {"phi-4", "phi-4-mini"}


async def test_global_catalogue_writes_need_platform_admin(client, make_actor, admin_pool):
    eng = await make_actor("org_admin")
    admin = await make_actor("platform_admin")
    glob = (await client.get("/api/v1/models", headers=eng.headers, params={"q": "gemma"})).json()["items"][0]

    r = await client.post("/api/v1/models", headers=eng.headers, json=model_body(global_catalogue=True))
    assert r.status_code == 403
    for method, path, body in [
        ("PATCH", f"/api/v1/models/{glob['id']}", {"description": "hacked"}),
        ("POST", f"/api/v1/models/{glob['id']}/archive", None),
        ("POST", f"/api/v1/models/{glob['id']}/versions", version_body()),
    ]:
        r = await client.request(method, path, headers=eng.headers, json=body)
        assert r.status_code == 403, path

    name = f"shared-{uuid.uuid4().hex[:6]}"
    r = await client.post("/api/v1/models", headers=admin.headers, json=model_body(name, global_catalogue=True))
    assert r.status_code == 201 and r.json()["scope"] == "global"
    gid = r.json()["id"]
    v = await create_version(client, admin, gid)
    assert v["org_id"] is None and v["version"] == 1
    # everyone can read the new global model and its versions
    assert (await client.get(f"/api/v1/models/{gid}/versions", headers=eng.headers)).json()["items"][0]["id"] == v["id"]
    assert "model.create" in await actions(admin_pool, "model", None)


async def test_versions_numbering_activation_and_rollback(client, make_actor, admin_pool):
    eng = await make_actor("ml_engineer")
    m = await create_model(client, eng)
    v1 = await create_version(client, eng, m["id"], activate=True)
    v2 = await create_version(client, eng, m["id"], activate=True, artifact_uri="s3://models/x/v2")
    v3 = await create_version(client, eng, m["id"], artifact_uri="hf://org/model")
    assert [v["version"] for v in (v1, v2, v3)] == [1, 2, 3]
    assert [v["status"] for v in (v1, v2, v3)] == ["active", "active", "registered"]

    versions = (await client.get(f"/api/v1/models/{m['id']}/versions", headers=eng.headers)).json()["items"]
    assert {v["version"]: v["status"] for v in versions} == {1: "archived", 2: "active", 3: "registered"}
    detail = (await client.get(f"/api/v1/models/{m['id']}", headers=eng.headers)).json()
    assert detail["active_version"] == 2 and detail["version_count"] == 3

    # rollback only to an older version
    r = await client.post(f"/api/v1/models/{m['id']}/versions/3/rollback", headers=eng.headers)
    assert r.status_code == 409 and r.json()["code"] == "not_an_older_version"
    r = await client.post(f"/api/v1/models/{m['id']}/versions/2/rollback", headers=eng.headers)
    assert r.status_code == 409 and r.json()["code"] == "already_active"
    r = await client.post(f"/api/v1/models/{m['id']}/versions/9/rollback", headers=eng.headers)
    assert r.status_code == 404
    r = await client.post(f"/api/v1/models/{m['id']}/versions/1/rollback", headers=eng.headers)
    assert r.status_code == 200 and r.json()["status"] == "active"
    versions = (await client.get(f"/api/v1/models/{m['id']}/versions", headers=eng.headers)).json()["items"]
    assert {v["version"]: v["status"] for v in versions} == {1: "active", 2: "archived", 3: "registered"}
    assert (await client.post(f"/api/v1/models/{m['id']}/versions/1/rollback", headers=(await make_actor("developer", eng.org_id)).headers)).status_code == 403
    assert "model_version.rollback" in await actions(admin_pool, "model_version", eng.org_id)


async def test_only_one_active_version_is_enforced_on_status_patch(client, make_actor):
    eng = await make_actor()
    m = await create_model(client, eng)
    await create_version(client, eng, m["id"], activate=True)
    await create_version(client, eng, m["id"])
    r = await client.patch(f"/api/v1/models/{m['id']}/versions/2", headers=eng.headers, json={"status": "active"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    versions = (await client.get(f"/api/v1/models/{m['id']}/versions", headers=eng.headers)).json()["items"]
    assert sorted(v["status"] for v in versions) == ["active", "archived"]
    bad = await client.patch(f"/api/v1/models/{m['id']}/versions/2", headers=eng.headers, json={"status": "registered"})
    assert bad.status_code == 409 and bad.json()["code"] == "invalid_status_transition"


async def test_benchmark_scores_merge(client, make_actor, admin_pool):
    eng = await make_actor()
    m = await create_model(client, eng)
    await create_version(client, eng, m["id"])
    r = await client.patch(
        f"/api/v1/models/{m['id']}/versions/1",
        headers=eng.headers,
        json={"benchmark_scores": {"rouge_l": 0.41, "accuracy": 0.82}},
    )
    assert r.status_code == 200
    assert r.json()["benchmark_scores"] == {"accuracy": 0.82, "rouge_l": 0.41}
    bad = await client.patch(
        f"/api/v1/models/{m['id']}/versions/1", headers=eng.headers, json={"benchmark_scores": {"a": "high"}}
    )
    assert bad.status_code == 422
    assert (await client.patch(f"/api/v1/models/{m['id']}/versions/1", headers=eng.headers, json={})).status_code == 400
    assert (await client.patch(f"/api/v1/models/{m['id']}/versions/7", headers=eng.headers, json={"status": "active"})).status_code == 404


async def test_version_validation_and_lineage(client, make_actor):
    eng, other = await make_actor(), await make_actor()
    m = await create_model(client, eng)
    base = await create_version(client, eng, m["id"])
    ok = await create_version(client, eng, m["id"], base_version_id=base["id"])
    assert ok["base_version_id"] == base["id"]

    other_model = await create_model(client, other)
    foreign = await create_version(client, other, other_model["id"])
    for body in (
        version_body(artifact_uri="ftp://nope"),
        version_body(format="onnx"),
        version_body(artifact_sha256="short"),
        version_body(param_count=-1),
    ):
        r = await client.post(f"/api/v1/models/{m['id']}/versions", headers=eng.headers, json=body)
        assert r.status_code == 422, body
    r = await client.post(
        f"/api/v1/models/{m['id']}/versions", headers=eng.headers,
        json=version_body(base_version_id=foreign["id"]),
    )
    assert r.status_code == 404  # other tenant's version is invisible
    r = await client.post(
        f"/api/v1/models/{m['id']}/versions", headers=eng.headers,
        json=version_body(training_job_id=str(uuid.uuid4())),
    )
    assert r.status_code == 404


async def test_archive_blocks_further_writes_and_deployed_models(client, make_actor, admin_pool):
    eng = await make_actor("org_admin")
    m = await create_model(client, eng)
    v = await create_version(client, eng, m["id"], activate=True)
    dep = uuid.uuid4()
    await admin_pool.execute(
        "INSERT INTO deployments (id, org_id, name, model_version_id, status) VALUES ($1, $2, 'd1', $3, 'running')",
        dep, eng.org_id, uuid.UUID(v["id"]),
    )
    r = await client.post(f"/api/v1/models/{m['id']}/archive", headers=eng.headers)
    assert r.status_code == 409 and r.json()["code"] == "model_in_use"

    await admin_pool.execute("UPDATE deployments SET status = 'stopped' WHERE id = $1", dep)
    r = await client.post(f"/api/v1/models/{m['id']}/archive", headers=eng.headers)
    assert r.status_code == 200 and r.json()["archived_at"] is not None
    assert (await client.post(f"/api/v1/models/{m['id']}/archive", headers=eng.headers)).status_code == 200
    versions = (await client.get(f"/api/v1/models/{m['id']}/versions", headers=eng.headers)).json()["items"]
    assert [x["status"] for x in versions] == ["archived"]

    r = await client.post(f"/api/v1/models/{m['id']}/versions", headers=eng.headers, json=version_body())
    assert r.status_code == 409 and r.json()["code"] == "model_archived"
    r = await client.patch(f"/api/v1/models/{m['id']}", headers=eng.headers, json={"family": "x"})
    assert r.status_code == 409
    default = (await client.get("/api/v1/models", headers=eng.headers, params={"scope": "org"})).json()["items"]
    assert default == []
    with_archived = (await client.get("/api/v1/models", headers=eng.headers, params={"scope": "org", "include_archived": "true"})).json()["items"]
    assert [x["id"] for x in with_archived] == [m["id"]]
    assert await actions(admin_pool, "model", eng.org_id) == ["model.create", "model.archive"]


async def test_model_list_pagination(client, make_actor):
    eng = await make_actor()
    names = [(await create_model(client, eng, name=f"page-{i}"))["name"] for i in range(5)]
    seen, cursor = [], None
    while True:
        params = {"scope": "org", "limit": 2} | ({"cursor": cursor} if cursor else {})
        body = (await client.get("/api/v1/models", headers=eng.headers, params=params)).json()
        seen += [m["name"] for m in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == list(reversed(names))
