"""RAG tells the LLM how the user sounded, from the emotion on the question.

Sequential QAG: the emotion is the question's own. Parallel QAG: the question
may carry the previous message's emotion instead, flagged `emotion_earlier`,
and the prompt says so.
"""

from huri_events.emotion import Emotion
from huri_events.question import RAGQuestion
from huri_events.speech import Transcript
from huri_rag.rag import RAG, RAGHandle

from huri_core.testing import LocalHandle, ModuleHarness


class FakeLLM:
    """Records each RAGQuery and streams a fixed answer."""

    def __init__(self):
        self.queries = []

    async def stream(self, query):
        self.queries.append(query)
        for word in ("Hi", " there"):
            yield word


def _ask(*questions):
    fake = FakeLLM()
    harness = ModuleHarness(RAG, handle=LocalHandle(fake), user_id="u")
    for question in questions:
        harness.run(question)
    return fake.queries


def _emotion(label, confidence=0.8):
    return Emotion(label, confidence, {label: confidence}, True)


def _question(text, emotion=None, earlier=False):
    return RAGQuestion(Transcript(text, True), emotion, earlier)


def test_the_emotion_of_the_question_reaches_the_llm():
    [query] = _ask(_question("hello", _emotion("sad", 0.67)))
    assert query.emotion == {"label": "sad", "confidence": 0.67}
    assert query.emotion_earlier is False


def test_the_previous_message_s_emotion_is_flagged_as_such():
    first, second = _ask(
        _question("first"),  # parallel QAG: sent before EMO finished
        _question("second", _emotion("ang"), earlier=True),
    )
    assert first.emotion is None and first.emotion_earlier is False
    assert second.emotion["label"] == "ang" and second.emotion_earlier is True


def test_the_flag_means_nothing_without_an_emotion():
    [query] = _ask(_question("hi", None, earlier=True))
    assert query.emotion is None and query.emotion_earlier is False


def test_the_prompt_tells_the_llm_how_the_user_sounds():
    rag_handle = object.__new__(RAGHandle.func_or_class)
    build = rag_handle._build_prompt

    system, _ = build("hi", [], {}, emotion={"label": "sad", "confidence": 0.67})
    assert "the person sounds sad right now (67% confident)" in system

    system, _ = build(
        "hi", [], {}, emotion={"label": "ang", "confidence": 0.9}, emotion_earlier=True
    )
    assert "sounded angry in their previous message" in system

    system, _ = build("hi", [], {})
    assert "tone of their voice" not in system
