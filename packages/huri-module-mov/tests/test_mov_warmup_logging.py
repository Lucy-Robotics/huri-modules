import logging
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from huri_mov.module import MOVHandle


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_warmup_progress_respects_log_level(level, monkeypatch, caplog, capsys):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    handle = object.__new__(MOVHandle.func_or_class)
    handle.device = SimpleNamespace(type="cpu")
    handle.infer = Mock()
    logger = logging.getLogger("ray.serve")
    logger.addHandler(caplog.handler)
    try:
        caplog.set_level(level, logger="ray.serve")
        handle._warmup()
    finally:
        logger.removeHandler(caplog.handler)
    assert handle.infer.call_count > 0
    assert any("warmup done" in r.getMessage() for r in caplog.records)
    assert any("warmup pass" in r.getMessage() for r in caplog.records) == (
        level == logging.DEBUG
    )
    assert capsys.readouterr().out == ""


def test_warmup_failure_is_a_nonfatal_warning(monkeypatch, caplog, capsys):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    handle = object.__new__(MOVHandle.func_or_class)
    handle.device = SimpleNamespace(type="cpu")
    handle.infer = Mock(side_effect=RuntimeError("inference unavailable"))
    logger = logging.getLogger("ray.serve")
    logger.addHandler(caplog.handler)
    try:
        caplog.set_level(logging.INFO, logger="ray.serve")
        handle._warmup()
    finally:
        logger.removeHandler(caplog.handler)
    assert any(
        r.levelno == logging.WARNING
        and "warmup failed" in r.getMessage()
        and "inference unavailable" in r.getMessage()
        for r in caplog.records
    )
    assert capsys.readouterr().out == ""
