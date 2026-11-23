"""What this protects: the cost of one spoken turn through the curated
pipeline stays visible. Models are faked, so what is timed is the modules' own
work, huri-core's bus and the server's Sender. A turn that never completes
fails instead of reporting a time."""

import asyncio
import struct

from huri_events import Audio, Token

from huri_core.core.dataclasses.config import ModuleConfig
from huri_core.core.events import BytesEvent
from huri_core.core.factory import ModuleFactory
from huri_core.core.plugins import load_modules
from huri_core.core.sender import Sender
from huri_core.core.session import Session
from huri_core.testing import LocalHandle

# shortcut: reuses the integration test's fakes, so a PR that changes them moves
# this benchmark's numbers too; copy them here if that ever gets in the way
from tests.integration.test_full_pipeline import (
    FRAME,
    PIPELINE,
    SUBSCRIBED,
    FakeEmotionModel,
    FakeGestures,
    FakeLLM,
    FakeVoice,
    FakeWhisper,
    Scripted,
)

FRAMES = 30  # 15 frames of speech, then silence: 0.6 s of audio
TIMEOUT = 10  # a turn that never completes fails here instead of hanging
MODULES = load_modules(list(PIPELINE))
CONFIGS = {
    n: ModuleConfig.from_dict({"name": n, "args": a}) for n, a in PIPELINE.items()
}


class Client:
    """The client's socket: done once the answer's text, speech and gestures
    have all arrived."""

    def __init__(self):
        self.done = asyncio.Event()
        self.text = self.speech = self.gestures = False

    async def send_bytes(self, payload):
        n = struct.unpack(">H", payload[:2])[0]
        topic, data = payload[2 : 2 + n].decode(), payload[2 + n :]
        if topic == "huri.audio.out" and Audio.from_wire(data).end:
            self.speech = True
        elif topic == "huri.motion":
            self.gestures = True
        self._check()

    async def send_json(self, message):
        if message["topic"] == "huri.token" and Token.from_wire(message["data"]).end:
            self.text = True
        self._check()

    def _check(self):
        if self.text and self.speech and self.gestures:
            self.done.set()


def new_turn():
    """A fresh session, as for each new client: not timed."""
    factory = ModuleFactory(
        {
            "stt": LocalHandle(FakeWhisper()),
            "emo": LocalHandle(FakeEmotionModel()),
            "rag": LocalHandle(FakeLLM()),
            "tts": LocalHandle(FakeVoice()),
            "mov": LocalHandle(FakeGestures()),
        }
    )
    for name, cls in MODULES.items():
        factory.register(name, cls)
    pipeline = factory.create_from_config("alice", CONFIGS)
    vad = next(m for m in pipeline if type(m).__name__ == "VAD")
    vad.vad = Scripted(speech_frames=15)
    client = Client()
    session = Session(pipeline + [Sender(client, topic) for topic in SUBSCRIBED])
    return (session, client), {}


def test_a_spoken_turn_through_the_curated_pipeline(benchmark, loop):
    def turn(session, client):
        async def talk():
            for _ in range(FRAMES):
                await session.publish("huri.audio.in", BytesEvent(FRAME))
            await asyncio.wait_for(client.done.wait(), TIMEOUT)
            # What the turn left running must not spill into the next round.
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()

        loop.run_until_complete(talk())

    benchmark.pedantic(turn, setup=new_turn, rounds=30, warmup_rounds=2)
