"""QAG's two ways of pairing a sentence with its emotion.

Parallel (the default) sends the question as soon as TAG's sentence is in, so
RAG starts while EMO is still reading the turn; an emotion landing afterwards
is kept for the next question, flagged `emotion_earlier`. Sequential holds the
question until its emotion is in. The two modes side by side on a real bus:
tests/integration/test_qag_modes.py.
"""

import asyncio

import pytest
from huri_events.emotion import Emotion
from huri_events.question import PartialQuestion
from huri_events.speech import Transcript
from huri_rag.question_aggregator import QAG

from huri_core.testing import ModuleHarness


def _transcript(text):
    return PartialQuestion(transcript=Transcript(text, True), emotion=None)


def _emotion(label):
    return PartialQuestion(
        transcript=None,
        emotion=Emotion(label=label, confidence=1.0, scores={label: 1.0}, end=True),
    )


def _run(qag, *partials):
    """One partial after the other, as TAG and EAG publish them: each in its
    own task, like the bus (a held question waits for a later partial). What
    QAG emitted for each, None where nothing."""

    harness = ModuleHarness(qag)

    async def impl():
        tasks = []
        for partial in partials:
            tasks.append(asyncio.ensure_future(harness.feed(partial)))
            await asyncio.sleep(0)  # it runs up to its first wait
        return [
            emitted[0] if emitted else None for emitted in await asyncio.gather(*tasks)
        ]

    return asyncio.run(impl())


def test_default_mode_is_parallel():
    assert QAG().mode == "parallel"


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="mode"):
        QAG(mode="both")


def test_sequential_holds_the_question_until_its_emotion():
    out = _run(QAG(mode="sequential"), _transcript("hi"), _emotion("hap"))

    assert out[0] is None
    assert out[1].transcript.text == "hi"
    assert out[1].emotion.label == "hap"
    assert out[1].emotion_earlier is False


def test_sequential_sends_the_question_without_an_emotion_that_never_comes():
    """No EMO in the session, or EMO failed: the question still leaves, and an
    emotion landing after all goes with the next one, as in parallel mode."""
    qag = QAG(mode="sequential", emotion_timeout=0.05)

    (alone,) = _run(qag, _transcript("hi"))
    assert alone.transcript.text == "hi" and alone.emotion is None

    late, again = _run(qag, _emotion("sad"), _transcript("again"))
    assert late is None
    assert again.transcript.text == "again"  # its own emotion never came either
    assert again.emotion.label == "sad" and again.emotion_earlier is True


def test_sequential_emotion_timeout_must_be_positive():
    with pytest.raises(ValueError, match="emotion_timeout"):
        QAG(mode="sequential", emotion_timeout=0)


def test_parallel_sends_the_question_without_waiting_for_its_emotion():
    out = _run(QAG(mode="parallel"), _transcript("hi"))

    assert out[0].transcript.text == "hi"
    assert out[0].emotion is None
    assert out[0].emotion_earlier is False


def test_parallel_attaches_an_emotion_that_landed_first():
    out = _run(QAG(mode="parallel"), _emotion("ang"), _transcript("hi"))

    assert out[0] is None
    assert out[1].emotion.label == "ang"
    assert out[1].emotion_earlier is False


def test_parallel_keeps_a_late_emotion_for_the_next_question():
    out = _run(
        QAG(mode="parallel"),
        _transcript("first"),
        _emotion("sad"),  # late: belongs to "first", already sent
        _transcript("second"),
        _transcript("third"),
    )

    assert out[0].emotion is None
    assert out[1] is None  # not a new question
    assert out[2].transcript.text == "second"
    assert out[2].emotion.label == "sad" and out[2].emotion_earlier is True
    assert out[3].emotion is None  # used once


def test_an_emotion_of_its_own_wins_over_an_earlier_one():
    out = _run(
        QAG(mode="parallel"),
        _transcript("first"),
        _emotion("sad"),  # late, for "first"
        _emotion("hap"),  # in time, for "second"
        _transcript("second"),
    )

    assert out[3].transcript.text == "second"
    assert out[3].emotion.label == "hap" and out[3].emotion_earlier is False


def test_without_emotion_the_mode_is_irrelevant():
    for mode in QAG.MODES:
        out = _run(
            QAG(use_emotion=False, mode=mode), _emotion("sad"), _transcript("hi")
        )

        assert out[0] is None
        assert out[1].transcript.text == "hi"
        assert out[1].emotion is None
