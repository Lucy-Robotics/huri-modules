"""What TAG must never do to a sentence.

The windows STT transcribes mid-turn are stitched into `sentence` as the
fallback for an empty end-of-turn pass. Two things went wrong with earlier
stitchers and are pinned here: a merge that dropped words already
accumulated (a short coincidental match near the start of the sentence
truncated everything before it), and one that, once it stopped matching,
glued the windows together without a separator and repeated them. The end
pass itself is not stitched: it is the whole utterance, so it is sent as is.
"""

import asyncio

from huri_events.speech import Transcript
from huri_tag.module import TAG


def _words(text):
    return text.lower().replace(",", "").replace(".", "").split()


async def _windows(tag, texts):
    """Feed window transcripts (end=False); the accumulated sentence."""

    for text in texts:
        assert await tag.process(Transcript(text, False)) is None
    return tag.sentence


async def _finish(tag, final_text):
    return await tag.process(Transcript(final_text, True))


def test_a_merge_never_drops_accumulated_words():
    """Word count is not the test: merging "w1" into "w2" keeps the count at
    one while losing the sentence. The words already there must stay, in
    order."""

    async def run():
        tag = TAG()
        for text in ["w1", "w2", "w3", "w4", "w5", "w6"]:
            before = _words(tag.sentence)
            await tag.process(Transcript(text, False))
            after = _words(tag.sentence)
            assert after[: len(before)] == before, f"{text!r} dropped words"

    asyncio.run(run())


def test_windows_sharing_no_words_all_survive():
    """What the pipeline does once the VAD has stripped the pauses and
    consecutive windows share no wording."""

    async def run():
        tag = TAG()
        windows = ["w1", "w2", "w3", "w4", "w5", "w6"]
        accumulated = await _windows(tag, windows)
        assert [w for w in windows if w not in accumulated] == []

    asyncio.run(run())


def test_a_long_sentence_is_not_truncated_by_a_short_match():
    async def run():
        tag = TAG()
        await tag.process(
            Transcript(
                "So the thing is that I have been working on this project "
                "for a while now and",
                False,
            )
        )
        before = tag.sentence
        await tag.process(
            Transcript("and I think that the the the main issue is", False)
        )
        assert len(_words(tag.sentence)) >= len(_words(before))

    asyncio.run(run())


def test_overlapping_windows_merge_cleanly():
    async def run():
        tag = TAG()
        accumulated = await _windows(
            tag,
            [
                "I would like to know",
                "would like to know what the",
                "what the capital of France is",
            ],
        )
        assert accumulated == "I would like to know what the capital of France is"

    asyncio.run(run())


def test_a_repeated_phrase_is_neither_duplicated_nor_glued():
    async def run():
        tag = TAG()
        final = (
            await _windows(
                tag,
                [
                    "the room on the left is empty",
                    "on the left is empty and the room",
                    "and the room on the right is empty",
                ],
            )
        ).lower()
        assert final.count("on the left is empty") == 1, final
        assert final.count("and the room") == 1, final
        assert "roomand" not in final, final

    asyncio.run(run())


def test_nothing_is_emitted_before_the_end_of_the_turn():
    async def run():
        tag = TAG()
        assert await tag.process(Transcript("hello", False)) is None
        assert await tag.process(Transcript("hello there", False)) is None
        assert await tag.process(Transcript("hello there world", True)) is not None

    asyncio.run(run())


def test_state_resets_between_turns():
    async def run():
        tag = TAG()
        await _windows(tag, ["alpha bravo", "bravo charlie"])
        first = await _finish(tag, "alpha bravo charlie")
        await _windows(tag, ["delta echo", "echo foxtrot"])
        second = await _finish(tag, "delta echo foxtrot")
        assert first.transcript.text == "alpha bravo charlie"
        assert second.transcript.text == "delta echo foxtrot"

    asyncio.run(run())


def test_the_end_pass_wins_over_the_windows():
    """STT's end-of-turn pass hears the whole utterance with real context: it
    beats anything stitched from 2 s windows."""

    async def run():
        tag = TAG()
        await _windows(tag, ["I would like", "would like to know the"])
        result = await _finish(tag, "I would like to know the capital of France.")
        assert result.transcript.text == "I would like to know the capital of France."

    asyncio.run(run())


def test_an_empty_end_pass_falls_back_to_the_windows():
    async def run():
        tag = TAG()
        await _windows(tag, ["hello there", "there world"])
        result = await _finish(tag, "")
        assert result.transcript.text == "hello there world"

    asyncio.run(run())
