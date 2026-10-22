import logging
import time
from typing import Optional

from huri_events.emotion import Emotion
from huri_events.question import PartialQuestion, RAGQuestion
from huri_events.speech import Transcript

from huri_core.core.module import Module

logger = logging.getLogger("ray.serve")


class QAG(Module):
    """QAG Module

    Aggregate sentence and emotion into a RAGQuestion.

    input: huri.partial_question,
    output: huri.question

    :use_emotion: default True.
    Set to False if you do not analyze emotion or do not need it.

    :mode: how RAG and EMO are synchronised when use_emotion is True.
    - "parallel" (default): send the question as soon as its transcript is
      ready, so RAG answers while EMO is still analysing the turn. The emotion
      is attached only if it already landed; one landing after its question
      left is kept and goes with the next question, flagged
      ``emotion_earlier`` so RAG reads it as the previous message's.
    - "sequential": hold the question until its emotion lands, so RAG only
      starts once EMO has finished the turn.
    """

    input_type = "huri.partial_question"
    output_type = "huri.question"
    input_event = PartialQuestion
    output_event = RAGQuestion

    MODES = ("parallel", "sequential")

    def __init__(self, use_emotion: bool = True, mode: str = "parallel"):
        super().__init__()

        if mode not in self.MODES:
            raise ValueError(f"QAG mode must be one of {self.MODES}, got {mode!r}")

        self.current_transcript: Optional[Transcript] = None
        self.current_emotion: Optional[Emotion] = None

        self.use_emotion = use_emotion
        self.mode = mode

        # Parallel mode: the last question left before its emotion landed, so
        # the next emotion belongs to an already answered turn...
        self._late_emotion_expected = False
        # ...and is kept here for the next question.
        self._earlier_emotion: Optional[Emotion] = None

        # When the current transcript arrived, to log how long it waited.
        self._transcript_at: float = 0.0

    async def process(self, partial_question: PartialQuestion) -> Optional[RAGQuestion]:
        if self.use_emotion and partial_question.emotion is not None:
            if self._late_emotion_expected:
                self._late_emotion_expected = False
                self._earlier_emotion = partial_question.emotion
                logger.info(
                    "[QAG] parallel: emotion %r landed after its question was "
                    "sent to RAG, kept for the next one",
                    partial_question.emotion.label,
                )
            else:
                self.current_emotion = partial_question.emotion

        if partial_question.transcript is not None:
            self.current_transcript = partial_question.transcript
            self._transcript_at = time.monotonic()

        if self.current_transcript is None:
            return None

        emotion = self.current_emotion if self.use_emotion else None
        earlier = False
        if self.use_emotion and emotion is None:
            if self.mode == "sequential":
                # Hold the question back until its emotion lands.
                logger.info("[QAG] sequential: question held until EMO finishes")
                return None
            # Parallel: EMO is still running; its emotion will be late.
            self._late_emotion_expected = True
            if self._earlier_emotion is not None:
                emotion, earlier = self._earlier_emotion, True
        self._earlier_emotion = None

        if self.use_emotion:
            if emotion is None:
                logger.info("[QAG] parallel: question sent to RAG, EMO still running")
            elif earlier:
                logger.info(
                    "[QAG] parallel: question sent to RAG with the previous "
                    "message's emotion %r, EMO still running",
                    emotion.label,
                )
            else:
                logger.info(
                    "[QAG] %s: question sent to RAG with emotion %r (waited %.2fs)",
                    self.mode,
                    emotion.label,
                    time.monotonic() - self._transcript_at,
                )

        question = RAGQuestion(self.current_transcript, emotion, earlier)

        # Clear the aggregation state after firing. Otherwise a later partial
        # carrying only one half (the next turn's emotion-only update, or a
        # stray transcript) re-emits this same question with stale data — which
        # made the avatar answer the previous turn again. TAG/EAG already reset
        # their own state on emit; QAG must do the same.
        self.current_transcript = None
        self.current_emotion = None
        return question
