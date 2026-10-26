"""Synthetic audio that webrtcvad accepts as speech.

Measured against ``webrtcvad.Vad(3)`` on 30 ms frames at 16 kHz:

===========================================  ================
signal                                       speech frames
===========================================  ================
pure 200 Hz sine                             0.09 - 0.15
white noise                                  1.00
5-harmonic stack with 4 Hz AM (below)        1.00
silence (zeros)                              0.00
===========================================  ================

A sine tone is the obvious thing to reach for and it does not work: a test
built on one passes vacuously because the detector never fires. Hence the
harmonic stack, which has the spectral spread and the syllable-rate envelope
webrtcvad keys on, and needs no model and no audio fixture.
"""

import numpy as np

SAMPLE_RATE = 16000


def speech_like(seconds, sample_rate=SAMPLE_RATE):
    """Voiced-sounding int16 audio: harmonic stack with a syllable-rate envelope."""

    t = np.arange(int(seconds * sample_rate), dtype=np.float32) / sample_rate
    wave = np.zeros_like(t)
    for harmonic, amplitude in ((1, 1.0), (2, 0.6), (3, 0.4), (4, 0.25), (6, 0.15)):
        wave += amplitude * np.sin(2 * np.pi * 120.0 * harmonic * t)
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 4.0 * t)
    wave *= envelope
    wave /= np.max(np.abs(wave)) or 1.0
    return (wave * 0.6 * 32767).astype(np.int16)


def silence(seconds, sample_rate=SAMPLE_RATE):
    return np.zeros(int(seconds * sample_rate), dtype=np.int16)


def frames(audio, block_size):
    count = len(audio) // block_size
    return [audio[i * block_size : (i + 1) * block_size] for i in range(count)]
