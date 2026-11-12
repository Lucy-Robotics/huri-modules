"""StreamingTTS on a fake engine, fed the way the bus feeds it: one concurrent
process() call per token."""

import asyncio
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


class Engine(StreamingTTS):
    _handle_cls = FakeEngine


class HangingStream:
    """Ray's DeploymentResponseGenerator stand-in: yields `items`, then hangs
    like a replica still synthesizing."""

    def __init__(self, *items):
        self._items = list(items)
        self.cancel_called = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._items:
            return self._items.pop(0)
        await asyncio.Event().wait()

    def cancel(self):
        self.cancel_called.set()


class HangingStreamHandle(LocalHandle):
    """Unary calls go to the fake engine; stream_audio returns `stream`."""

    def __init__(self, target, stream):
        super().__init__(target)
        self.stream = stream

    def options(self, *, stream=False, **ignored):
        if not stream:
            return super().options(stream=stream, **ignored)
        return SimpleNamespace(
            stream_audio=SimpleNamespace(remote=lambda sid: self.stream)
        )


async def speak(harness, tokens):
    return await asyncio.gather(*(harness.feed(t) for t in tokens))


def test_an_utterance_streams_in_token_order_and_ends_with_a_marker():
    engine = FakeEngine()
    harness = ModuleHarness(Engine, handle=LocalHandle(engine))
    tokens = [Token("Hello", False), Token(" there", False), Token("", True)]
    first, *rest = asyncio.run(speak(harness, tokens))

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


def test_an_interrupted_tts_utterance_stops_on_the_replica():
    stream = HangingStream(Audio(np.ones(10, np.float32), 16000))
    tts = Engine(HangingStreamHandle(FakeEngine(), stream))

    async def interrupt():
        first_chunk = asyncio.Event()

        async def utterance():
            async for _ in tts.process(Token("Hello there.", False)):
                first_chunk.set()

        task = asyncio.ensure_future(utterance())
        await first_chunk.wait()
        task.cancel()  # the session interrupts "huri.audio.out"
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait(
            {asyncio.ensure_future(stream.cancel_called.wait())}, timeout=1
        )
        # asserted inside the loop: asyncio.run's shutdown would cancel the drain
        assert stream.cancel_called.is_set()
        assert tts._session_id is None and tts._stream_task is None

    asyncio.run(interrupt())
