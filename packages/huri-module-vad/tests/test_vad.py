"""VAD's speech/silence logic, with WebRTC's detector scripted."""

import numpy as np
import pytest
from huri_events.speech import Voice
from huri_vad.module import VAD

from huri_core.core.events import BytesEvent
from huri_core.testing import ModuleHarness

FRAME = (np.full(320, 16384, dtype=np.int16)).tobytes()  # 20 ms at 16 kHz


class Scripted:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def is_speech(self, frame, sample_rate):
        return self.decisions.pop(0)


def vad(decisions, silence_duration=0.05):
    harness = ModuleHarness(VAD, silence_duration=silence_duration)
    harness.module.vad = Scripted(decisions)
    return harness


def test_speech_frames_become_normalized_voice():
    (voice,) = vad([True]).run(BytesEvent(FRAME))
    assert voice.data.dtype == np.float32 and voice.data.size == 320
    assert voice.data[0] == pytest.approx(0.5)


def test_end_of_utterance_is_sent_once_after_enough_silence():
    # 0.05 s of silence = more than 2 frames of 20 ms.
    out = vad([True] + [False] * 6).run(*[BytesEvent(FRAME)] * 7)
    assert [v.data is None for v in out] == [False, True]


def test_silence_before_any_speech_sends_nothing():
    assert vad([False] * 5).run(*[BytesEvent(FRAME)] * 5) == []


def test_block_duration_must_be_one_webrtc_accepts():
    with pytest.raises(RuntimeError, match="block duration"):
        ModuleHarness(VAD, block_duration=0.025)


def test_real_detector_runs_on_a_frame():
    out = ModuleHarness(VAD).run(BytesEvent(bytes(640)))  # digital silence
    assert out == [] or isinstance(out[0], Voice)


class Recording(Scripted):
    def __init__(self, decisions):
        super().__init__(decisions)
        self.frames = []

    def is_speech(self, frame, sample_rate):
        self.frames.append(len(frame))
        return super().is_speech(frame, sample_rate)


def test_chunks_of_any_size_are_recut_into_frames_webrtc_accepts():
    """WebRTC raises on any other frame size; a client may send any chunk
    size, down to an odd number of bytes."""
    harness = ModuleHarness(VAD, silence_duration=0.05)
    harness.module.vad = detector = Recording([True] * 4)
    audio = FRAME * 3 + FRAME[:100]

    out = harness.run(
        *(BytesEvent(audio[i : i + 333]) for i in range(0, len(audio), 333))
    )

    assert detector.frames == [640, 640, 640]  # the last 100 bytes wait
    assert [v.data.size for v in out] == [320, 320, 320]
    harness.run(BytesEvent(FRAME[100:]))  # completes the 4th frame
    assert detector.frames == [640] * 4


def test_one_chunk_holding_speech_then_silence_ends_the_utterance():
    out = vad([True] + [False] * 6).run(BytesEvent(FRAME * 7))
    assert [v.data is None for v in out] == [False, True]


def test_real_detector_takes_an_odd_sized_chunk():
    out = ModuleHarness(VAD).run(BytesEvent(bytes(1001)), BytesEvent(bytes(279)))
    assert out == [] or all(isinstance(v, Voice) for v in out)
