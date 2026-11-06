"""Piper's deployment logic, with the ONNX voice replaced by a fake."""

import asyncio
import os
from types import SimpleNamespace

import numpy as np
import pytest
from huri_events.text import Token
from huri_tts.piper import handle as piper_handle
from huri_tts.piper.handle import PiperTTSHandle
from huri_tts.piper.module import PiperTTS

from huri_core.testing import LocalHandle, ModuleHarness


class FakeVoice:
    """10 samples per character, in two chunks per phrase."""

    def synthesize(self, phrase, syn_config=None):
        half = len(phrase) * 5
        for _ in range(2):
            yield SimpleNamespace(audio_float_array=np.ones(half, dtype=np.float32))


def handle():
    deployment = PiperTTSHandle.func_or_class.__new__(PiperTTSHandle.func_or_class)
    deployment._voice = FakeVoice()
    deployment._syn_config = None
    deployment._sample_rate = 22050
    deployment._buffers, deployment._queues = {}, {}
    return deployment


async def phrases(h, tokens):
    await h.start_session("s")
    for text, end in tokens:
        await h.push_text("s", text, end)
    queue, out = h._queues["s"], []
    while not queue.empty():
        out.append(await queue.get())
    return out


def test_tokens_are_flushed_by_sentence_once_long_enough():
    tokens = [
        ("Dr. Smith is here. ", False),
        ("He says hello to everyone! ", False),
        ("Bye", False),
        ("", True),
    ]
    assert asyncio.run(phrases(handle(), tokens)) == [
        "Dr. Smith is here. He says hello to everyone!",
        "Bye",
        None,  # end of utterance
    ]


def test_streamed_audio_is_timed_and_ends_with_a_marker():
    async def run():
        h = handle()
        await h.start_session("s")
        await h.push_text("s", "Hello there.", True)
        return [a async for a in h.stream_audio("s")]

    audio = asyncio.run(run())
    assert [a.data.size for a in audio] == [60, 60, 0]
    assert [a.pts for a in audio] == [0.0, 60 / 22050, 120 / 22050]
    assert audio[-1].end and not audio[0].end


def test_the_module_drives_the_deployment_end_to_end():
    harness = ModuleHarness(PiperTTS, handle=LocalHandle(handle()))

    async def speak():
        tokens = [Token("Hello there, this is HuRI. ", False), Token("", True)]
        return await asyncio.gather(*(harness.feed(t) for t in tokens))

    first, rest = asyncio.run(speak())
    assert rest == [] and first[-1].end
    assert sum(a.data.size for a in first) == len("Hello there, this is HuRI.") * 10


def test_the_onnx_session_runs_without_a_memory_pattern(monkeypatch):
    """onnxruntime's memory-pattern planner caches buffer layouts per input
    shape, but the audio length Piper predicts varies run to run: the second
    phrase with an already-seen phoneme count failed in a Reshape node and the
    whole sentence was silently dropped. PiperVoice.load takes no session
    options, so the handle rebuilds the session itself."""

    onnxruntime = pytest.importorskip("onnxruntime")
    built = {}

    class FakeSession:
        def __init__(self, path, sess_options=None, providers=None):
            built.update(path=path, options=sess_options, providers=providers)

    monkeypatch.setattr(onnxruntime, "InferenceSession", FakeSession)
    session = PiperTTSHandle.func_or_class._build_session("voice.onnx")

    assert isinstance(session, FakeSession)
    assert built["path"] == "voice.onnx"
    assert built["options"].enable_mem_pattern is False
    assert built["providers"] == ["CPUExecutionProvider"]


@pytest.mark.skipif(
    not os.path.isfile(piper_handle._VOICE_PATH),
    reason="no Piper voice (set HURI_PIPER_VOICE)",
)
def test_same_phrase_can_be_spoken_twice():
    """With the real voice: three utterances of the same phrase all come out."""

    deployment = PiperTTSHandle.func_or_class()

    async def speak(session_id, text):
        await deployment.start_session(session_id)
        await deployment.push_text(session_id, text, end=True)
        return [audio async for audio in deployment.stream_audio(session_id)]

    for i in range(3):
        chunks = asyncio.run(speak(f"s{i}", "Sure thing!"))
        assert chunks[-1].end
        assert sum(c.data.size for c in chunks) > 0
