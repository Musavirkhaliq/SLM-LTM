import json

from gw_helpers import CACHED_TOKENS, COMPLETION_TOKENS, PROMPT_TOKENS, bearer, chat


async def setup_basic(make_tenant, register_upstream, *, alias="support-bot", **tenant_kwargs):
    tenant = await make_tenant(**tenant_kwargs)
    fake = register_upstream("vllm-a")
    dep = await tenant.add_deployment("dep-a", "http://vllm-a:8000")
    await tenant.add_endpoint(alias, [(dep, 100, "stable")])
    key, key_id = await tenant.add_key(project=True)
    return tenant, fake, key, key_id


async def test_chat_completion_roundtrip_and_usage_log(client, make_tenant, register_upstream, flush):
    tenant, fake, key, key_id = await setup_basic(make_tenant, register_upstream)

    r = await client.post(
        "/v1/chat/completions", json=chat("support-bot", temperature=0.2), headers=bearer(key) | {"X-Request-ID": "req-42"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "hello from vllm-a"
    assert r.headers["x-request-id"] == "req-42"
    assert r.headers["x-slm-route"] == "stable"
    assert r.headers["x-ratelimit-limit-requests"] == "100000"
    assert int(r.headers["x-ratelimit-remaining-requests"]) < 100000
    assert "x-ratelimit-limit-tokens" in r.headers and "x-ratelimit-reset-requests" in r.headers

    sent = fake.calls[0]
    assert sent["body"]["model"] == "dep-a"  # alias rewritten to the deployment's served name
    assert sent["body"]["temperature"] == 0.2  # unknown params pass through
    assert "authorization" not in sent["headers"]  # customer key never reaches the model server
    assert sent["headers"]["x-request-id"] == "req-42"

    await flush()
    [row] = await tenant.usage_rows()
    assert row["api_key_id"] == key_id and row["request_id"] == "req-42"
    assert (row["prompt_tokens"], row["completion_tokens"], row["cached_tokens"]) == (
        PROMPT_TOKENS, COMPLETION_TOKENS, CACHED_TOKENS,
    )
    assert row["status_code"] == 200 and row["endpoint_alias"] == "support-bot"
    assert row["route_label"] == "stable" and row["deployment_id"] is not None
    assert row["latency_ms"] is not None and row["project_id"] is not None
    last_used = await tenant.pool.fetchval("SELECT last_used_at FROM api_keys WHERE id = $1", key_id)
    assert last_used is not None


async def test_request_id_is_generated_when_absent(client, make_tenant, register_upstream):
    _, _, key, _ = await setup_basic(make_tenant, register_upstream)
    r = await client.post("/v1/chat/completions", json=chat("support-bot"), headers=bearer(key))
    assert len(r.headers["x-request-id"]) >= 16


async def test_completions_and_embeddings(client, make_tenant, register_upstream, flush):
    tenant, fake, key, _ = await setup_basic(make_tenant, register_upstream, alias="mini")
    r = await client.post("/v1/completions", json={"model": "mini", "prompt": "hi"}, headers=bearer(key))
    assert r.status_code == 200 and r.json()["choices"][0]["text"] == "hello from vllm-a"
    r = await client.post("/v1/embeddings", json={"model": "mini", "input": ["a", "b"]}, headers=bearer(key))
    assert r.status_code == 200 and r.json()["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert [c["path"] for c in fake.calls] == ["/v1/completions", "/v1/embeddings"]
    await flush()
    rows = await tenant.usage_rows()
    assert [(r["prompt_tokens"], r["completion_tokens"]) for r in rows] == [(11, 7), (5, 0)]


async def test_streaming_is_passed_through_and_billed_from_upstream_usage(
    client, make_tenant, register_upstream, flush
):
    tenant, fake, key, _ = await setup_basic(make_tenant, register_upstream)
    async with client.stream(
        "POST", "/v1/chat/completions", json=chat("support-bot", stream=True), headers=bearer(key)
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-slm-route"] == "stable" and r.headers["cache-control"] == "no-cache"
        body = b"".join([chunk async for chunk in r.aiter_bytes()]).decode()

    events = [e for e in body.split("\n\n") if e]
    assert events[-1] == "data: [DONE]"
    chunks = [json.loads(e[6:]) for e in events[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text == "hello vllm-a"
    # the gateway asked vLLM for usage, but the client did not -> that chunk is withheld
    assert fake.calls[0]["body"]["stream_options"] == {"include_usage": True}
    assert all(c["choices"] for c in chunks) and not any("usage" in c for c in chunks)

    await flush()
    [row] = await tenant.usage_rows()
    assert (row["prompt_tokens"], row["completion_tokens"]) == (PROMPT_TOKENS, COMPLETION_TOKENS)
    assert row["ttft_ms"] is not None and row["latency_ms"] >= row["ttft_ms"]
    assert row["status_code"] == 200


async def test_streaming_with_client_requested_usage_forwards_usage_chunk(
    client, make_tenant, register_upstream, flush
):
    tenant, fake, key, _ = await setup_basic(make_tenant, register_upstream)
    payload = chat("support-bot", stream=True, stream_options={"include_usage": True})
    r = await client.post("/v1/chat/completions", json=payload, headers=bearer(key))
    events = [json.loads(e[6:]) for e in r.text.split("\n\n") if e.startswith("data: {")]
    assert events[-1]["usage"]["completion_tokens"] == COMPLETION_TOKENS and events[-1]["choices"] == []


async def test_client_disconnect_still_records_usage(client, make_tenant, register_upstream, flush):
    tenant, fake, key, _ = await setup_basic(make_tenant, register_upstream)
    async with client.stream(
        "POST", "/v1/chat/completions", json=chat("support-bot", stream=True), headers=bearer(key)
    ) as r:
        async for _chunk in r.aiter_bytes():
            break  # hang up after the first bytes
    await flush()
    rows = await tenant.usage_rows()
    assert len(rows) == 1


async def test_upstream_errors_pass_through_and_are_logged(
    client, make_tenant, register_upstream, flush
):
    tenant, fake, key, _ = await setup_basic(make_tenant, register_upstream)
    fake.fail_status = 503
    r = await client.post("/v1/chat/completions", json=chat("support-bot"), headers=bearer(key))
    assert r.status_code == 503 and r.json()["error"]["code"] == "boom"
    await flush()
    [row] = await tenant.usage_rows()
    assert row["status_code"] == 503 and row["prompt_tokens"] == 0


async def test_request_validation_errors_are_openai_shaped(client, make_tenant, register_upstream, settings):
    _, _, key, _ = await setup_basic(make_tenant, register_upstream)
    r = await client.post("/v1/chat/completions", json={"model": "support-bot"}, headers=bearer(key))
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error" and r.json()["error"]["param"] == "messages"
    r = await client.post("/v1/chat/completions", content=b"{nope", headers=bearer(key))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_json"
    r = await client.post("/v1/chat/completions", json=[1, 2], headers=bearer(key))
    assert r.status_code == 400
    big = chat("support-bot", pad="x" * (settings.max_request_bytes + 10))
    r = await client.post("/v1/chat/completions", json=big, headers=bearer(key))
    assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large"


async def test_models_listing(client, make_tenant, register_upstream):
    tenant, _, key, _ = await setup_basic(make_tenant, register_upstream, alias="alpha")
    stopped = await tenant.add_deployment("dep-stopped", "http://x", status="stopped")
    await tenant.add_endpoint("dead", [(stopped, 100, None)])
    other = await make_tenant()
    dep = await other.add_deployment("o-dep", "http://vllm-a:8000")
    await other.add_endpoint("other-org-alias", [(dep, 100, None)])
    await other.add_endpoint("global-shared", [(dep, 100, None)], kind="shared")

    r = await client.get("/v1/models", headers=bearer(key))
    assert r.status_code == 200 and r.json()["object"] == "list"
    ids = {m["id"]: m["owned_by"] for m in r.json()["data"]}
    assert ids == {"alpha": "organization", "global-shared": "slm-studio"}
    assert (await client.get("/v1/models")).status_code == 401
