import asyncio
import os
import traceback
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional, Tuple

import numpy as np
from huri_events.audio import Audio
from huri_lips.config import models_dir
from huri_lips.phonemes import Phoneme
from huri_tts.piper.handle import _VOICE_PATH, PiperTTSHandle
from huri_tts.streaming import StreamingTTS
from ray import serve


def aligned_voice(voice: str = _VOICE_PATH) -> Path:
    return models_dir() / (Path(voice).stem + ".aligned.onnx")


_PiperTTS: Any = getattr(PiperTTSHandle, "func_or_class")


@serve.deployment(name="PhonemeTTSHandle")
class PhonemeTTSHandle(_PiperTTS):
    def __init__(
        self,
        voice_path: Optional[str] = None,
        length_scale: Optional[float] = None,
        volume: Optional[float] = None,
    ):
        voice_path = voice_path or os.environ.get("HURI_LIPS_VOICE") or ""
        if not voice_path:
            aligned = aligned_voice()
            voice_path = str(aligned) if aligned.is_file() else _VOICE_PATH
        super().__init__(voice_path, length_scale, volume)
        print(f"[PhonemeTTS] {voice_path}")

    async def stream_audio(self, session_id: str) -> AsyncGenerator[Audio, None]:
        queue = self._queues.setdefault(session_id, asyncio.Queue())
        loop = asyncio.get_running_loop()
        pts = 0.0
        try:
            while True:
                phrase = await queue.get()
                if phrase is None:
                    yield Audio(
                        data=np.zeros(0, dtype=np.float32),
                        sample_rate=self._sample_rate,
                        end=True,
                        pts=pts,
                    )
                    return
                chunks = await loop.run_in_executor(None, self._synthesize, phrase)
                for samples, phonemes in chunks:
                    if samples.size == 0:
                        continue
                    audio = Audio(
                        data=samples, sample_rate=self._sample_rate, end=False, pts=pts
                    )
                    setattr(audio, "phonemes", phonemes)
                    yield audio
                    pts += samples.size / self._sample_rate
        except Exception:
            traceback.print_exc()
            raise
        finally:
            self._buffers.pop(session_id, None)
            self._queues.pop(session_id, None)

    def _synthesize(
        self, phrase: str
    ) -> List[Tuple[np.ndarray, Optional[List[Phoneme]]]]:
        out: List[Tuple[np.ndarray, Optional[List[Phoneme]]]] = []
        for chunk in self._voice.synthesize(
            phrase, syn_config=self._syn_config, include_alignments=True
        ):
            samples = np.asarray(chunk.audio_float_array, dtype=np.float32)
            phonemes: Optional[List[Phoneme]] = None
            if chunk.phoneme_alignments:
                marks: List[Phoneme] = []
                t = 0.0
                for a in chunk.phoneme_alignments:
                    d = a.num_samples / chunk.sample_rate
                    marks.append((a.phoneme, t, t + d))
                    t += d
                phonemes = marks
            out.append((samples, phonemes))
        return out


class PhonemeTTS(StreamingTTS):
    _handle_cls = PhonemeTTSHandle
