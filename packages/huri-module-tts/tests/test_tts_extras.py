"""Each TTS engine comes with its own extra: the base package installs none."""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROJECT = tomllib.loads(
    (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
)["project"]


def test_tts_installs_no_engine_without_its_extra():
    base = {canonicalize_name(Requirement(r).name) for r in PROJECT["dependencies"]}
    assert not {"piper-tts", "torch", "torchaudio", "onnxruntime"} & base
    assert PROJECT["optional-dependencies"]["piper"] == ["piper-tts>=1.8,<1.9"]
