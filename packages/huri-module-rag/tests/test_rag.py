"""RAG and QAG on fake deployments, through huri_core.testing."""

import asyncio

import pytest
from huri_events.emotion import Emotion
from huri_events.question import PartialQuestion, RAGQuestion
from huri_events.speech import Transcript
from huri_events.text import Token
from huri_rag.question_aggregator import QAG
from huri_rag.rag import RAG

from huri_core.testing import LocalHandle, ModuleHarness


def test_a_real_streaming_module_runs_on_a_fake_deployment():
    class FakeRAGHandle:
        async def stream(self, query):
            for delta in ("Hello", " ", query.question):
                yield delta

        async def save_conversation(self, user_id, history, working=None):
            self.saved = (user_id, history)

    fake = FakeRAGHandle()
    harness = ModuleHarness(RAG, handle=LocalHandle(fake), user_id="alice")
    out = harness.run(RAGQuestion(Transcript("world", True), None))
    assert out == [
        Token("Hello", False),
        Token(" ", False),
        Token("world", False),
        Token("", True),
    ]

    asyncio.run(harness.finalize())
    assert fake.saved[0] == "alice" and fake.saved[1][-1]["content"] == "Hello world"


def test_qag_waits_for_the_emotion_when_asked_to():
    harness = ModuleHarness(QAG, use_emotion=True, mode="sequential")
    emotion = Emotion("joy", 0.8, {"joy": 0.8}, True)
    # The sentence is held (its call emits nothing) until the emotion's call
    # sends the question.
    assert harness.run(
        PartialQuestion(Transcript("hi", True), None), PartialQuestion(None, emotion)
    ) == [RAGQuestion(Transcript("hi", True), emotion)]


def test_qag_without_emotion_forwards_the_transcript():
    out = ModuleHarness(QAG, use_emotion=False).run(
        PartialQuestion(Transcript("hi", True), None)
    )
    assert out == [RAGQuestion(Transcript("hi", True), None)]


class _RayStream:
    """Like Ray's streaming response: hands out `items`, then waits for ever."""

    def __init__(self, items, hang=True):
        self._items = list(items)
        self._hang = hang
        self.cancel_called = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._items:
            return self._items.pop(0)
        if not self._hang:
            raise StopAsyncIteration
        await asyncio.Event().wait()

    def cancel(self):
        self.cancel_called.set()


class _RayHandle:
    """A DeploymentHandle whose `.stream.remote()` returns the streams in turn."""

    def __init__(self, *streams):
        self._streams = list(streams)
        self.stream = self

    def options(self, **_):
        return self

    def remote(self, query):
        return self._streams.pop(0)


def _ask(rag, text):
    return rag.process(RAGQuestion(Transcript(text, True), None))


def test_an_interrupted_rag_answer_stops_on_the_replica():
    async def run():
        stream = _RayStream(["Hi"])
        rag = ModuleHarness(RAG, handle=_RayHandle(stream)).module
        got = asyncio.Event()

        async def answer():
            async for _ in _ask(rag, "What is HuRI?"):
                got.set()

        task = asyncio.create_task(answer())
        await got.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.cancel_called.is_set()

    asyncio.run(run())


def test_an_answer_closed_by_its_consumer_stops_on_the_replica():
    async def run():
        stream = _RayStream(["Hi"])
        rag = ModuleHarness(RAG, handle=_RayHandle(stream)).module
        agen = _ask(rag, "What is HuRI?")
        await agen.__anext__()
        await agen.aclose()
        assert stream.cancel_called.is_set()

    asyncio.run(run())


def test_the_next_question_is_answered_after_an_interruption():
    async def run():
        handle = _RayHandle(
            _RayStream(["Hey"], hang=False),
            _RayStream(["Hi"]),
            _RayStream(["Me"], hang=False),
        )
        rag = ModuleHarness(RAG, handle=handle).module
        assert [t async for t in _ask(rag, "Hello")][0] == Token("Hey", False)

        agen = _ask(rag, "What is HuRI?")
        await agen.__anext__()
        await agen.aclose()

        async def collect():
            return [t async for t in _ask(rag, "Who built you?")]

        # A leaked answer lock would hang here: fail instead.
        out = await asyncio.wait_for(collect(), 5)
        assert out == [Token("Me", False), Token("", True)]
        assert {"role": "user", "content": "Hello"} in rag.history

    asyncio.run(run())
