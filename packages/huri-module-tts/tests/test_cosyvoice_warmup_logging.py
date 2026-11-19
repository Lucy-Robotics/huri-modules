import logging
from types import SimpleNamespace

import pytest
from huri_tts.cosyvoice.handle import CosyVoiceTTSHandle


@pytest.mark.parametrize("result", ["success", "missing", "failure"])
def test_cosyvoice_warmup_logs_without_stdout(result, tmp_path, caplog, capsys):
    handle = object.__new__(CosyVoiceTTSHandle.func_or_class)
    sample = tmp_path / "voice.wav"
    if result != "missing":
        sample.write_bytes(b"test reference")
    handle.prompt_speech = str(sample)
    handle.prompt_text = "reference"

    def synthesize(*args, **kwargs):
        if result == "failure":
            raise RuntimeError("invalid reference WAV")
        return iter([object()])

    handle.model = SimpleNamespace(inference_zero_shot=synthesize)
    logger = logging.getLogger("ray.serve")
    logger.addHandler(caplog.handler)
    try:
        caplog.set_level(logging.INFO, logger="ray.serve")
        handle._warmup()
    finally:
        logger.removeHandler(caplog.handler)
    message = {
        "success": "warmup done",
        "missing": "warmup skipped",
        "failure": "warmup failed",
    }[result]
    expected_level = logging.INFO if result == "success" else logging.WARNING
    assert any(
        r.levelno == expected_level and message in r.getMessage()
        for r in caplog.records
    )
    if result == "failure":
        assert any("invalid reference WAV" in r.getMessage() for r in caplog.records)
    assert capsys.readouterr().out == ""
