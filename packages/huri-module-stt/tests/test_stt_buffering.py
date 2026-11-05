"""STT's buffering under load, with frames that name themselves.

Each frame's samples are all set to the frame's index, so the distinct values
in a submitted window say exactly which frames the model heard. What must
hold whatever the timing: every frame of a turn reaches the model, the
end-of-turn transcript is never lost nor unfinished by a late frame, and the
mid-turn windows keep their cadence.
"""

import asyncio

import numpy as np
from huri_events.speech import Voice
from huri_stt.module import STT

from huri_core.testing import LocalHandle, ModuleHarness

BLOCK_DURATION = 0.030
SAMPLE_RATE = 16000
BLOCK = int(BLOCK_DURATION * SAMPLE_RATE)


class RecordingWhisper:
    """Keeps every window it was given; `gate` holds the calls open so a
    backlog builds up deterministically."""

    def __init__(self, gate=None):
        self.seen = []
        self.gate = gate

    async def transcribe(self, audio, language="en"):
        if self.gate is not None:
            await self.gate.wait()
        self.seen.append(np.asarray(audio).copy())
        return f"t{len(self.seen)}"

    @property
    def frames_seen(self):
        """Indices of every frame that reached the model, over all calls."""

        out = set()
        for chunk in self.seen:
            out.update(int(v) for v in np.unique(chunk))
        return out


def _stt(whisper):
    return ModuleHarness(
        STT,
        handle=LocalHandle(whisper),
        language="en",
        sample_rate=SAMPLE_RATE,
        block_duration=BLOCK_DURATION,
    )


def _frame(index):
    return Voice(np.full(BLOCK, float(index), dtype=np.float32))


def test_every_frame_of_the_turn_reaches_the_model():
    """A transcription slower than the audio builds a backlog; the end of the
    turn must not throw the backlog away."""

    async def run():
        gate = asyncio.Event()
        whisper = RecordingWhisper(gate)
        stt = _stt(whisper)

        frames = 200  # 6 s at 30 ms, well over one 2 s window
        tasks = [asyncio.create_task(stt.feed(_frame(i))) for i in range(frames)]
        await asyncio.sleep(0)  # all queued behind the in-flight window
        gate.set()
        tasks.append(asyncio.create_task(stt.feed(Voice(None))))
        await asyncio.gather(*tasks)

        never_seen = sorted(set(range(frames)) - whisper.frames_seen)
        assert not never_seen, f"{len(never_seen)} frames never transcribed"

    asyncio.run(run())


def test_the_end_flag_is_not_stolen_by_a_late_frame():
    """A frame landing while the final pass runs belongs to the next turn and
    must not turn the finished transcript back into a partial one."""

    async def run():
        gate = asyncio.Event()
        gate.set()
        whisper = RecordingWhisper(gate)
        stt = _stt(whisper)

        for i in range(70):  # one full window and a bit
            await stt.feed(_frame(i))

        gate.clear()  # hold the final pass open
        final = asyncio.create_task(stt.feed(Voice(None)))
        await asyncio.sleep(0)
        late = asyncio.create_task(stt.feed(_frame(999)))
        await asyncio.sleep(0)
        gate.set()
        final_out, late_out = await asyncio.gather(final, late)

        assert [t.end for t in final_out] == [True]
        assert late_out == []
        # The late frame is the start of the next turn.
        assert [int(f[0]) for f in stt.module.utterance] == [999]

    asyncio.run(run())


def test_mid_turn_windows_keep_their_cadence():
    """2 s windows every 1 s of speech: what a visitor reads as live captions."""

    async def run():
        whisper = RecordingWhisper()
        stt = _stt(whisper)
        for i in range(200):
            await stt.feed(_frame(i))

        assert len(whisper.seen) == 5
        assert {len(chunk) // BLOCK for chunk in whisper.seen} == {66}

    asyncio.run(run())


def test_silence_alone_never_calls_the_model():
    async def run():
        whisper = RecordingWhisper()
        stt = _stt(whisper)
        for _ in range(5):
            out = await stt.feed(Voice(None))
            assert [t.end for t in out] == [True]
        assert whisper.seen == []

    asyncio.run(run())
