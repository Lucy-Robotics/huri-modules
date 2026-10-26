import asyncio

import numpy as np
from huri_events.speech import Transcript, Voice

from huri_core.testing import LocalHandle, ModuleHarness


class FakeWhisper:
    """Stands in for STTHandle: one scripted text per call; can block the first."""

    def __init__(self, texts, gate=None):
        self.texts = list(texts)
        self.gate = gate
        self.calls = []

    async def transcribe(self, audio, language):
        self.calls.append(len(audio))
        if self.gate is not None and len(self.calls) == 1:
            await self.gate.wait()
        return self.texts.pop(0)


def _stt(whisper):
    from huri_stt.module import STT

    # 20 ms blocks -> window 10 frames, step 5 frames (fast tests).
    return ModuleHarness(
        STT, handle=LocalHandle(whisper), transcribe_window=0.2, transcribe_step=0.1
    )


def _voice():
    return Voice(np.zeros(4, dtype=np.float32))


async def _feed(stt, n):
    """n frames at once, as the bus delivers them while a pass is in flight."""

    return await stt.feed_all(*[_voice() for _ in range(n)])


async def _speak(stt, n):
    """n frames one after the other, each fully handled before the next."""

    out = []
    for _ in range(n):
        out += await stt.feed(_voice())
    return out


def test_end_event_during_inflight_transcription_is_not_lost():
    async def run():
        gate = asyncio.Event()
        h = FakeWhisper(["what is the", "what is the weather"], gate)
        stt = _stt(h)
        first = asyncio.create_task(_feed(stt, 10))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # In-flight call is blocked: more speech arrives, then the VAD
        # reports end of turn.
        for _ in range(12):
            assert await stt.feed(_voice()) == []
        assert await stt.feed(Voice(None)) == []
        gate.set()
        out = await first
        # One Transcript per pass, in order; the last one heard the whole turn
        # (TAG sends that one, see huri-module-tag's tests).
        assert out == [
            Transcript("what is the", False),
            Transcript("what is the weather", True),
        ]
        assert h.calls == [10 * 4, 22 * 4]

    asyncio.run(run())


def test_tail_beyond_first_window_is_transcribed_at_end():
    async def run():
        h = FakeWhisper(["b c d e f"])
        stt = _stt(h)
        # Backlog: more frames buffered than one window, then the turn ends.
        stt.module.buffer = [np.zeros(4, np.float32)] * 14
        stt.module.utterance = list(stt.module.buffer)
        out = await stt.feed(Voice(None))
        assert out == [Transcript("b c d e f", True)]
        assert h.calls == [14 * 4]  # the whole turn, not just the first window
        assert stt.module.buffer == [] and stt.module.utterance == []
        assert stt.module._covered == 0

    asyncio.run(run())


def test_end_of_turn_transcribes_the_whole_utterance():
    async def run():
        h = FakeWhisper(["hello wor", "a b", "Hello world, a b."])
        stt = _stt(h)
        out = await _speak(stt, 15)
        assert out == [Transcript("hello wor", False), Transcript("a b", False)]
        out = await stt.feed(Voice(None))
        assert out == [Transcript("Hello world, a b.", True)]
        # Windows of 10 frames, then one pass over all 15.
        assert h.calls == [10 * 4, 10 * 4, 15 * 4]
        assert stt.module.utterance == []

        # The next turn starts from nothing.
        h.texts = ["yes"]
        await _speak(stt, 3)
        assert await stt.feed(Voice(None)) == [Transcript("yes", True)]
        assert h.calls[-1] == 3 * 4

    asyncio.run(run())


def test_end_without_speech_skips_whisper():
    async def run():
        h = FakeWhisper(["should not be called"])
        stt = _stt(h)
        # The end marker always comes out, or TAG never fires and QAG holds the
        # turn forever.
        assert await stt.feed(Voice(None)) == [Transcript("", True)]
        assert h.calls == []

    asyncio.run(run())


def test_short_utterance_below_one_window_is_still_transcribed():
    async def run():
        h = FakeWhisper(["yes"])
        stt = _stt(h)
        out = await _feed(stt, 3)  # far less than one window / overlap
        assert out == []
        out = await stt.feed(Voice(None))
        assert out == [Transcript("yes", True)]

    asyncio.run(run())
