"""RAGHandle's LLM streaming and memory maintenance, on fake HTTP and Qdrant."""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from huri_rag.rag import RAGDeploymentConfig, RAGHandle


def _handle(transport=None, **config):
    rag_handle = object.__new__(RAGHandle.func_or_class)
    rag_handle._cfg = RAGDeploymentConfig(retry_base_delay=0, **config)
    rag_handle._llm_client = httpx.AsyncClient(transport=transport)
    return rag_handle


def _stream(rag_handle, history=None):
    async def collect():
        return [
            d
            async for d in rag_handle._llm_stream(
                "sys", "question", {"max_length": 50, "temperature": 0.1}, history
            )
        ]

    return asyncio.run(collect())


def test_ollama_streams_the_message_deltas_until_done():
    sent = []

    def reply(request):
        sent.append(json.loads(request.content))
        lines = [
            {"message": {"content": "Hel"}},
            {"message": {"content": "lo"}},
            {"message": {"content": ""}, "done": True},
            {"message": {"content": "after done"}},
        ]
        body = "\n".join(json.dumps(x) for x in lines[:2]) + "\n\nnot json\n"
        body += "\n".join(json.dumps(x) for x in lines[2:])
        return httpx.Response(200, text=body)

    rag_handle = _handle(httpx.MockTransport(reply), llm_provider="ollama")
    history = [{"role": "user", "content": "before"}]
    assert _stream(rag_handle, history) == ["Hel", "lo"]
    [body] = sent
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "before"},
        {"role": "user", "content": "question"},
    ]
    assert body["options"] == {"num_predict": 50, "temperature": 0.1}
    assert body["stream"] is True


def _sse(*deltas):
    events = [
        "data: " + json.dumps({"choices": [{"delta": {"content": d}}]}) for d in deltas
    ]
    return "\n".join([": keep-alive", *events, "data: [DONE]", events[0]])


@pytest.mark.parametrize("provider", ["vllm", "api"])
def test_openai_compatible_providers_stream_the_choice_deltas(provider):
    sent = []

    def reply(request):
        sent.append(request)
        return httpx.Response(200, text=_sse("Hi", " there"))

    rag_handle = _handle(
        httpx.MockTransport(reply), llm_provider=provider, llm_api_key="k"
    )
    assert _stream(rag_handle) == ["Hi", " there"]
    [request] = sent
    assert request.url.path == "/v1/chat/completions"
    body = json.loads(request.content)
    assert body["max_tokens"] == 50 and body["temperature"] == 0.1
    # Only the hosted API gets the key.
    assert (request.headers.get("Authorization") == "Bearer k") == (provider == "api")


@pytest.mark.parametrize(
    "provider, field, value",
    [
        ("ollama", "format", "json"),
        ("vllm", "response_format", {"type": "json_object"}),
        ("api", "response_format", {"type": "json_object"}),
    ],
)
def test_memory_bookkeeping_asks_for_json(provider, field, value):
    sent = []

    def reply(request):
        sent.append(json.loads(request.content))
        if provider == "ollama":
            return httpx.Response(200, text=json.dumps({"message": {"content": "{}"}}))
        return httpx.Response(200, text=_sse("{}"))

    rag_handle = _handle(httpx.MockTransport(reply), llm_provider=provider)
    assert asyncio.run(rag_handle._llm_json("sys", "prompt")) == {}
    assert asyncio.run(rag_handle._llm_complete("sys", "prompt")) == "{}"
    json_mode, plain = sent
    assert json_mode[field] == value and field not in plain


@pytest.mark.parametrize("provider", ["ollama", "vllm"])
def test_a_failed_connection_is_retried(provider):
    calls = []

    def reply(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("down")
        if provider == "ollama":
            return httpx.Response(200, text=json.dumps({"message": {"content": "ok"}}))
        return httpx.Response(200, text=_sse("ok"))

    rag_handle = _handle(httpx.MockTransport(reply), llm_provider=provider)
    assert _stream(rag_handle) == ["ok"]
    assert len(calls) == 2


def test_an_unknown_provider_is_refused():
    with pytest.raises(ValueError, match="Unknown llm_provider"):
        _stream(_handle(llm_provider="nope"))


class FakeQdrant:
    def __init__(self, points):
        self.points = {p.id: p for p in points}
        self.upserted = []

    def scroll(self, collection_name, limit, offset, with_payload, with_vectors):
        points = list(self.points.values())
        start = offset or 0
        nxt = start + limit if start + limit < len(points) else None
        return points[start : start + limit], nxt

    def upsert(self, collection_name, points):
        for p in points:
            self.upserted.append(p)
            self.points[p.id] = p

    def delete(self, collection_name, points_selector):
        for i in points_selector.points:
            del self.points[i]

    def retrieve(self, collection_name, ids, with_payload, with_vectors):
        return [self.points[i] for i in ids if i in self.points]

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name="conversations")])


def _memory(id, user, days_ago, importance):
    when = (datetime.now() - timedelta(days=days_ago)).isoformat()
    return SimpleNamespace(
        id=id,
        payload={
            "text": f"memory {id}",
            "_user_id": user,
            "created_at": when,
            "last_accessed": when,
            "importance": importance,
        },
    )


def test_maintenance_deletes_dead_memories_and_merges_weak_ones():
    # strength = 0.5 ** (age / (5 * imp / 5)) * imp / 10
    points = [
        _memory("strong", "ana", 0, 8),  # 0.8: kept
        _memory("dead", "ana", 60, 3),  # ~0: deleted
        _memory("weak1", "ana", 3, 3),  # ~0.15: weak
        _memory("weak2", "ana", 4, 3),
        _memory("weak3", "ana", 5, 4),
        _memory("lone1", "bob", 3, 3),  # bob has only 2 weak: kept
        _memory("lone2", "bob", 4, 3),
    ]
    qdrant = FakeQdrant(points)
    rag_handle = _handle()
    rag_handle._qdrant = qdrant
    prompts = []

    async def complete(system, prompt, max_tokens=300):
        prompts.append(prompt)
        return " merged ana \n"

    async def embed(text):
        return [0.1, 0.2]

    rag_handle._llm_complete = complete
    rag_handle._embed = embed
    asyncio.run(rag_handle._run_maintenance())

    assert all(f"memory weak{i}" in prompts[0] for i in (1, 2, 3))
    merged, marker = qdrant.upserted
    assert merged.payload["text"] == "merged ana"
    assert merged.payload["_user_id"] == "ana"
    assert merged.payload["type"] == "conversation_consolidated"
    assert merged.payload["importance"] == 5  # strongest merged + 1
    assert merged.vector == [0.1, 0.2]
    assert marker.payload["type"] == "maintenance_marker" and marker.vector == [0, 0]
    assert set(qdrant.points) == {"strong", "lone1", "lone2", merged.id, marker.id}


def test_maintenance_leaves_its_marker_alone():
    rag_handle = _handle()
    marker = SimpleNamespace(
        id=rag_handle._MAINTENANCE_MARKER_ID,
        payload={"type": "maintenance_marker", "last_run": "2020-01-01T00:00:00"},
    )
    rag_handle._qdrant = FakeQdrant([marker])

    async def embed(text):
        return [0.0]

    rag_handle._embed = embed
    asyncio.run(rag_handle._run_maintenance())
    assert rag_handle._last_maintenance() > datetime(2020, 1, 2)


def test_inspect_tells_each_memory_s_fate():
    from huri_rag.memory_inspect import fate, strength

    now = datetime.now()
    assert strength(_memory("m", "ana", 0, 8).payload, at=now) == pytest.approx(0.8)
    later = now + timedelta(days=5)
    assert strength(_memory("m", "ana", 0, 5).payload, at=later) == pytest.approx(0.25)
    assert [fate(s) for s in (0.8, 0.2, 0.01)] == ["KEEP", "CONSOLIDATE", "DELETE"]


def test_the_maintenance_job_scores_like_the_handle():
    from huri_rag.memory_maintenance import strength

    assert strength(_memory("m", "ana", 5, 5).payload) == pytest.approx(0.25)
