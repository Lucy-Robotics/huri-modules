"""StreamingTTS on a fake engine, fed the way the bus feeds it: one concurrent
process() call per token."""

import asyncio
import functools
from types import SimpleNamespace

import numpy as np
import pytest
from huri_events.audio import Audio
from huri_events.text import Token
from huri_tts.streaming import StreamingTTS

from huri_core.testing import LocalHandle, ModuleHarness


class FakeEngine:
    """The four methods StreamingTTS drives; 10 samples per pushed character."""

    def __init__(self):
        self.sessions = {}

    async def get_sample_rate(self):
        return 16000

    async def start_session(self, sid):
        self.sessions[sid] = {"texts": [], "done": asyncio.Event()}

    async def push_text(self, sid, text, end):
        await asyncio.sleep(0)  # let concurrent pushes interleave if they could
        session = self.sessions[sid]
        session["texts"].append(text)
        if end:
            session["done"].set()

    async def stream_audio(self, sid):
        session = self.sessions[sid]
        await session["done"].wait()
        for text in session["texts"]:
            if text:
                yield Audio(np.ones(len(text) * 10, dtype=np.float32), 16000)

    async def end_session(self, sid):
        session = self.sessions.pop(sid, None)
        if session:
            session["done"].set()


class Engine(StreamingTTS):
    _handle_cls = FakeEngine


class HangingEngine(FakeEngine):
    """Yields one chunk, then hangs like a replica still synthesizing."""

    async def stream_audio(self, sid):
        yield Audio(np.ones(10, np.float32), 16000)
        await asyncio.Event().wait()


class GatedEngine(FakeEngine):
    """Holds the `nth` call to `method` once done, until `release` is set."""

    def __init__(self, method, nth=1):
        super().__init__()
        self.method = method
        self.calls_left = nth
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def start_session(self, sid):
        await super().start_session(sid)
        await self._gate("start_session")

    async def push_text(self, sid, text, end):
        await super().push_text(sid, text, end)
        await self._gate("push_text")

    async def _gate(self, method):
        if method == self.method:
            self.calls_left -= 1
        if method == self.method and self.calls_left == 0:
            self.entered.set()
            await self.release.wait()


class Stream:
    """Ray's DeploymentResponseGenerator stand-in: iterates `source`, records
    cancel()."""

    def __init__(self, source):
        self._source = source
        self.cancel_called = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._source.__anext__()

    def cancel(self):
        self.cancel_called.set()


class StreamHandle(LocalHandle):
    """Unary calls go to the fake engine; each stream_audio call returns a
    Stream over the engine's, kept in `streams` by session id."""

    def __init__(self, target):
        super().__init__(target)
        self.streams = {}

    def options(self, *, stream=False, **ignored):
        if not stream:
            return super().options(stream=stream, **ignored)
        return SimpleNamespace(stream_audio=SimpleNamespace(remote=self._open))

    def _open(self, sid):
        self.streams[sid] = Stream(self.target.stream_audio(sid))
        return self.streams[sid]


async def speak(harness, tokens):
    return await asyncio.gather(*(harness.feed(t) for t in tokens))


def test_an_utterance_streams_in_token_order_and_ends_with_a_marker():
    engine = FakeEngine()
    harness = ModuleHarness(Engine, handle=LocalHandle(engine))
    tokens = [Token("Hello", False), Token(" there", False), Token("", True)]
    first, *rest = asyncio.run(speak(harness, tokens))

    assert "end_session" not in [call.method for call in harness.module._handle.calls]
    assert rest == [[], []]  # only the utterance's first call yields audio
    (session,) = engine.sessions.values()
    assert session["texts"] == ["Hello", " there", ""]
    assert [chunk.data.size for chunk in first] == [50, 60, 0]
    assert [chunk.pts for chunk in first[:2]] == [0.0, 50 / 16000]
    assert first[-1].end and first[-1].sample_rate == 16000


def test_each_utterance_gets_its_own_session():
    engine = FakeEngine()
    harness = ModuleHarness(Engine, handle=LocalHandle(engine))

    async def two_utterances():  # one event loop, like the server
        await speak(harness, [Token("One.", False), Token("", True)])
        await speak(harness, [Token("Two.", False), Token("", True)])

    asyncio.run(two_utterances())
    assert [s["texts"] for s in engine.sessions.values()] == [
        ["One.", ""],
        ["Two.", ""],
    ]


# Scenario: An interrupted utterance publishes no more audio
def test_an_interrupted_tts_utterance_stops_on_the_replica():
    handle = StreamHandle(HangingEngine())
    tts = Engine(handle)

    async def interrupt():
        first_chunk = asyncio.Event()

        async def utterance():
            async for _ in tts.process(Token("Hello there.", False)):
                first_chunk.set()

        task = asyncio.ensure_future(utterance())
        await asyncio.wait_for(first_chunk.wait(), 1)
        task.cancel()  # the session interrupts "huri.audio.out"
        with pytest.raises(asyncio.CancelledError):
            await task
        (stream,) = handle.streams.values()
        await asyncio.wait(
            {asyncio.ensure_future(stream.cancel_called.wait())}, timeout=1
        )
        # asserted inside the loop: asyncio.run's shutdown would cancel the drain
        assert stream.cancel_called.is_set()
        assert tts._session_id is None and tts._stream_task is None

    asyncio.run(interrupt())


async def interrupt(task):
    task.cancel()  # the session interrupts "huri.audio.out"
    with pytest.raises(asyncio.CancelledError):
        await task


async def speaks_a_new_answer(harness, engine):
    first, rest = await speak(harness, [Token("Again.", False), Token("", True)])
    *_, session = engine.sessions.values()  # the newest
    assert session["texts"] == ["Again.", ""]
    assert [chunk.data.size for chunk in first] == [60, 0] and rest == []


def test_the_next_utterance_starts_cleanly():
    engine = HangingEngine()
    harness = ModuleHarness(Engine, handle=StreamHandle(engine))

    async def interrupted_then_new_answer():
        first_chunk = asyncio.Event()

        async def utterance():
            async for _ in harness.module.process(Token("Hello there.", False)):
                first_chunk.set()

        task = asyncio.ensure_future(utterance())
        await asyncio.wait_for(first_chunk.wait(), 1)
        await interrupt(task)
        engine.stream_audio = functools.partial(FakeEngine.stream_audio, engine)
        await speaks_a_new_answer(harness, engine)

    asyncio.run(interrupted_then_new_answer())


@pytest.mark.parametrize("setup_step", ["start_session", "push_text"])
def test_an_utterance_interrupted_during_setup_leaves_tts_ready_to_speak(setup_step):
    engine = GatedEngine(setup_step)
    handle = StreamHandle(engine)
    harness = ModuleHarness(Engine, handle=handle)

    async def interrupted_in_setup_then_new_answer():
        task = asyncio.ensure_future(harness.feed(Token("Hello", False)))
        await asyncio.wait_for(engine.entered.wait(), 1)
        await interrupt(task)
        await speaks_a_new_answer(harness, engine)
        # asserted inside the loop: asyncio.run's shutdown would cancel the drain
        new_sid = [c.args for c in handle.calls if c.method == "start_session"][-1][0]
        assert all(
            stream.cancel_called.is_set()
            for sid, stream in handle.streams.items()
            if sid != new_sid
        )

    asyncio.run(interrupted_in_setup_then_new_answer())


def test_tts_speaks_again_after_an_utterance_is_cancelled_twice():
    engine = GatedEngine("push_text", nth=2)
    handle = StreamHandle(engine)
    harness = ModuleHarness(Engine, handle=handle)

    async def cancelled_twice_then_new_answer():
        first = asyncio.ensure_future(harness.feed(Token("Hello", False)))
        second = asyncio.ensure_future(harness.feed(Token(" there", False)))
        await asyncio.wait_for(engine.entered.wait(), 1)  # 2nd token holds the lock
        first.cancel()
        await asyncio.sleep(0)  # its cleanup now waits for the push lock
        await interrupt(first)
        await interrupt(second)
        (opened,) = [c.args for c in handle.calls if c.method == "start_session"]
        ended = [c.args for c in handle.calls if c.method == "end_session"]
        assert ended == [opened]
        assert opened[0] not in engine.sessions  # it ran despite the second cancel
        await speaks_a_new_answer(harness, engine)

    asyncio.run(cancelled_twice_then_new_answer())


def test_an_interrupted_utterance_ends_its_session_on_the_replica():
    handle = StreamHandle(HangingEngine())
    harness = ModuleHarness(Engine, handle=handle)

    async def interrupted():
        first_chunk = asyncio.Event()

        async def utterance():
            async for _ in harness.module.process(Token("Hello there.", False)):
                first_chunk.set()

        task = asyncio.ensure_future(utterance())
        await asyncio.wait_for(first_chunk.wait(), 1)
        await interrupt(task)
        (opened,) = [c.args for c in handle.calls if c.method == "start_session"]
        ended = [c.args for c in handle.calls if c.method == "end_session"]
        assert ended == [opened]

    asyncio.run(interrupted())
