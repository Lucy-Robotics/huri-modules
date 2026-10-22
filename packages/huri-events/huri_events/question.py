from dataclasses import dataclass
from typing import Any, Mapping, Optional

from huri_events.emotion import Emotion
from huri_events.speech import Transcript

from huri_core.core.events import JsonEvent


@dataclass
class PartialQuestion(JsonEvent):
    """Partial question used to aggregate a sentence to an emotion: either
    part may be missing (TAG sends the transcript, EAG the emotion)."""

    transcript: Optional[Transcript]
    emotion: Optional[Emotion]


@dataclass
class RAGQuestion(JsonEvent):
    """Fully aggregated question to send to the RAG.

    Wire: JSON ``{"transcript": {"text", "end"}, "emotion": {...} | null,
    "emotion_earlier": bool}``, or the shorthand ``{"text": str}`` a client
    sends for a typed question.

    `emotion_earlier` comes from QAG's parallel mode: the question left before
    EMO had finished reading the turn, so the emotion attached is the one of
    the user's previous message, kept for this one. False when the emotion is
    this question's own, or when there is none.
    """

    transcript: Transcript
    emotion: Optional[Emotion] = None
    emotion_earlier: bool = False

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> "RAGQuestion":
        """Accept a typed question straight off the wire.

        In-pipeline, QAG builds this from a Transcript + Emotion. External clients
        (the website backend, the CLI text sender) inject a typed question as a
        bare ``{"text": ...}`` — no live emotion — which we wrap into a final
        Transcript. The nested branch also hydrates the dataclasses (JSON gives
        plain dicts) so a fully-shaped payload round-trips too.
        """
        if "text" in data:
            return cls(Transcript(text=data["text"], end=True), None)

        # Thomas: it's nasty, but ragquestion is a subclass and doesn't work well with
        # ray for my end, i get RAGQuestion.__init__() got an unexpected keyword
        # argument 'text' if there is a agnostic way, we can fix this, but until now
        # it's like this

        transcript = data.get("transcript")
        if isinstance(transcript, Mapping):
            transcript = Transcript(**transcript)
        if not isinstance(transcript, Transcript):
            raise ValueError(
                "RAGQuestion.from_wire needs a 'text' or 'transcript' field"
            )
        emotion = data.get("emotion")
        if isinstance(emotion, Mapping):
            emotion = Emotion(**emotion)
        return cls(transcript, emotion, bool(data.get("emotion_earlier", False)))
