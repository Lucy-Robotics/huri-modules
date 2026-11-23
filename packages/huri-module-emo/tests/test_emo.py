import asyncio

import numpy as np
import pytest
from huri_events.speech import Voice

from huri_core.testing import LocalHandle, ModuleHarness


class FakeHandle:
    """Stands in for EMOHandle: a fixed emotion per call; can block the first."""

    def __init__(self, gate=None):
        self.gate = gate
        self.calls = []

    async def predict(self, audio):
        self.calls.append(len(audio))
        if self.gate is not None and len(self.calls) == 1:
            await self.gate.wait()
        return {"label": "neu", "confidence": 0.9, "scores": {"neu": 0.9, "ang": 0.1}}


def _emo(handle):
    from huri_emo.module import EMO

    # 20 ms blocks -> window of 10 frames (fast tests).
    return ModuleHarness(EMO, handle=LocalHandle(handle), analysis_window=0.2)


async def _collect(emo, voice):
    return await emo.feed(voice)


def _voice():
    return Voice(np.zeros(4, dtype=np.float32))


def test_interim_emotion_once_window_is_full():
    asyncio.run(_test_interim_emotion_once_window_is_full_impl())


async def _test_interim_emotion_once_window_is_full_impl():
    handle = FakeHandle()
    emo = _emo(handle)

    results = []
    for _ in range(10):
        results += await _collect(emo, _voice())

    assert [e.end for e in results] == [False]
    assert handle.calls == [40]


def test_end_marker_always_yields_final_emotion():
    asyncio.run(_test_end_marker_always_yields_final_emotion_impl())


async def _test_end_marker_always_yields_final_emotion_impl():
    handle = FakeHandle()
    emo = _emo(handle)

    # Very short turn: no frames at all still produces Emotion(end=True).
    results = await _collect(emo, Voice(None))

    assert [e.end for e in results] == [True]
    assert results[0].label == "neu"


def test_end_marker_during_inflight_analysis_is_not_dropped():
    asyncio.run(_test_end_marker_during_inflight_analysis_is_not_dropped_impl())


async def _test_end_marker_during_inflight_analysis_is_not_dropped_impl():
    gate = asyncio.Event()
    handle = FakeHandle(gate)
    emo = _emo(handle)

    for _ in range(9):
        await _collect(emo, _voice())

    # 10th frame starts an analysis that blocks on the gate.
    inflight = asyncio.create_task(_collect(emo, _voice()))
    await asyncio.sleep(0)

    # The single end marker lands mid-analysis: it must be remembered.
    assert await _collect(emo, Voice(None)) == []
    gate.set()

    results = await inflight
    assert [e.end for e in results] == [False, True]


def test_interrupted_emo_request_cancels_ray_response():
    class RayResponse:
        cancel_called = False

        def __await__(self):
            return asyncio.get_running_loop().create_future().__await__()

        def cancel(self):
            self.cancel_called = True

    class RayHandle:
        def __init__(self):
            self.started = asyncio.Event()
            self.response = RayResponse()
            self.predict = self

        def remote(self, audio):
            self.started.set()
            return self.response

    async def run():
        from huri_emo.module import EMO

        handle = RayHandle()
        emo = ModuleHarness(EMO, handle=handle, analysis_window=0.2)
        # Fill the buffer until the next frame triggers analysis.
        for _ in range(9):
            await emo.feed(_voice())

        task = asyncio.create_task(emo.feed(_voice()))
        await handle.started.wait()

        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert handle.response.cancel_called

    asyncio.run(run())
