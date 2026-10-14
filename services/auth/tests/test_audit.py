async def test_audit_log_listing_is_org_scoped_filtered_and_admin_only(client, register):
    owner, other = await register(), await register()
    project = (await client.post("/api/v1/projects", headers=owner.headers, json={"name": "Alpha"})).json()
    key = await client.post(
        "/api/v1/api-keys",
        headers=owner.headers,
        json={"name": "k", "scope": "project", "project_id": project["id"]},
    )
    assert key.status_code == 201
    await client.post("/api/v1/projects", headers=other.headers, json={"name": "Elsewhere"})

    r = await client.get("/api/v1/audit-logs", headers=owner.headers)
    assert r.status_code == 200
    items = r.json()["items"]
    actions = [i["action"] for i in items]
    assert actions[:2] == ["api_key.create", "project.create"] and "auth.register" in actions
    assert {i["org_id"] for i in items} == {owner.org_id}  # never the other tenant's rows
    assert items[0]["detail"]["name"] == "k" and items[0]["actor_id"] == owner.user_id

    only_keys = (await client.get("/api/v1/audit-logs", headers=owner.headers, params={"action": "api_key."})).json()
    assert [i["action"] for i in only_keys["items"]] == ["api_key.create"]
    exact = (await client.get("/api/v1/audit-logs", headers=owner.headers, params={"action": "project.create"})).json()
    assert len(exact["items"]) == 1
    by_resource = (await client.get("/api/v1/audit-logs", headers=owner.headers, params={"resource": "project"})).json()
    assert {i["resource"] for i in by_resource["items"]} == {"project"}

    page1 = (await client.get("/api/v1/audit-logs", headers=owner.headers, params={"limit": 2})).json()
    page2 = (await client.get("/api/v1/audit-logs", headers=owner.headers,
                              params={"limit": 2, "cursor": page1["next_cursor"]})).json()
    assert page1["items"][-1]["id"] > page2["items"][0]["id"]

    # members below org_admin cannot read the log
    await client.post("/api/v1/orgs/members", headers=owner.headers, json={"email": other.email, "role": "developer"})
    sw = (await client.post("/api/v1/orgs/switch", headers=other.headers, json={"org_id": owner.org_id})).json()
    r = await client.get("/api/v1/audit-logs", headers={"Authorization": f"Bearer {sw['access_token']}"})
    assert r.status_code == 403
    assert (await client.get("/api/v1/audit-logs")).status_code == 401
