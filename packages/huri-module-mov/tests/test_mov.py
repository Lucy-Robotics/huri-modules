"""MOV's audio windowing, with EMAGE replaced by a fake deployment."""

import asyncio

import numpy as np
from huri_events.audio import Audio
from huri_events.motion import Motion
from huri_mov.module import MOV

from huri_core.testing import LocalHandle, ModuleHarness

SR = 16000


class FakeEmage:
    """30 fps of zero poses covering the whole window it is given."""

    def __init__(self):
        self.windows = []

    async def infer(self, window, sr):
        self.windows.append(len(window))
        frames = int(round(len(window) / sr * 30))
        return Motion(
            poses=np.zeros((frames, 165), dtype=np.float32),
            expressions=np.zeros((frames, 100), dtype=np.float32),
            trans=np.zeros((frames, 3), dtype=np.float32),
        )


def chunk(seconds, end=False):
    return Audio(np.zeros(int(seconds * SR), dtype=np.float32), SR, end)


def mov(**args):
    emage = FakeEmage()
    return emage, ModuleHarness(MOV, handle=LocalHandle(emage), **args)


def test_waits_for_enough_fresh_audio_before_inferring():
    emage, harness = mov(min_chunk_sec=0.5, context_sec=1.0)
    assert harness.run(chunk(0.2), chunk(0.2)) == []
    assert emage.windows == []
    (motion,) = harness.run(chunk(0.2))  # 0.6 s accumulated
    assert motion.poses.shape[0] == 18


def test_end_of_utterance_flushes_the_tail_and_resets():
    emage, harness = mov(min_chunk_sec=0.5, context_sec=1.0)

    async def utterance():
        out = await harness.feed_all(chunk(0.6), chunk(0.2, end=True))
        return out, harness.module._sr

    out, sample_rate = asyncio.run(utterance())
    assert [m.poses.shape[0] for m in out] == [18, 6]  # only the new 0.2 s
    assert emage.windows[1] == int(0.8 * SR)  # inferred with context
    assert sample_rate is None  # ready for the next utterance


def test_long_audio_is_inferred_in_short_windows():
    """A whole sentence at once: its gestures come window by window."""
    emage, harness = mov(min_chunk_sec=0.5, context_sec=1.0, max_chunk_sec=1.0)
    out = harness.run(chunk(3.4))
    assert [m.poses.shape[0] for m in out] == [30, 30, 42]  # 1, 1, then 1.4 s
    assert [m.pts for m in out] == [0.0, 1.0, 2.0]
    # Each window: up to 1 s of context, then its fresh audio.
    assert emage.windows == [int(s * SR) for s in (1.0, 2.0, 2.4)]


def test_short_windows_can_be_turned_off():
    emage, harness = mov(min_chunk_sec=0.5, context_sec=1.0, max_chunk_sec=0)
    (motion,) = harness.run(chunk(6.4))
    assert motion.poses.shape[0] == 192


class SlowEmage(FakeEmage):
    """Takes `cost` seconds of a fake clock per window."""

    def __init__(self, clock, cost):
        super().__init__()
        self.clock, self.cost = clock, cost

    async def infer(self, window, sr):
        self.clock[0] += self.cost
        return await super().infer(window, sr)


def test_gestures_behind_the_voice_skip_ahead(monkeypatch):
    """Inference slower than realtime: rather than gestures for words long
    gone, the next window starts where the voice is."""
    clock = [0.0]
    monkeypatch.setattr(MOV, "_clock", staticmethod(lambda: clock[0]))
    emage = SlowEmage(clock, cost=1.5)
    harness = ModuleHarness(
        MOV,
        handle=LocalHandle(emage),
        min_chunk_sec=0.5,
        context_sec=1.0,
        max_chunk_sec=1.0,
        max_behind_sec=1.0,
    )
    out = harness.run(chunk(6.4))
    # Heard at 1.5 s, 3 s, then 4.5 s: 1.5 s behind, so on from 4.5 + 0.5 s.
    assert [m.pts for m in out] == [0.0, 1.0, 2.0, 5.0]
    assert out[-1].poses.shape[0] == 42  # 5.0 to 6.4 s
