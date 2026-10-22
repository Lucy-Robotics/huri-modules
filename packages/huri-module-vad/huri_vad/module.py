from typing import AsyncGenerator, Optional

import numpy as np
import webrtcvad
from huri_events.speech import Voice

from huri_core.core.events import BytesEvent
from huri_core.core.module import Module


class VAD(Module):
    """VAD Module

    Detect voice and silence using WebRTC VAD.

    input: huri.audio.in,
    output: huri.voice

    :vad_agressiveness: from 0 (low) to 3 (high, can distord audio).
    :silence_duration: how many seconds will a no voice be considered a silence.
    :sample_rate: size of received chunk of audio. Usually 8000, 16000 or 48000.
    :block_duration: size of the frames WebRTC analyses (in s), 0.010, 0.020
        or 0.030. Received chunks of any size are re-cut to it.
    """

    # Inbound microphone frames travel on their own topic so the TTS-output
    # "audio" topic (consumed by MOV and the client Sender) never collides
    # with mic input — otherwise raw mic bytes get echoed back to the client.
    input_type = "huri.audio.in"
    output_type = "huri.voice"
    input_event = BytesEvent
    output_event = Voice

    def __init__(
        self,
        vad_agressiveness: int = 3,
        silence_duration: float = 3,  # s
        sample_rate: int = 16000,
        block_duration: float = 0.020,  # s
    ):
        super().__init__()

        if block_duration not in [0.010, 0.020, 0.030]:
            raise RuntimeError("block duration must be 0.010, 0.020 or 0.030 s")

        self.sample_rate: int = sample_rate
        self.block_size: int = int(block_duration * sample_rate)  # ms

        self.silence_frames_size: int = int(silence_duration * sample_rate)
        self.silence_frames_count: int = -1

        self.vad = webrtcvad.Vad(vad_agressiveness)
        # WebRTC only takes frames of exactly block_size samples; a client may
        # send any size, so the bytes are re-cut here and a partial frame waits
        # for the next chunk.
        self._pending = b""

    async def process(self, data: BytesEvent) -> AsyncGenerator[Voice, None]:
        self._pending += data.data
        frame_bytes = self.block_size * 2  # int16
        while len(self._pending) >= frame_bytes:
            frame = self._pending[:frame_bytes]
            self._pending = self._pending[frame_bytes:]
            voice = self._frame(frame)
            if voice is not None:
                yield voice

    def _frame(self, frame: bytes) -> Optional[Voice]:
        if self.vad.is_speech(frame, self.sample_rate) is True:
            self.silence_frames_count = 0

            audio_array = np.frombuffer(frame, dtype=np.int16)
            audio_array_float = audio_array.astype(np.float32) / 32768.0

            return Voice(audio_array_float)
        else:
            if self.silence_frames_count != -1:
                self.silence_frames_count += self.block_size
            if self.silence_frames_count > self.silence_frames_size:
                self.silence_frames_count = -1  # sent only once

                return Voice(None)
            return None
