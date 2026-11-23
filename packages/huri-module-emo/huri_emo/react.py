from typing import Dict, Mapping, Optional

from huri_emo.expression import REACTIONS
from huri_events.emotion import EXPRESSIONS, Emotion, Expression, complete_scores
from huri_events.question import RAGQuestion

from huri_core.core.module import ModuleWithId

DEFAULT_REACTIONS: Dict[str, Dict[str, float]] = {
    "neu": {"interest": 0.3},
    "neutral": {"interest": 0.3},
    "hap": {"joy": 1.0},
    "happy": {"joy": 1.0},
    "joy": {"joy": 1.0},
    "sad": {"concern": 0.7, "sadness": 0.3},
    "sadness": {"concern": 0.7, "sadness": 0.3},
    "ang": {"calm": 0.6},
    "angry": {"calm": 0.6},
    "anger": {"calm": 0.6},
    "fea": {"calm": 0.5, "concern": 0.3},
    "fear": {"calm": 0.5, "concern": 0.3},
    "sur": {"surprise": 1.0},
    "surprise": {"surprise": 1.0},
    "dis": {"concern": 0.3},
    "disgust": {"concern": 0.3},
}


class EmoReact(ModuleWithId):
    input_type = "huri.question"
    output_type = "huri.emotion.out"
    input_event = RAGQuestion
    output_event = Expression

    def __init__(
        self,
        _user_id: str,
        reactions: Optional[Mapping[str, Mapping[str, float]]] = None,
        strength: float = 0.6,
    ):
        super().__init__(_user_id)
        if not 0.0 <= strength <= 1.0:
            raise ValueError(f"strength must be within [0, 1], got {strength}")
        table = DEFAULT_REACTIONS if reactions is None else reactions
        self.reactions = {
            label.lower(): complete_scores(scores) for label, scores in table.items()
        }
        self.strength = float(strength)

    def react(self, emotion: Optional[Emotion]) -> Dict[str, float]:
        if emotion is None:
            return complete_scores({})
        felt = dict(emotion.scores) or {emotion.label: emotion.confidence}
        out = {name: 0.0 for name in EXPRESSIONS if name != "neutral"}
        for label, score in felt.items():
            reaction = self.reactions.get(label.lower())
            if reaction is None:
                continue
            for name in out:
                out[name] += float(score) * reaction[name]
        return complete_scores({n: self.strength * s for n, s in out.items()})

    async def process(self, question: RAGQuestion) -> Expression:
        scores = self.react(question.emotion)
        REACTIONS.set(self._user_id, scores)
        return Expression.from_scores(scores, "reaction")

    async def finalize(self) -> None:
        REACTIONS.forget(self._user_id)
