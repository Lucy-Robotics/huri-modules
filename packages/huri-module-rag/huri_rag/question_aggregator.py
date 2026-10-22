import asyncio
import logging
import time
from typing import AsyncGenerator, Optional

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

    :emotion_timeout: sequential mode, in seconds: how long a question waits
    for its emotion before it is sent without one (no EMO in the session, or
    EMO failed). The emotion, if it lands later, goes with the next question
    as in parallel mode.
    """

    input_type = "huri.partial_question"
    output_type = "huri.question"
    input_event = PartialQuestion
    output_event = RAGQuestion

    MODES = ("parallel", "sequential")

    def __init__(
        self,
        use_emotion: bool = True,
        mode: str = "parallel",
        emotion_timeout: float = 10.0,
    ):
        super().__init__()

        if mode not in self.MODES:
            raise ValueError(f"QAG mode must be one of {self.MODES}, got {mode!r}")
        if emotion_timeout <= 0:
            raise ValueError(f"emotion_timeout must be > 0, got {emotion_timeout}")

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

        # Sequential mode: set when a question leaves, which ends the wait of
        # the call holding it. Made per hold: an Event binds to the event loop
        # it is first awaited in.
        self.emotion_timeout = emotion_timeout
        self._sent: Optional[asyncio.Event] = None

    async def process(
        self, partial_question: PartialQuestion
    ) -> AsyncGenerator[RAGQuestion, None]:
        question = self._aggregate(partial_question)
        if question is not None:
            if self._sent is not None:
                self._sent.set()
            yield question
            return
        held = self.current_transcript
        if held is None or not self.use_emotion or self.mode != "sequential":
            return
        # Held for its emotion: the call that brings it sends the question.
        self._sent = sent = asyncio.Event()
        try:
            await asyncio.wait_for(sent.wait(), self.emotion_timeout)
            return
        except asyncio.TimeoutError:
            if self.current_transcript is not held:
                return  # sent, or replaced by a newer sentence that waits too
        logger.warning(
            "[QAG] sequential: no emotion after %.1fs, question sent without "
            "one (is EMO in this session?)",
            self.emotion_timeout,
        )
        # Its emotion, if it still comes, goes with the next question; one
        # that came late for the previous question goes with this one.
        earlier, self._earlier_emotion = self._earlier_emotion, None
        self._late_emotion_expected = True
        self.current_transcript = None
        self.current_emotion = None
        yield RAGQuestion(held, earlier, earlier is not None)

    def _aggregate(self, partial_question: PartialQuestion) -> Optional[RAGQuestion]:
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
