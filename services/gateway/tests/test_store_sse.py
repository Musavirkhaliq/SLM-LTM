import json

import fakeredis
import pytest

from slm_gateway.sse import SSEInspector
from slm_gateway.store import MemoryStore, RedisStore, ResilientStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(params=["memory", "redis"])
def store(request):
    if request.param == "memory":
        return MemoryStore()
    return RedisStore(fakeredis.FakeAsyncRedis())


async def test_window_hit_blocks_at_limit_and_reports_remaining(store):
    for used in (1, 2, 3):
        r = await store.window_hit("k", limit=3, window=60)
        assert r.allowed and r.used == used
    r = await store.window_hit("k", limit=3, window=60)
    assert not r.allowed and r.used == 3 and 0 < r.retry_after <= 60
    assert (await store.window_used("k", window=60))[0] == 3  # a denied hit adds nothing


async def test_windows_are_independent_per_key(store):
    assert (await store.window_hit("a", limit=1, window=60)).allowed
    assert not (await store.window_hit("a", limit=1, window=60)).allowed
    assert (await store.window_hit("b", limit=1, window=60)).allowed


async def test_weighted_cost_and_negative_adjustments(store):
    assert (await store.window_hit("t", limit=100, window=60, cost=70)).allowed
    assert not (await store.window_hit("t", limit=100, window=60, cost=40)).allowed
    await store.window_add("t", cost=-50, window=60)  # refund of an over-estimate
    assert (await store.window_hit("t", limit=100, window=60, cost=40)).allowed
    assert (await store.window_used("t", window=60))[0] == 60


async def test_counters_and_seed(store):
    assert await store.counter_get("c") is None
    await store.counter_seed("c", 10, 60)
    await store.counter_seed("c", 99, 60)  # no-op: already exists
    assert await store.counter_get("c") == 10
    assert await store.counter_add("c", 5, 60) == 15


async def test_kv_roundtrip_and_delete(store):
    await store.set("x", "v", 60)
    assert await store.get("x") == "v"
    await store.delete("x", "missing")
    assert await store.get("x") is None


async def test_memory_window_slides_with_time():
    clock = Clock()
    store = MemoryStore(clock)
    for _ in range(3):
        assert (await store.window_hit("k", limit=3, window=60)).allowed
        clock.now += 10  # t = 0, 10, 20 -> now 30
    denied = await store.window_hit("k", limit=3, window=60)
    assert not denied.allowed and denied.retry_after == pytest.approx(30)  # first entry frees at t=60
    clock.now += 31  # t=61: only the entry at t=0 has left the window
    assert (await store.window_hit("k", limit=3, window=60)).allowed
    assert not (await store.window_hit("k", limit=3, window=60)).allowed
    clock.now += 200
    assert (await store.window_used("k", window=60))[0] == 0


async def test_memory_kv_and_counters_expire():
    clock = Clock()
    store = MemoryStore(clock)
    await store.set("x", "v", 10)
    await store.counter_add("c", 1, 10)
    clock.now += 11
    assert await store.get("x") is None and await store.counter_get("c") is None


async def test_redis_sliding_window_expires_old_entries():
    redis = fakeredis.FakeAsyncRedis()
    store = RedisStore(redis)
    assert (await store.window_hit("k", limit=1, window=0.2)).allowed
    assert not (await store.window_hit("k", limit=1, window=0.2)).allowed
    import asyncio

    await asyncio.sleep(0.3)
    assert (await store.window_hit("k", limit=1, window=0.2)).allowed


class BrokenStore(MemoryStore):
    async def _boom(self, *a, **k):
        raise ConnectionError("redis down")

    get = set = delete = window_hit = window_used = window_add = counter_get = counter_add = counter_seed = _boom


async def test_resilient_store_falls_back_to_local_memory():
    store = ResilientStore(BrokenStore(), MemoryStore())
    assert (await store.window_hit("k", limit=1, window=60)).allowed
    assert not (await store.window_hit("k", limit=1, window=60)).allowed  # still enforced locally
    await store.set("a", "b", 60)
    assert await store.get("a") == "b"
    assert await store.counter_add("c", 3, 60) == 3


def event(payload: dict | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {data}\n\n".encode()


def test_sse_inspector_reassembles_events_split_across_chunks():
    stream = (
        event({"choices": [{"delta": {"content": "a"}}]})
        + event({"choices": [{"delta": {"content": "b"}}]})
        + event({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
        + event("[DONE]")
    )
    inspector = SSEInspector(drop_usage_chunk=False)
    out = []
    for i in range(0, len(stream), 7):  # arbitrary network chunking
        out += inspector.feed(stream[i : i + 7])
    assert b"".join(out) == stream  # byte-for-byte passthrough
    assert inspector.usage == {"prompt_tokens": 3, "completion_tokens": 2}
    assert inspector.content_events == 2 and inspector.saw_done


def test_sse_inspector_drops_only_the_usage_only_chunk_when_asked():
    content = event({"choices": [{"delta": {"content": "a"}}]})
    usage = event({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    inspector = SSEInspector(drop_usage_chunk=True)
    assert inspector.feed(content + usage + event("[DONE]")) == [content, event("[DONE]")]
    assert inspector.usage["completion_tokens"] == 1


def test_sse_inspector_handles_crlf_garbage_and_tail():
    inspector = SSEInspector(drop_usage_chunk=False)
    crlf = b'data: {"choices": [{"delta": {"content": "x"}}]}\r\n\r\n'
    assert inspector.feed(crlf + b": keep-alive\n\n" + b"data: not json\n\n") == [
        crlf, b": keep-alive\n\n", b"data: not json\n\n",
    ]
    assert inspector.feed(b"data: partial") == []
    assert inspector.tail() == b"data: partial"
