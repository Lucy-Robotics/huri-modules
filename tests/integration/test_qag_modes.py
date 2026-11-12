"""QAG's two modes on the real bus, with the real TAG, EAG and QAG.

    huri.voice ──► EMO (slow, gated) ──► EAG ┐
    huri.transcript ──────────────► TAG ──────┴► QAG ──► RAG

EMO and RAG are fakes: EMO stays busy until the test opens its gate, RAG
records when the question reached it and whether EMO had finished by then.
"""

import asyncio

from huri_events.emotion import Emotion
from huri_events.speech import Transcript, Voice

from huri_core.core.bus import EventGraph
from huri_core.core.module import Module
from huri_core.core.plugins import load_modules


class SlowEMO(Module):
    input_type = "huri.voice"
    output_type = "huri.emotion"

    def __init__(self):
        self.gate = asyncio.Event()
        self.done = False

    async def process(self, _voice):
        await self.gate.wait()
        self.done = True
        return Emotion(label="hap", confidence=1.0, scores={"hap": 1.0}, end=True)


class FakeRAG(Module):
    input_type = "huri.question"
    output_type = None

    def __init__(self, emo):
        self.emo = emo
        self.received = asyncio.Event()
        self.questions = []
        self.emo_was_done = None

    async def process(self, question):
        self.questions.append(question)
        self.emo_was_done = self.emo.done
        self.received.set()


def _pipeline(mode):
    curated = load_modules(["tag", "eag", "qag"])
    emo = SlowEMO()
    rag = FakeRAG(emo)
    graph = EventGraph()
    for module in (
        emo,
        curated["eag"](),
        curated["tag"](),
        curated["qag"](mode=mode),
        rag,
    ):
        graph.register(module)
    return graph, emo, rag


async def _speak(graph, text="hello"):
    await graph.publish("huri.voice", Voice(None))  # end of turn -> EMO starts
    await graph.publish("huri.transcript", Transcript(text, True))


def test_parallel_rag_answers_while_emo_is_still_busy():
    async def run():
        graph, emo, rag = _pipeline("parallel")
        await _speak(graph)

        await asyncio.wait_for(rag.received.wait(), timeout=1)
        assert rag.emo_was_done is False  # RAG started before EMO finished
        [question] = rag.questions
        assert question.transcript.text == "hello"
        assert question.emotion is None

        # EMO finishes: its emotion goes with the next question, flagged.
        emo.gate.set()
        await asyncio.sleep(0.05)
        rag.received.clear()
        await graph.publish("huri.transcript", Transcript("and then", True))
        await asyncio.wait_for(rag.received.wait(), timeout=1)
        _, second = rag.questions
        assert second.transcript.text == "and then"
        assert second.emotion.label == "hap" and second.emotion_earlier is True

    asyncio.run(run())


def test_sequential_rag_waits_for_emo():
    async def run():
        graph, emo, rag = _pipeline("sequential")
        await _speak(graph)

        await asyncio.sleep(0.1)
        assert not rag.received.is_set()  # EMO busy -> RAG not started

        emo.gate.set()
        await asyncio.wait_for(rag.received.wait(), timeout=1)
        assert rag.emo_was_done is True  # RAG started only after EMO
        [question] = rag.questions
        assert question.emotion.label == "hap" and question.emotion_earlier is False

    asyncio.run(run())
