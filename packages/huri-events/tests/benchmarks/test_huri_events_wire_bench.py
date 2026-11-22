"""What this protects: the wire cost of the curated events a client receives
on every turn stays visible, and each still decodes to what it was made from."""

import numpy as np
import pytest
from huri_events import Audio, Motion, Token

AUDIO = Audio(np.zeros(320, dtype=np.float32), 16000)  # 20 ms at 16 kHz
MOTION = Motion(  # 1 s of gestures at 30 fps
    poses=np.zeros((30, 165), dtype=np.float32),
    expressions=np.zeros((30, 100), dtype=np.float32),
    trans=np.zeros((30, 3), dtype=np.float32),
)
TOKEN = Token("hello", False)


@pytest.mark.parametrize(
    "event", [AUDIO, MOTION, TOKEN], ids=["audio-20ms", "motion-1s", "token"]
)
def test_a_curated_events_wire_round_trip(benchmark, event):
    back = benchmark(lambda: type(event).from_wire(event.to_wire()))
    assert type(back) is type(event)
    for name, value in vars(event).items():
        if isinstance(value, np.ndarray):
            assert np.array_equal(getattr(back, name), value)
        else:
            assert getattr(back, name) == value
