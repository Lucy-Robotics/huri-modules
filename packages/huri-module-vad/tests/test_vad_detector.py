"""VAD with the real WebRTC detector, on synthetic speech (speech_signals.py).

test_vad.py scripts the detector's decisions; these tests let it decide, so
they also guard that the synthetic signal still reads as speech to it. What
the rest of the pipeline relies on: ``Voice(None)`` exactly once per
utterance (EMO, and through EAG/QAG the whole turn, depend on it) and nothing
at all before anyone spoke.
"""

from huri_events.speech import Voice
from huri_vad.module import VAD
from speech_signals import SAMPLE_RATE, frames, silence, speech_like

from huri_core.core.events import BytesEvent
from huri_core.testing import ModuleHarness

BLOCK_DURATION = 0.030
BLOCK = int(BLOCK_DURATION * SAMPLE_RATE)


def _vad(**kwargs):
    params = dict(
        vad_agressiveness=3,
        silence_duration=0.3,
        sample_rate=SAMPLE_RATE,
        block_duration=BLOCK_DURATION,
    )
    params.update(kwargs)
    return ModuleHarness(VAD, **params)


def _feed(vad, *audios):
    events = [BytesEvent(f.tobytes()) for audio in audios for f in frames(audio, BLOCK)]
    return vad.run(*events)


def _markers(out):
    return [v for v in out if v.data is None]


def test_synthetic_speech_reads_as_speech():
    out = _feed(_vad(), speech_like(1.0))
    voiced = [v for v in out if v.data is not None]
    assert len(voiced) > len(frames(speech_like(1.0), BLOCK)) * 0.8, (
        "the synthetic signal must read as speech to webrtcvad, or every test "
        "in this file passes vacuously"
    )
    assert all(isinstance(v, Voice) and v.data.dtype.name == "float32" for v in voiced)


def test_end_of_turn_is_emitted_exactly_once():
    out = _feed(_vad(), speech_like(0.6), silence(2.0))
    assert len(_markers(out)) == 1


def test_no_end_of_turn_before_any_speech():
    assert _feed(_vad(), silence(3.0)) == []


def test_two_turns_each_get_their_own_marker():
    out = _feed(_vad(), speech_like(0.6), silence(2.0), speech_like(0.6), silence(2.0))
    assert len(_markers(out)) == 2
    # Speech, marker, speech, marker: the markers close their own turn.
    kinds = ["end" if v.data is None else "voice" for v in out]
    assert kinds.index("end") < len(kinds) - 1 - kinds[::-1].index("voice")
