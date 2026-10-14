import random
from collections import Counter
from uuid import uuid4

from gw_helpers import bearer, chat

from slm_gateway.routing import Resolved, Router, Target


def target(label: str, weight: int) -> Target:
    return Target(uuid4(), "alias", uuid4(), label, uuid4(), f"http://{label}", label, weight, label)


def test_pick_follows_weights_and_skips_excluded():
    router = Router(pool=None, rng=random.Random(7))  # type: ignore[arg-type]
    stable, canary = target("stable", 95), target("canary", 5)
    resolved = Resolved("alias", (stable, canary))
    counts = Counter(router.pick(resolved).label for _ in range(20_000))
    assert 0.04 < counts["canary"] / 20_000 < 0.06
    assert router.pick(resolved, frozenset({stable.deployment_id})) is canary
    assert router.pick(resolved, frozenset({stable.deployment_id, canary.deployment_id})) is None


async def serve_split(make_tenant, register_upstream, weights, *, statuses=("running", "running")):
    tenant = await make_tenant()
    register_upstream("vllm-stable")
    register_upstream("vllm-canary")
    stable = await tenant.add_deployment("stable-dep", "http://vllm-stable:8000", status=statuses[0])
    canary = await tenant.add_deployment("canary-dep", "http://vllm-canary:8000", status=statuses[1])
    await tenant.add_endpoint("chat", [(stable, weights[0], "stable"), (canary, weights[1], "canary")])
    key, _ = await tenant.add_key()
    return tenant, key


async def test_canary_weight_distribution_end_to_end(client, make_tenant, register_upstream, upstreams):
    _, key = await serve_split(make_tenant, register_upstream, (90, 10))
    n = 600
    labels = Counter()
    for _ in range(n):
        r = await client.post("/v1/chat/completions", json=chat("chat"), headers=bearer(key))
        assert r.status_code == 200
        labels[r.headers["x-slm-route"]] += 1
    assert labels["stable"] + labels["canary"] == n
    # 10% canary: expected 60 +- ~7 (1 sd); allow 3.5 sd
    assert 35 <= labels["canary"] <= 85, labels
    assert len(upstreams["vllm-canary"].calls) == labels["canary"]
    assert len(upstreams["vllm-stable"].calls) == labels["stable"]
    assert upstreams["vllm-canary"].calls[0]["body"]["model"] == "canary-dep"


async def test_ab_split_fifty_fifty(client, make_tenant, register_upstream):
    _, key = await serve_split(make_tenant, register_upstream, (50, 50))
    labels = Counter()
    for _ in range(300):
        r = await client.post("/v1/chat/completions", json=chat("chat"), headers=bearer(key))
        labels[r.headers["x-slm-route"]] += 1
    assert 120 <= labels["canary"] <= 180, labels


async def test_zero_weight_and_stopped_deployments_get_no_traffic(client, make_tenant, register_upstream, upstreams):
    _, key = await serve_split(make_tenant, register_upstream, (100, 0))
    for _ in range(30):
        await client.post("/v1/chat/completions", json=chat("chat"), headers=bearer(key))
    assert len(upstreams["vllm-canary"].calls) == 0 and len(upstreams["vllm-stable"].calls) == 30

    _, key2 = await serve_split(make_tenant, register_upstream, (50, 50), statuses=("running", "stopped"))
    labels = {
        (await client.post("/v1/chat/completions", json=chat("chat"), headers=bearer(key2))).headers["x-slm-route"]
        for _ in range(20)
    }
    assert labels == {"stable"}


async def test_failover_on_unreachable_deployment(client, make_tenant, register_upstream, upstreams):
    tenant = await make_tenant()
    register_upstream("vllm-up")
    dead = await tenant.add_deployment("dead-dep", "http://vllm-down:8000")  # host refuses connections
    live = await tenant.add_deployment("live-dep", "http://vllm-up:8000")
    await tenant.add_endpoint("ha", [(dead, 90, "primary"), (live, 10, "secondary")])
    key, _ = await tenant.add_key()
    for _ in range(25):
        r = await client.post("/v1/chat/completions", json=chat("ha"), headers=bearer(key))
        assert r.status_code == 200 and r.headers["x-slm-route"] == "secondary"
    assert len(upstreams["vllm-up"].calls) == 25


async def test_all_upstreams_down_is_502(client, make_tenant, flush):
    tenant = await make_tenant()
    dead = await tenant.add_deployment("dead-dep", "http://vllm-down:8000")
    await tenant.add_endpoint("ha", [(dead, 100, None)])
    key, _ = await tenant.add_key()
    r = await client.post("/v1/chat/completions", json=chat("ha"), headers=bearer(key))
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_unavailable"
    await flush()
    assert [row["status_code"] for row in await tenant.usage_rows()] == [502]


async def test_unknown_disabled_and_not_running_aliases(client, make_tenant, register_upstream):
    tenant = await make_tenant()
    register_upstream("vllm-a")
    dep = await tenant.add_deployment("d", "http://vllm-a:8000")
    stopped = await tenant.add_deployment("s", "http://vllm-a:8000", status="stopped")
    await tenant.add_endpoint("off", [(dep, 100, None)], enabled=False)
    await tenant.add_endpoint("down", [(stopped, 100, None)])
    key, _ = await tenant.add_key()

    r = await client.post("/v1/chat/completions", json=chat("nope"), headers=bearer(key))
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    assert r.json()["error"]["param"] == "model"
    assert (await client.post("/v1/chat/completions", json=chat("off"), headers=bearer(key))).status_code == 404
    r = await client.post("/v1/chat/completions", json=chat("down"), headers=bearer(key))
    assert r.status_code == 503 and r.json()["error"]["code"] == "no_healthy_deployment"


async def test_tenant_isolation_and_shared_aliases(client, make_tenant, register_upstream, upstreams):
    owner, other = await make_tenant(), await make_tenant()
    register_upstream("vllm-a")
    register_upstream("vllm-b")
    own_dep = await owner.add_deployment("own-dep", "http://vllm-a:8000")
    shared_dep = await owner.add_deployment("shared-dep", "http://vllm-b:8000")
    await owner.add_endpoint("private-model", [(own_dep, 100, None)])
    await owner.add_endpoint("phi-shared", [(shared_dep, 100, "shared")], kind="shared")
    key_other, _ = await other.add_key()

    r = await client.post("/v1/chat/completions", json=chat("private-model"), headers=bearer(key_other))
    assert r.status_code == 404  # another org's private alias is invisible
    r = await client.post("/v1/chat/completions", json=chat("phi-shared"), headers=bearer(key_other))
    assert r.status_code == 200 and r.json()["system_fingerprint"] == "vllm-b"

    # an org's own alias shadows a shared alias of the same name
    mine = await other.add_deployment("mine", "http://vllm-a:8000")
    await other.add_endpoint("phi-shared", [(mine, 100, "mine")])
    r = await client.post("/v1/chat/completions", json=chat("phi-shared"), headers=bearer(key_other))
    assert r.headers["x-slm-route"] == "mine" and r.json()["system_fingerprint"] == "vllm-a"


async def test_served_model_name_override(client, make_tenant, register_upstream, upstreams):
    tenant = await make_tenant()
    register_upstream("vllm-a")
    dep = await tenant.add_deployment("dep", "http://vllm-a:8000/v1", vllm_args={"served_model_name": "phi-4-mini"})
    await tenant.add_endpoint("x", [(dep, 100, None)])
    key, _ = await tenant.add_key()
    r = await client.post("/v1/chat/completions", json=chat("x"), headers=bearer(key))
    assert r.status_code == 200  # a base_url ending in /v1 is not doubled
    assert upstreams["vllm-a"].calls[0]["body"]["model"] == "phi-4-mini"
    assert upstreams["vllm-a"].calls[0]["path"] == "/v1/chat/completions"
