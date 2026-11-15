"""What can be checked of CosyVoice without its sources and model."""

import asyncio
import sys
import threading

import pytest
from huri_tts.cosyvoice.handle import (
    _END_TEXT,
    CosyVoiceTTSHandle,
    _add_cosyvoice_to_path,
    _normalize_transcript,
)
from huri_tts.cosyvoice.module import CosyVoiceTTS


def test_a_bare_transcript_is_placed_after_the_prompt_marker():
    # Before the marker the LM reads it as an instruction and may speak it.
    bare = "Instinct creates its own oppressors."
    assert _normalize_transcript(bare) == (
        f"You are a helpful assistant.<|endofprompt|>{bare}"
    )


def test_an_explicit_instruction_is_kept():
    raw = "Speak softly.<|endofprompt|>Hello."
    assert _normalize_transcript(raw) == raw


def test_the_module_binds_its_own_deployment():
    assert CosyVoiceTTS._handle_cls is CosyVoiceTTSHandle
    # Not "TTSHandle": both engines can then run in one Serve app.
    assert CosyVoiceTTSHandle.name == "CosyVoiceTTSHandle"
    assert (CosyVoiceTTS.input_type, CosyVoiceTTS.output_type) == (
        "huri.token",
        "huri.audio.out",
    )


def test_cosy_dir_puts_the_checkout_and_matcha_on_the_path(tmp_path, monkeypatch):
    # The Serve configs set HURI_COSY_DIR only, no per-host PYTHONPATH.
    matcha = tmp_path / "third_party" / "Matcha-TTS"
    matcha.mkdir(parents=True)
    monkeypatch.setattr(sys, "path", list(sys.path))
    _add_cosyvoice_to_path(str(tmp_path))
    _add_cosyvoice_to_path(str(tmp_path))
    assert sys.path.count(str(tmp_path)) == 1
    assert sys.path.count(str(matcha)) == 1


def _replica(reading=None, released=None):
    cls = CosyVoiceTTSHandle.func_or_class
    h = cls.__new__(cls)

    class Model:
        def inference_zero_shot(self, text_iter, *a, **kw):
            for _ in text_iter:
                if reading:
                    reading.set()
            if released:
                released.set()
            return
            yield

    h.model = Model()
    h.prompt_text = h.prompt_speech = ""
    h.sample_rate = 24000
    h._text_queues = {}
    return h


def test_an_interrupted_cosyvoice_synthesis_releases_its_worker_thread():
    async def scenario():
        reading, released = threading.Event(), threading.Event()
        h = _replica(reading, released)
        await h.start_session("s")
        await h.push_text("s", "Hello there", False)
        text_q = h._text_queues["s"]
        agen = h.stream_audio("s")
        try:
            t = asyncio.ensure_future(agen.__anext__())
            await asyncio.to_thread(reading.wait, 1)
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            assert await asyncio.to_thread(released.wait, 1)
        finally:
            text_q.put(_END_TEXT)  # a red run must fail, not hang at shutdown

    asyncio.run(scenario())


def test_ending_a_cosyvoice_session_before_it_streams_leaves_no_state():
    async def scenario():
        h = _replica()
        await h.start_session("s")
        await h.push_text("s", "Hello there", False)
        await h.end_session("s")
        assert h._text_queues == {}

    asyncio.run(scenario())


def test_ending_a_cosyvoice_session_twice_is_harmless():
    async def scenario():
        h = _replica()
        await h.start_session("s")
        await h.end_session("s")
        await h.end_session("s")

    asyncio.run(scenario())


def test_a_cosyvoice_stream_opened_for_an_ended_session_yields_nothing():
    async def scenario():
        h = _replica()
        await h.start_session("s")
        await h.end_session("s")
        return [a async for a in h.stream_audio("s")]

    assert asyncio.run(scenario()) == []
