"""What can be checked of CosyVoice without its sources and model."""

import sys

from huri_tts.cosyvoice.handle import (
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
