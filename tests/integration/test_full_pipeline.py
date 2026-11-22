"""The whole curated pipeline in one session, no Ray and no model:

    audio in -> VAD -> STT -> TAG ┐
                    └> EMO -> EAG ┴> QAG -> RAG -> TTS -> MOV
                                                   │      │
                         client <- huri.token, huri.audio.out, huri.motion

Real module classes from the installed packages, built by the server's module
factory and wired by its event bus; the client side is the server's Sender.
Only the model deployments are fakes (huri_core.testing.LocalHandle).
"""

import asyncio

from huri_events import Token

from huri_core.core.dataclasses.config import ModuleConfig
from huri_core.core.events import BytesEvent
from huri_core.core.factory import ModuleFactory
from huri_core.core.plugins import load_events, load_modules
from huri_core.core.sender import Sender
from huri_core.core.session import Session
from huri_core.testing import LocalHandle
from tests.integration.fakes import (
    FRAME,
    PIPELINE,
    SUBSCRIBED,
    FakeEmotionModel,
    FakeGestures,
    FakeLLM,
    FakeVoice,
    FakeWhisper,
    FakeWS,
    Scripted,
)


def test_a_spoken_question_comes_back_as_text_speech_and_gestures():
    llm = FakeLLM()
    handles = {
        "stt": LocalHandle(FakeWhisper()),
        "emo": LocalHandle(FakeEmotionModel()),
        "rag": LocalHandle(llm),
        "tts": LocalHandle(FakeVoice()),
        "mov": LocalHandle(FakeGestures()),
    }
    factory = ModuleFactory(handles)
    for name, cls in load_modules(list(PIPELINE)).items():
        factory.register(name, cls)
    pipeline = factory.create_from_config(
        "alice",
        {
            n: ModuleConfig.from_dict({"name": n, "args": a})
            for n, a in PIPELINE.items()
        },
    )
    vad = next(m for m in pipeline if type(m).__name__ == "VAD")
    vad.vad = Scripted(speech_frames=15)

    ws = FakeWS()
    session = Session(pipeline + [Sender(ws, topic) for topic in SUBSCRIBED])
    events = load_events()

    def received(topic):
        return [events[t].from_wire(data) for t, data in ws.frames if t == topic]

    async def talk():
        for _ in range(30):  # 15 speech frames, then silence
            await session.publish("huri.audio.in", BytesEvent(FRAME))
            await asyncio.sleep(0.005)
        for _ in range(400):
            done = any(t.end for t in received("huri.token")) and any(
                a.end for a in received("huri.audio.out")
            )
            if done and received("huri.motion"):
                return
            await asyncio.sleep(0.01)

    asyncio.run(asyncio.wait_for(talk(), timeout=20))

    tokens = received("huri.token")
    assert "".join(t.text for t in tokens) == "You said: hello robot"
    assert tokens[-1] == Token("", True)
    assert llm.questions == ["hello robot"]

    audio = received("huri.audio.out")
    assert audio[-1].end and sum(a.data.size for a in audio) > 0
    assert [a.pts for a in audio[:-1]] == sorted(a.pts for a in audio[:-1])

    motion = received("huri.motion")
    assert motion and sum(m.poses.shape[0] for m in motion) > 0
