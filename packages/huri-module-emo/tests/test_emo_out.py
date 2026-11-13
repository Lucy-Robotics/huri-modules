import asyncio

import pytest
from huri_emo.expression import (
    REACTIONS,
    apply_gains,
    blend,
    split_sentences,
    to_expressions,
)
from huri_events.emotion import Emotion
from huri_events.question import RAGQuestion
from huri_events.speech import Transcript
from huri_events.text import Token

from huri_core.testing import LocalHandle, ModuleHarness


class FakeClassifier:
    def __init__(self, scores=None, gate=None):
        self.scores = scores or {}
        self.gate = gate
        self.calls = []

    async def classify(self, texts):
        self.calls.append(list(texts))
        if self.gate is not None and len(self.calls) == 1:
            await self.gate.wait()
        return [dict(self.scores.get(text, {"neutral": 1.0})) for text in texts]


def _out(classifier, user_id="out-user", **args):
    from huri_emo.out import EmoOut

    return ModuleHarness(
        EmoOut, handle=LocalHandle(classifier), user_id=user_id, **args
    )


def _react(user_id="react-user", **args):
    from huri_emo.react import EmoReact

    return ModuleHarness(EmoReact, user_id=user_id, **args)


def _question(label=None, confidence=0.9, scores=None):
    emotion = None
    if label is not None:
        emotion = Emotion(label, confidence, scores or {label: confidence}, True)
    return RAGQuestion(Transcript("hi", True), emotion)


def _tokens(*texts):
    return [Token(t, False) for t in texts] + [Token("", True)]


@pytest.fixture(autouse=True)
def _no_reactions():
    for user in ("out-user", "react-user", "both"):
        REACTIONS.forget(user)
    yield


def test_go_emotions_labels_are_grouped_into_expressions():
    scores = to_expressions({"gratitude": 0.6, "joy": 0.2, "neutral": 0.2})

    assert scores["joy"] == pytest.approx(0.8)
    assert scores["neutral"] == pytest.approx(0.2)


def test_social_go_emotions_labels_get_social_expressions():
    assert to_expressions({"caring": 1.0})["concern"] == pytest.approx(1.0)
    assert to_expressions({"curiosity": 1.0})["interest"] == pytest.approx(1.0)
    assert to_expressions({"confusion": 1.0})["interest"] == pytest.approx(1.0)
    assert to_expressions({"relief": 1.0})["calm"] == pytest.approx(1.0)
    assert to_expressions({"remorse": 1.0})["concern"] == pytest.approx(1.0)


def test_ekman_labels_map_onto_themselves():
    assert to_expressions({"surprise": 1.0})["surprise"] == pytest.approx(1.0)


def test_unknown_labels_only_are_neutral():
    assert to_expressions({"LABEL_0": 0.9})["neutral"] == 1.0


def test_gains_move_the_removed_share_to_neutral():
    shown = apply_gains({"anger": 1.0}, {"anger": 0.3})

    assert shown["anger"] == pytest.approx(0.3)
    assert shown["neutral"] == pytest.approx(0.7)


def test_blend_weights_the_second_scores():
    mixed = blend({}, {"sadness": 1.0}, 0.3)

    assert mixed["sadness"] == pytest.approx(0.3)
    assert mixed["neutral"] == pytest.approx(0.7)


def test_sentences_wait_for_the_space_after_their_punctuation():
    assert split_sentences("Pi is 3.14 and", 1, 200) == ([], "Pi is 3.14 and")
    assert split_sentences("Hello there!", 1, 200) == ([], "Hello there!")
    done, rest = split_sentences("Hello there! How", 1, 200)
    assert (done, rest) == (["Hello there!"], " How")


def test_short_sentences_join_the_next():
    done, rest = split_sentences("Oh. I see it now. And", 8, 200)

    assert done == ["Oh. I see it now."]
    assert rest == " And"


def test_long_text_without_punctuation_is_cut_at_a_space():
    done, rest = split_sentences("word " * 30, 1, 40)

    assert all(len(s) <= 40 for s in done)
    assert len(rest) <= 40


def test_final_flushes_the_rest():
    assert split_sentences("I see", 12, 200, final=True) == (["I see"], "")
    assert split_sentences("   ", 12, 200, final=True) == ([], "")


def test_sad_user_gets_compassion():
    [expression] = _react().run(_question("sad", 0.9))

    assert expression.source == "reaction"
    assert expression.label == "concern"
    assert expression.scores["concern"] == pytest.approx(0.378)
    assert expression.scores["sadness"] == pytest.approx(0.162)


def test_angry_user_gets_calm_not_anger():
    [expression] = _react().run(_question("ang", 1.0))

    assert expression.label == "calm"
    assert expression.scores["anger"] == 0.0
    assert expression.scores["sadness"] == 0.0


def test_afraid_user_is_reassured():
    [expression] = _react().run(_question("fea", 1.0))

    assert expression.label == "calm"
    assert expression.scores["concern"] > 0.0
    assert expression.scores["fear"] == 0.0


def test_neutral_user_gets_attentive_interest():
    [expression] = _react().run(_question("neu", 1.0))

    assert expression.label == "interest"


def test_no_default_reaction_is_sadness_alone():
    from huri_emo.react import DEFAULT_REACTIONS

    for label, reaction in DEFAULT_REACTIONS.items():
        assert set(reaction) != {"sadness"}, label


def test_typed_question_resets_to_neutral():
    [expression] = _react().run(_question())

    assert expression.label == "neutral"
    assert expression.intensity == 0.0


def test_unknown_user_label_is_neutral():
    [expression] = _react().run(_question("xyz"))

    assert expression.label == "neutral"


def test_reaction_reads_every_score_of_the_user():
    question = _question("neu", 0.5, {"neu": 0.5, "hap": 0.5})
    [expression] = _react(strength=1.0).run(question)

    assert expression.scores["joy"] == pytest.approx(0.5)


def test_reactions_are_configurable():
    react = _react(reactions={"hap": {"surprise": 1.0}}, strength=1.0)
    [expression] = react.run(_question("hap", 1.0))

    assert expression.label == "surprise"


def test_strength_out_of_range_is_refused():
    with pytest.raises(ValueError, match="strength"):
        _react(strength=1.5)


def test_reaction_is_forgotten_when_the_session_ends():
    react = _react(user_id="both")
    react.run(_question("sad"))
    assert REACTIONS.get("both") is not None

    asyncio.run(react.finalize())

    assert REACTIONS.get("both") is None


def test_one_expression_per_sentence():
    classifier = FakeClassifier({"That's great!": {"joy": 1.0}})
    out = _out(classifier, min_chars=1)

    expressions = out.run(*_tokens("That's ", "great! ", "I'm happy ", "for you."))

    assert [e.text for e in expressions] == ["That's great!", "I'm happy for you."]
    assert [e.source for e in expressions] == ["reply", "reply"]
    assert expressions[0].label == "joy"
    assert [e.end for e in expressions] == [False, True]


def test_end_flushes_the_rest():
    expressions = _out(FakeClassifier()).run(*_tokens("I see"))

    assert [(e.text, e.end) for e in expressions] == [("I see", True)]


def test_empty_reply_still_ends_neutral():
    classifier = FakeClassifier()
    expressions = _out(classifier).run(Token("", True))

    assert [(e.label, e.end) for e in expressions] == [("neutral", True)]
    assert classifier.calls == []


def test_end_without_rest_repeats_the_last_expression():
    classifier = FakeClassifier({"What a lovely day.": {"joy": 1.0}})
    out = _out(classifier)

    expressions = out.run(Token("What a lovely day. ", False), Token("", True))

    assert [(e.label, e.end) for e in expressions] == [("joy", False), ("joy", True)]
    assert expressions[-1].text is None


def test_anger_is_toned_down():
    classifier = FakeClassifier({"This is outrageous": {"anger": 1.0}})
    [expression] = _out(classifier).run(*_tokens("This is outrageous"))

    assert expression.scores["anger"] == pytest.approx(0.3)
    assert expression.scores["neutral"] == pytest.approx(0.7)


def test_gains_are_configurable():
    classifier = FakeClassifier({"This is outrageous": {"anger": 1.0}})
    [expression] = _out(classifier, gains={}).run(*_tokens("This is outrageous"))

    assert expression.label == "anger"


def test_user_emotion_colours_the_reply():
    _react(user_id="both", strength=1.0).run(_question("sad", 1.0))
    [expression] = _out(FakeClassifier(), user_id="both").run(*_tokens("I see."))

    assert expression.scores["concern"] == pytest.approx(0.21)
    assert expression.scores["sadness"] == pytest.approx(0.09)


def test_no_reaction_leaves_the_reply_alone():
    classifier = FakeClassifier({"I see.": {"joy": 1.0}})
    [expression] = _out(classifier).run(*_tokens("I see."))

    assert expression.scores["joy"] == pytest.approx(1.0)


def test_concurrent_tokens_keep_their_order():
    asyncio.run(_test_concurrent_tokens_keep_their_order_impl())


async def _test_concurrent_tokens_keep_their_order_impl():
    gate = asyncio.Event()
    classifier = FakeClassifier(gate=gate)
    out = _out(classifier, min_chars=1)

    tokens = _tokens("One. ", "Two. ", "Three. ", "Four")
    task = asyncio.ensure_future(out.feed_all(*tokens))
    await asyncio.sleep(0)
    gate.set()
    expressions = await task

    assert [e.text for e in expressions] == ["One.", "Two.", "Three.", "Four"]
    assert [c for call in classifier.calls for c in call] == [
        "One.",
        "Two.",
        "Three.",
        "Four",
    ]


def test_state_resets_after_the_end():
    classifier = FakeClassifier({"Great news.": {"joy": 1.0}})
    out = _out(classifier)
    out.run(*_tokens("Great news. "))

    [expression] = out.run(Token("", True))

    assert expression.label == "neutral"


def test_bad_args_are_refused():
    with pytest.raises(ValueError, match="empathy"):
        _out(FakeClassifier(), empathy=2)
    with pytest.raises(ValueError, match="min_chars"):
        _out(FakeClassifier(), min_chars=50, max_chars=10)
