"""RAG's three memories: short-term (the last turns, verbatim), working (what
the turns pushed out of it are folded into) and long-term (what the session
leaves behind, saved at the end).
"""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from huri_events.question import RAGQuestion
from huri_events.speech import Transcript
from huri_rag.rag import RAG, RAGDeploymentConfig, RAGHandle, WorkingMemory, _ago

from huri_core.testing import LocalHandle, ModuleHarness


class FakeLLM:
    """Answers each question with "re: <question>"; folds by listing the
    folded questions as notes; records what it is sent."""

    def __init__(self):
        self.queries = []
        self.folds = []
        self.saved = None

    async def stream(self, query):
        self.queries.append(query)
        yield f"re: {query.question}"

    async def fold_into_working_memory(self, working, turns):
        self.folds.append(turns)
        asked = [m["content"] for m in turns if m["role"] == "user"]
        return WorkingMemory(summary="so far", notes=working.notes + asked)

    async def save_conversation(self, user_id, history, working=None):
        self.saved = (user_id, history, working)


def _talk(*questions, max_history_turns=2, **args):
    fake = FakeLLM()
    harness = ModuleHarness(
        RAG,
        handle=LocalHandle(fake),
        user_id="u",
        max_history_turns=max_history_turns,
        **args,
    )

    async def session():
        for q in questions:  # one after the other, like a conversation
            await harness.feed(RAGQuestion(Transcript(q, True), None))
        await harness.finalize()

    asyncio.run(session())
    return fake


def test_short_term_memory_keeps_the_last_turns_verbatim():
    fake = _talk("a", "b", "c", "d")
    last = fake.queries[-1]
    assert [m["content"] for m in last.history] == ["b", "re: b", "c", "re: c"]


def test_nothing_is_folded_while_short_term_memory_has_room():
    fake = _talk("a", "b")
    assert fake.folds == []
    assert not fake.queries[-1].working


def test_turns_pushed_out_of_short_term_memory_go_to_working_memory():
    fake = _talk("a", "b", "c", "d")
    assert fake.folds == [
        [{"role": "user", "content": "a"}, {"role": "assistant", "content": "re: a"}],
        [{"role": "user", "content": "b"}, {"role": "assistant", "content": "re: b"}],
    ]
    # "c" pushed "a" out: the question after it has "a" in working memory.
    assert fake.queries[3].working.notes == ["a"]


def test_the_session_leaves_both_memories_to_long_term_memory():
    fake = _talk("a", "b", "c", "d")
    user_id, history, working = fake.saved
    assert user_id == "u"
    assert [m["content"] for m in history] == ["c", "re: c", "d", "re: d"]
    assert working.notes == ["a", "b"]


def test_short_term_memory_alone_forgets_what_leaves_it():
    fake = _talk("a", "b", "c", memory="short")
    history = fake.queries[-1].history
    assert [m["content"] for m in history] == ["a", "re: a", "b", "re: b"]
    assert fake.folds == [] and fake.saved is None
    assert fake.queries[-1].long_term is False


def test_working_memory_alone_takes_each_exchange_at_once():
    fake = _talk("a", "b", "c", memory="working")
    assert [q.history for q in fake.queries] == [[], [], []]
    assert fake.queries[-1].working.notes == ["a", "b"]
    assert fake.saved is None


def test_long_term_memory_alone_still_saves_the_last_turns():
    fake = _talk("a", "b", "c", memory=["long"])
    assert [q.history for q in fake.queries] == [[], [], []]
    assert fake.folds == []
    assert fake.queries[-1].long_term is True
    _, history, working = fake.saved
    assert [m["content"] for m in history] == ["b", "re: b", "c", "re: c"]
    assert not working


def test_short_and_long_without_working_memory():
    fake = _talk("a", "b", "c", memory="short, long")
    assert fake.folds == []
    assert [m["content"] for m in fake.saved[1]] == ["b", "re: b", "c", "re: c"]


def test_no_memory_at_all_is_a_stateless_answerer():
    fake = _talk("a", "b", "c", memory=[])
    assert [q.history for q in fake.queries] == [[], [], []]
    assert fake.folds == [] and fake.saved is None
    assert not fake.queries[-1].working and fake.queries[-1].long_term is False


def test_an_unknown_memory_is_refused():
    with pytest.raises(ValueError, match="Unknown memory"):
        _talk("a", memory="short,episodic")


def _in_json_mode(llm):
    """An _llm_stream answering with `llm(system, prompt)`, that memory
    bookkeeping must call in JSON mode."""

    async def stream(system, prompt, preferences, history):
        assert preferences.get("json") is True
        yield await llm(system, prompt)

    return stream


def _handle(**config):
    rag_handle = object.__new__(RAGHandle.func_or_class)
    rag_handle._cfg = RAGDeploymentConfig(**config)
    return rag_handle


def test_working_memory_is_in_the_prompt():
    working = WorkingMemory(summary="We talked about cheese.", notes=["Name: Ana"])
    _, user = _handle()._build_prompt("hi", [], {}, working=working)
    assert "Earlier in this conversation: We talked about cheese." in user
    assert "Keep in mind:\n- Name: Ana" in user

    _, user = _handle()._build_prompt("hi", [], {}, working=WorkingMemory())
    assert "Keep in mind" not in user


def test_the_fold_keeps_working_memory_to_its_capacity():
    rag_handle = _handle(working_memory_capacity=2)

    async def llm(system, prompt, max_tokens=300):
        return "```json\n" + json.dumps({"summary": "s", "notes": ["1", "2", "3"]})

    rag_handle._llm_stream = _in_json_mode(llm)
    working = asyncio.run(
        rag_handle.fold_into_working_memory(
            WorkingMemory(), [{"role": "user", "content": "x"}]
        )
    )
    assert working == WorkingMemory(summary="s", notes=["1", "2"])


def test_a_failed_fold_keeps_the_turns_raw():
    rag_handle = _handle()

    async def llm(system, prompt, max_tokens=300):
        return "not json"

    rag_handle._llm_stream = _in_json_mode(llm)
    working = asyncio.run(
        rag_handle.fold_into_working_memory(
            WorkingMemory(summary="before", notes=["n"]),
            [{"role": "user", "content": "x"}],
        )
    )
    assert working.summary == "before\nuser: x" and working.notes == ["n"]


def test_long_term_memory_summarizes_working_memory_too():
    rag_handle = _handle()
    prompts, stored = [], []

    async def llm(system, prompt, max_tokens=300):
        prompts.append(prompt)
        return json.dumps({"summary": "Ana has a cat, Pixel.", "importance": 8})

    async def embed(text):
        return [0.0]

    rag_handle._llm_stream = _in_json_mode(llm)
    rag_handle._embed = embed
    rag_handle._ensure_memory_collection = lambda size: None
    rag_handle._qdrant = type(
        "Q", (), {"upsert": lambda self, **kw: stored.extend(kw["points"])}
    )()
    working = WorkingMemory(summary="Ana said hi.", notes=["Cat: Pixel"])
    last = [{"role": "user", "content": "bye"}]
    asyncio.run(rag_handle.save_conversation("ana", last, working))

    [prompt] = prompts
    assert "PART 1" in prompt and "- Cat: Pixel" in prompt
    assert "PART 2, how the conversation ended:\nuser: bye" in prompt
    [point] = stored
    assert point.payload["text"] == "Ana has a cat, Pixel."
    assert point.payload["importance"] == 8 and point.payload["_user_id"] == "ana"


class MemoryStore:
    """A fake Qdrant memory collection: similar memories are `similar`."""

    def __init__(self, *similar):
        self.similar = [
            SimpleNamespace(id=f"old{i}", payload={"text": t, "importance": 6})
            for i, t in enumerate(similar, 1)
        ]
        self.upserted, self.deleted, self.queried = [], [], 0

    def query_points(self, **kwargs):
        self.queried += 1
        return SimpleNamespace(points=self.similar)

    def upsert(self, collection_name, points):
        self.upserted.extend(points)

    def delete(self, collection_name, points_selector):
        self.deleted.extend(points_selector.points)


def _save(store, reconcile_reply, **config):
    rag_handle = _handle(**config)
    prompts = []

    async def llm(system, prompt, max_tokens=300):
        prompts.append(prompt)
        if len(prompts) == 1:
            return json.dumps({"summary": "Ana renamed Pixel Felix.", "importance": 7})
        return reconcile_reply

    async def embed(text):
        return [float(len(text))]

    rag_handle._llm_stream = _in_json_mode(llm)
    rag_handle._embed = embed
    rag_handle._ensure_memory_collection = lambda size: None
    rag_handle._qdrant = store
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hey"},
    ]
    asyncio.run(rag_handle.save_conversation("ana", history))
    return prompts


def test_a_new_memory_corrects_the_older_ones_it_updates():
    store = MemoryStore("Ana's cat is called pixel.", "Ana likes jazz.")
    reply = json.dumps({"changes": [{"old": "Pixel", "new": "Felix"}]})
    prompts = _save(store, reply)

    assert "Ana renamed Pixel Felix." in prompts[1]
    assert "- Ana's cat is called pixel." in prompts[1]
    assert store.deleted == []
    corrected, new = store.upserted  # "Ana likes jazz." is left alone
    assert corrected.id == "old1"
    assert corrected.payload["text"] == "Ana's cat is called Felix."
    assert corrected.payload["importance"] == 6
    # The text before is kept, to audit or undo the correction.
    [before] = corrected.payload["history"]
    assert before["text"] == "Ana's cat is called pixel."
    assert before["changes"] == ["Pixel -> Felix"]
    assert datetime.fromisoformat(before["until"]) <= datetime.now()
    assert corrected.vector == [float(len("Ana's cat is called Felix."))]
    assert new.payload["text"] == "Ana renamed Pixel Felix."


def test_a_change_may_come_with_its_context():
    store = MemoryStore("Ana has an orange cat named Pixel.")
    change = {"old": "orange cat named Pixel", "new": "orange cat named Felix"}
    _save(store, json.dumps({"changes": [change]}))
    corrected, _ = store.upserted
    assert corrected.payload["text"] == "Ana has an orange cat named Felix."


def test_changes_the_new_memory_does_not_say_are_ignored():
    store = MemoryStore("Ana's cat is called Pixel.", "Ana likes jazz.")
    reply = json.dumps(
        {
            "changes": [
                {"old": "jazz", "new": "rock"},  # not in the new memory
                {"old": "Pixel", "new": "Pixel"},  # no change
                {"old": "Ana's cat is called Pixel", "new": "Felix"},  # too long
                {"old": "Pixel"},  # incomplete
                "Pixel -> Felix",  # not an object
            ]
        }
    )
    _save(store, reply)
    assert [p.payload["text"] for p in store.upserted] == ["Ana renamed Pixel Felix."]


def test_nothing_to_reconcile_without_similar_memories():
    store = MemoryStore()
    prompts = _save(store, "unused")
    assert len(prompts) == 1 and store.deleted == []
    assert [p.payload["text"] for p in store.upserted] == ["Ana renamed Pixel Felix."]


def test_a_failed_reconciliation_keeps_every_memory():
    store = MemoryStore("Ana's cat is called Pixel.")
    _save(store, "not json")
    assert store.deleted == []
    assert [p.payload["text"] for p in store.upserted] == ["Ana renamed Pixel Felix."]


def test_reconciliation_can_be_turned_off():
    store = MemoryStore("Ana's cat is called Pixel.")
    reply = '{"changes": [{"old": "Pixel", "new": "Felix"}]}'
    prompts = _save(store, reply, memory_reconcile=False)
    assert store.queried == 0 and len(prompts) == 1 and store.deleted == []


def test_ages_read_like_a_person_would_say_them():
    now = datetime(2026, 10, 22, 12)
    ago = [_ago((now - timedelta(days=d)).isoformat(), now) for d in (0, 1, 5, 20, 90)]
    assert ago == ["today", "yesterday", "5 days ago", "2 weeks ago", "3 months ago"]
    assert _ago(None, now) == "some time ago"


def test_recalled_memories_are_dated_oldest_first():
    def days_ago(n):
        return (datetime.now() - timedelta(days=n)).isoformat()

    def hit(text, score, created, **extra):
        payload = {"text": text, "created_at": created, "importance": 5, **extra}
        return SimpleNamespace(id=text, score=score, payload=payload)

    rag_handle = _handle(memory_top_k=3)
    rag_handle._qdrant = SimpleNamespace(
        query_points=lambda **kw: SimpleNamespace(
            points=[
                hit("Pixel was renamed Felix.", 0.9, days_ago(1)),
                hit(
                    "Ana has an orange cat named Felix.",
                    0.8,
                    days_ago(20),
                    history=[{"text": "...Pixel.", "until": days_ago(1)}],
                ),
                hit("Ana likes jazz.", 0.7, days_ago(5)),
            ]
        ),
        set_payload=lambda **kw: None,
    )
    assert rag_handle._search_memories([0.0], "ana") == [
        "2 weeks ago (corrected yesterday): Ana has an orange cat named Felix.",
        "5 days ago: Ana likes jazz.",
        "yesterday: Pixel was renamed Felix.",
    ]
    _, user = rag_handle._build_prompt("hi", [], {}, memories=["today: x"])
    assert "oldest first (when two disagree, the more recent one is right)" in user
