from dataclasses import dataclass
from typing import Any, Dict, Mapping

from huri_core.core.events import JsonEvent


@dataclass
class Emotion(JsonEvent):
    """Speech emotion: best `label`, its `confidence`, all `scores`; `end`
    marks the utterance's final estimate. EMO emits one per analysis window
    (EMO -> EAG); a client may subscribe to the topic too, to show what the
    server senses while the user speaks.

    Wire: JSON ``{"label": str, "confidence": float, "scores": {label: float},
    "end": bool}``.
    """

    label: str
    confidence: float
    scores: Dict[str, float]
    end: bool

    def to_wire(self) -> Mapping[str, Any]:
        # The classifier hands back numpy floats, which json cannot encode.
        return {
            "label": self.label,
            "confidence": float(self.confidence),
            "scores": {label: float(s) for label, s in self.scores.items()},
            "end": bool(self.end),
        }
