import json

import numpy as np
import pytest
from huri_events.emotion import EXPRESSIONS, Expression, complete_scores
from huri_events.face import ARKIT_INDEX, N_SHAPES


def test_expression_roundtrips_on_the_wire():
    original = Expression.from_scores({"joy": 0.7}, "reply", text="Great!", end=True)
    wire = json.loads(json.dumps(original.to_wire()))

    assert Expression.from_wire(wire) == original


def test_partial_scores_are_completed_with_neutral():
    expression = Expression.from_scores({"joy": 0.6}, "reply")

    assert set(expression.scores) == set(EXPRESSIONS)
    assert expression.scores["neutral"] == pytest.approx(0.4)
    assert expression.label == "joy"
    assert expression.intensity == pytest.approx(0.6)
    assert expression.valence > 0 and expression.arousal > 0


def test_scores_above_one_are_normalised():
    scores = complete_scores({"joy": 1.0, "surprise": 1.0})

    assert sum(scores.values()) == pytest.approx(1.0)
    assert scores["joy"] == pytest.approx(0.5)
    assert scores["neutral"] == 0.0


def test_negative_scores_count_as_zero():
    assert complete_scores({"anger": -0.5})["anger"] == 0.0


def test_unknown_expression_is_refused():
    with pytest.raises(ValueError, match="unknown expressions"):
        complete_scores({"contempt": 0.5})


def test_empty_scores_are_neutral():
    expression = Expression.from_scores({}, "reaction")

    assert expression.label == "neutral"
    assert expression.intensity == 0.0
    assert (expression.valence, expression.arousal) == (0.0, 0.0)


def test_sad_expression_has_negative_valence_and_low_arousal():
    expression = Expression.from_scores({"sadness": 1.0}, "reaction")

    assert expression.valence < 0 and expression.arousal < 0


def test_joy_face_smiles():
    face = Expression.from_scores({"joy": 1.0}, "reply").face()

    assert face.shape == (N_SHAPES,)
    assert face.dtype == np.float32
    for shape in ("mouthSmileLeft", "mouthSmileRight", "cheekSquintLeft"):
        assert face[ARKIT_INDEX[shape]] > 0.4
    assert face[ARKIT_INDEX["mouthFrownLeft"]] == 0.0


def test_neutral_face_is_zero():
    assert not Expression.from_scores({}, "reply").face().any()


def test_face_scales_with_the_score():
    half = Expression.from_scores({"sadness": 0.5}, "reply").face()
    full = Expression.from_scores({"sadness": 1.0}, "reply").face()

    np.testing.assert_allclose(half * 2, full, rtol=1e-6)


def test_face_stays_within_unit_range():
    mixed = Expression.from_scores({"surprise": 0.5, "fear": 0.5}, "reply").face()

    assert mixed.min() >= 0.0 and mixed.max() <= 1.0


def test_label_is_the_strongest_shown_expression_even_below_neutral():
    expression = Expression.from_scores({"sadness": 0.3, "joy": 0.1}, "reaction")

    assert expression.label == "sadness"
    assert expression.intensity == pytest.approx(0.4)


def test_faint_expressions_are_labelled_neutral():
    assert Expression.from_scores({"joy": 0.05}, "reply").label == "neutral"


def test_concern_raises_the_inner_brows_without_a_frown():
    face = Expression.from_scores({"concern": 1.0}, "reaction").face()

    assert face[ARKIT_INDEX["browInnerUp"]] > 0.4
    assert face[ARKIT_INDEX["mouthFrownLeft"]] == 0.0


def test_calm_smiles_softer_than_joy():
    calm = Expression.from_scores({"calm": 1.0}, "reaction").face()
    joy = Expression.from_scores({"joy": 1.0}, "reaction").face()

    assert 0 < calm[ARKIT_INDEX["mouthSmileLeft"]] < joy[ARKIT_INDEX["mouthSmileLeft"]]


def test_interest_raises_the_outer_brows():
    face = Expression.from_scores({"interest": 1.0}, "reply").face()

    assert face[ARKIT_INDEX["browOuterUpLeft"]] > 0.2


def test_social_expressions_have_their_affect():
    calm = Expression.from_scores({"calm": 1.0}, "reaction")
    interest = Expression.from_scores({"interest": 1.0}, "reaction")

    assert calm.valence > 0 and calm.arousal < 0
    assert interest.valence > 0 and interest.arousal > 0
