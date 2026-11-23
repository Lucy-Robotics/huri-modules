import re
from typing import Dict, List, Mapping, Optional, Tuple

from huri_events.emotion import EXPRESSIONS, complete_scores

GO_EMOTIONS_EKMAN: Dict[str, str] = {
    "anger": "anger",
    "annoyance": "anger",
    "disapproval": "anger",
    "disgust": "disgust",
    "fear": "fear",
    "nervousness": "fear",
    "joy": "joy",
    "amusement": "joy",
    "approval": "joy",
    "excitement": "joy",
    "gratitude": "joy",
    "love": "joy",
    "optimism": "joy",
    "relief": "calm",
    "pride": "joy",
    "admiration": "joy",
    "desire": "joy",
    "caring": "concern",
    "sadness": "sadness",
    "disappointment": "sadness",
    "embarrassment": "sadness",
    "grief": "sadness",
    "remorse": "concern",
    "surprise": "surprise",
    "realization": "surprise",
    "confusion": "interest",
    "curiosity": "interest",
    "neutral": "neutral",
}

DEFAULT_GAINS: Dict[str, float] = {"anger": 0.3, "disgust": 0.3, "fear": 0.5}

_BOUNDARY = re.compile(r"[.!?…]+[\"')\]]*(?=\s)|\n")


def to_expressions(label_scores: Mapping[str, float]) -> Dict[str, float]:
    grouped = {name: 0.0 for name in EXPRESSIONS}
    for label, score in label_scores.items():
        name = GO_EMOTIONS_EKMAN.get(label.lower())
        if name is not None:
            grouped[name] += float(score)
    total = sum(grouped.values())
    if total <= 0.0:
        return complete_scores({})
    return complete_scores({name: s / total for name, s in grouped.items()})


def apply_gains(
    scores: Mapping[str, float], gains: Mapping[str, float]
) -> Dict[str, float]:
    out = complete_scores(scores)
    for name, gain in gains.items():
        if name == "neutral" or name not in out:
            continue
        kept = out[name] * min(max(float(gain), 0.0), 1.0)
        out["neutral"] += out[name] - kept
        out[name] = kept
    return out


def blend(
    first: Mapping[str, float], second: Mapping[str, float], weight: float
) -> Dict[str, float]:
    a, b = complete_scores(first), complete_scores(second)
    return {name: (1.0 - weight) * a[name] + weight * b[name] for name in EXPRESSIONS}


def split_sentences(
    text: str, min_chars: int, max_chars: int, final: bool = False
) -> Tuple[List[str], str]:
    sentences: List[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        candidate = text[start : match.end()].strip()
        if len(candidate) >= min_chars:
            sentences.append(candidate)
            start = match.end()
    rest = text[start:]
    while len(rest) > max_chars:
        cut = rest.rfind(" ", 0, max_chars)
        cut = cut if cut > 0 else max_chars
        sentences.append(rest[:cut].strip())
        rest = rest[cut:]
    if final:
        if rest.strip():
            sentences.append(rest.strip())
        rest = ""
    return [s for s in sentences if s], rest


class Reactions:
    def __init__(self) -> None:
        self._scores: Dict[str, Dict[str, float]] = {}

    def set(self, user_id: str, scores: Mapping[str, float]) -> None:
        self._scores[user_id] = dict(scores)

    def get(self, user_id: str) -> Optional[Dict[str, float]]:
        return self._scores.get(user_id)

    def forget(self, user_id: str) -> None:
        self._scores.pop(user_id, None)


REACTIONS = Reactions()
