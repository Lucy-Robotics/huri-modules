from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from huri_events.face import ARKIT_INDEX, N_SHAPES

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


EXPRESSIONS = (
    "neutral",
    "joy",
    "sadness",
    "surprise",
    "fear",
    "anger",
    "disgust",
    "concern",
    "calm",
    "interest",
)
LABEL_MIN_SCORE = 0.1

AFFECT: Dict[str, Tuple[float, float]] = {
    "neutral": (0.0, 0.0),
    "joy": (0.8, 0.5),
    "sadness": (-0.7, -0.4),
    "surprise": (0.2, 0.8),
    "fear": (-0.7, 0.6),
    "anger": (-0.6, 0.7),
    "disgust": (-0.6, 0.3),
    "concern": (-0.2, 0.1),
    "calm": (0.4, -0.5),
    "interest": (0.3, 0.3),
}

FACE: Dict[str, Dict[str, float]] = {
    "neutral": {},
    "joy": {
        "mouthSmileLeft": 0.8,
        "mouthSmileRight": 0.8,
        "cheekSquintLeft": 0.5,
        "cheekSquintRight": 0.5,
        "eyeSquintLeft": 0.3,
        "eyeSquintRight": 0.3,
    },
    "sadness": {
        "browInnerUp": 0.7,
        "browDownLeft": 0.2,
        "browDownRight": 0.2,
        "mouthFrownLeft": 0.6,
        "mouthFrownRight": 0.6,
        "mouthShrugLower": 0.3,
        "eyeLookDownLeft": 0.2,
        "eyeLookDownRight": 0.2,
    },
    "surprise": {
        "browInnerUp": 0.8,
        "browOuterUpLeft": 0.8,
        "browOuterUpRight": 0.8,
        "eyeWideLeft": 0.6,
        "eyeWideRight": 0.6,
        "jawOpen": 0.25,
    },
    "fear": {
        "browInnerUp": 0.7,
        "browOuterUpLeft": 0.4,
        "browOuterUpRight": 0.4,
        "browDownLeft": 0.3,
        "browDownRight": 0.3,
        "eyeWideLeft": 0.7,
        "eyeWideRight": 0.7,
        "mouthStretchLeft": 0.5,
        "mouthStretchRight": 0.5,
    },
    "anger": {
        "browDownLeft": 0.8,
        "browDownRight": 0.8,
        "eyeSquintLeft": 0.4,
        "eyeSquintRight": 0.4,
        "mouthPressLeft": 0.5,
        "mouthPressRight": 0.5,
        "noseSneerLeft": 0.2,
        "noseSneerRight": 0.2,
    },
    "disgust": {
        "noseSneerLeft": 0.7,
        "noseSneerRight": 0.7,
        "mouthUpperUpLeft": 0.5,
        "mouthUpperUpRight": 0.5,
        "browDownLeft": 0.3,
        "browDownRight": 0.3,
        "cheekSquintLeft": 0.3,
        "cheekSquintRight": 0.3,
    },
    "concern": {
        "browInnerUp": 0.6,
        "browDownLeft": 0.1,
        "browDownRight": 0.1,
        "mouthPressLeft": 0.2,
        "mouthPressRight": 0.2,
    },
    "calm": {
        "mouthSmileLeft": 0.25,
        "mouthSmileRight": 0.25,
        "eyeSquintLeft": 0.1,
        "eyeSquintRight": 0.1,
        "eyeBlinkLeft": 0.1,
        "eyeBlinkRight": 0.1,
    },
    "interest": {
        "browInnerUp": 0.2,
        "browOuterUpLeft": 0.3,
        "browOuterUpRight": 0.3,
        "eyeWideLeft": 0.15,
        "eyeWideRight": 0.15,
    },
}


def complete_scores(scores: Mapping[str, float]) -> Dict[str, float]:
    unknown = set(scores) - set(EXPRESSIONS)
    if unknown:
        raise ValueError(f"unknown expressions {sorted(unknown)}, use {EXPRESSIONS}")
    out = {name: max(0.0, float(scores.get(name, 0.0))) for name in EXPRESSIONS}
    total = sum(out.values())
    if total > 1.0:
        out = {name: s / total for name, s in out.items()}
    else:
        out["neutral"] += 1.0 - total
    return out


@dataclass
class Expression(JsonEvent):
    label: str
    intensity: float
    scores: Dict[str, float]
    valence: float
    arousal: float
    source: str
    text: Optional[str] = None
    end: bool = False

    @classmethod
    def from_scores(
        cls,
        scores: Mapping[str, float],
        source: str,
        text: Optional[str] = None,
        end: bool = False,
    ) -> "Expression":
        full = complete_scores(scores)
        shown = max(EXPRESSIONS[1:], key=lambda name: full[name])
        label = shown if full[shown] >= LABEL_MIN_SCORE else "neutral"
        return cls(
            label=label,
            intensity=1.0 - full["neutral"],
            scores=full,
            valence=sum(full[n] * AFFECT[n][0] for n in EXPRESSIONS),
            arousal=sum(full[n] * AFFECT[n][1] for n in EXPRESSIONS),
            source=source,
            text=text,
            end=end,
        )

    def face(self) -> np.ndarray:
        weights = np.zeros(N_SHAPES, dtype=np.float32)
        for name, score in self.scores.items():
            for shape, weight in FACE.get(name, {}).items():
                weights[ARKIT_INDEX[shape]] += score * weight
        return np.clip(weights, 0.0, 1.0)

    def to_wire(self) -> Mapping[str, Any]:
        return {
            "label": self.label,
            "intensity": float(self.intensity),
            "scores": {name: float(s) for name, s in self.scores.items()},
            "valence": float(self.valence),
            "arousal": float(self.arousal),
            "source": self.source,
            "text": self.text,
            "end": bool(self.end),
        }
