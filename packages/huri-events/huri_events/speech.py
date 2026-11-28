from dataclasses import dataclass
from typing import Any, Mapping, Optional

import numpy as np

from huri_core.core.events import JsonEvent


@dataclass
class Transcript(JsonEvent):
    """Text recognized from speech; `end` closes the utterance.

    Wire: JSON ``{"text": str, "end": bool}``.
    """

    text: str
    end: bool


@dataclass
class Voice(JsonEvent):
    """A frame of detected speech (float32 samples), or `data=None` to mark the
    end of the utterance. Modules pass it as is (VAD -> STT, EMO); a client may
    subscribe too (one frame per VAD block, 50/s by default, while speaking).

    Wire: JSON ``{"data": [float, ...] | null}``.
    """

    data: Optional[np.ndarray]

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> "Voice":
        samples = data["data"]
        return cls(None if samples is None else np.asarray(samples, np.float32))

    def to_wire(self) -> Mapping[str, Any]:
        # json cannot encode an ndarray; tolist() gives plain floats.
        return {"data": None if self.data is None else self.data.tolist()}

    def summarize(self) -> str:
        """Short repr that avoids dumping full numpy arrays into the log."""

        if self.data is not None:
            cls = type(self).__name__
            return f"{cls}(shape={self.data.shape}, dtype={self.data.dtype})"
        else:
            return super().summarize()
