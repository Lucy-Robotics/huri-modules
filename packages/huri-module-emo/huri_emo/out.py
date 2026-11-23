import asyncio
import os
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Mapping, Optional

from huri_emo.expression import (
    DEFAULT_GAINS,
    REACTIONS,
    apply_gains,
    blend,
    split_sentences,
    to_expressions,
)
from huri_events.emotion import Expression
from huri_events.text import Token
from ray import serve

from huri_core.core.module import ModuleWithHandle, ModuleWithId

if TYPE_CHECKING:
    from ray.serve import handle

_MODEL_NAME = os.environ.get("HURI_EMO_OUT_MODEL", "SamLowe/roberta-base-go_emotions")


@serve.deployment(name="EmoOutHandle", max_ongoing_requests=8)
class EmoOutHandle:
    def __init__(self, model_name: str = _MODEL_NAME, device: str = "cpu"):
        from transformers import pipeline

        self.classifier = pipeline(
            "text-classification", model=model_name, top_k=None, device=device
        )

    async def classify(self, texts: List[str]) -> List[Dict[str, float]]:
        return await asyncio.to_thread(self._classify_sync, texts)

    def _classify_sync(self, texts: List[str]) -> List[Dict[str, float]]:
        if not texts:
            return []
        results: List[Any] = self.classifier(texts, truncation=True)
        return [
            to_expressions({item["label"]: item["score"] for item in labels})
            for labels in results
        ]


class EmoOut(ModuleWithHandle, ModuleWithId):
    _handle_cls = EmoOutHandle
    input_type = "huri.token"
    output_type = "huri.emotion.out"
    input_event = Token
    output_event = Expression

    def __init__(
        self,
        _handle: "handle.DeploymentHandle",
        _user_id: str,
        empathy: float = 0.3,
        gains: Optional[Mapping[str, float]] = None,
        min_chars: int = 12,
        max_chars: int = 200,
    ):
        super().__init__(_handle=_handle, _user_id=_user_id)
        if not 0.0 <= empathy <= 1.0:
            raise ValueError(f"empathy must be within [0, 1], got {empathy}")
        if min_chars < 0 or max_chars <= min_chars:
            raise ValueError("min_chars must be >= 0 and max_chars > min_chars")
        self.empathy = float(empathy)
        self.gains = dict(DEFAULT_GAINS if gains is None else gains)
        self.min_chars = int(min_chars)
        self.max_chars = int(max_chars)
        self._buffer = ""
        self._last: Optional[Expression] = None
        self._lock = asyncio.Lock()

    def _express(self, text: str, scores: Mapping[str, float]) -> Expression:
        shown = apply_gains(scores, self.gains)
        reaction = REACTIONS.get(self._user_id)
        if reaction is not None:
            shown = blend(shown, reaction, self.empathy)
        return Expression.from_scores(shown, "reply", text=text)

    async def process(self, token: Token) -> AsyncGenerator[Expression, None]:
        async with self._lock:
            self._buffer += token.text
            sentences, self._buffer = split_sentences(
                self._buffer, self.min_chars, self.max_chars, final=token.end
            )
            scores = await self._handle.classify.remote(sentences) if sentences else []
            out = [self._express(t, s) for t, s in zip(sentences, scores)]
            if token.end:
                if out:
                    out[-1].end = True
                else:
                    last = self._last or self._express_neutral()
                    out = [
                        Expression.from_scores(
                            last.scores, "reply", text=None, end=True
                        )
                    ]
                self._last = None
            elif out:
                self._last = out[-1]
            for expression in out:
                yield expression

    def _express_neutral(self) -> Expression:
        reaction = REACTIONS.get(self._user_id)
        if reaction is None:
            return Expression.from_scores({}, "reply")
        return Expression.from_scores(blend({}, reaction, self.empathy), "reply")
