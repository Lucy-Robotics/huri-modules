"""The curated HuRI events, one class per `huri.*` topic.

| topic                   | class             | wire   |
|-------------------------|-------------------|--------|
| huri.audio.in           | BytesEvent (core) | binary |
| huri.voice              | Voice             | json   |
| huri.transcript         | Transcript        | json   |
| huri.emotion            | Emotion           | json   |
| huri.emotion.out        | Expression        | json   |
| huri.partial_question   | PartialQuestion   | json   |
| huri.question           | RAGQuestion       | json   |
| huri.token              | Token             | json   |
| huri.audio.out          | Audio             | binary |
| huri.motion             | Motion            | binary |
| huri.lips.face          | FaceWeights       | binary |

`Voice` and `Emotion` travel between modules (VAD -> STT, EMO; EMO -> EAG), and
a client may subscribe to either: `huri.voice` to see that the server hears
speech, `huri.emotion` to show what it senses.
"""

from huri_events.audio import Audio
from huri_events.emotion import Emotion, Expression
from huri_events.face import FaceWeights
from huri_events.motion import Motion
from huri_events.question import PartialQuestion, RAGQuestion
from huri_events.speech import Transcript, Voice
from huri_events.text import Token

__all__ = [
    "Audio",
    "Emotion",
    "Expression",
    "FaceWeights",
    "Motion",
    "PartialQuestion",
    "RAGQuestion",
    "Token",
    "Transcript",
    "Voice",
]
