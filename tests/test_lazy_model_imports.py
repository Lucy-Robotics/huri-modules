"""Every curated module imports without its model libraries (#37): the Ray head
node imports every enabled module to build the app, whatever its hardware, and
only the GPU workers' deployments load the models."""

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]

# The import names of each curated module's model libraries: they may only be
# imported inside its functions (its deployment), never at the top of a file.
MODEL_LIBRARIES: Dict[str, Tuple[str, ...]] = {
    "vad": (),
    "stt": ("faster_whisper",),
    "tag": (),
    "emo": ("torch", "transformers"),
    "eag": (),
    "qag": (),
    "rag": ("sentence_transformers", "qdrant_client"),
    "mov": ("torch", "transformers", "librosa"),
    "tts": ("piper",),
    "tts_cosyvoice": ("torch", "torchaudio"),
}


def entry_points() -> Dict[str, str]:
    found = {}
    for path in sorted((ROOT / "packages").glob("huri-module-*")):
        project = tomllib.loads((path / "pyproject.toml").read_text())["project"]
        found.update(project["entry-points"]["huri.modules"])
    return found


def import_failure(target: str, hidden: Iterable[str], path: Iterable[str] = ()) -> str:
    """Imports `target` ("pkg.module:Class") in a fresh interpreter where the
    `hidden` libraries cannot be imported; returns the error, or ""."""
    module, attr = target.split(":")
    code = (
        "import importlib, sys\n"
        + "".join(f"sys.path.insert(0, {p!r})\n" for p in path)
        + "".join(f"sys.modules[{name!r}] = None\n" for name in hidden)
        + f"getattr(importlib.import_module({module!r}), {attr!r})\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    if res.returncode == 0:
        return ""
    error = res.stderr.strip().splitlines()[-1]
    return f"{target} needs one of {sorted(hidden)} at import time: {error}"


def unlisted(names: Iterable[str]) -> List[str]:
    return sorted(set(names) - MODEL_LIBRARIES.keys())


@pytest.mark.parametrize("name,target", sorted(entry_points().items()))
def test_a_curated_module_imports_with_its_model_libraries_hidden(name, target):
    error = import_failure(target, MODEL_LIBRARIES.get(name, ()))
    assert not error, error


def test_a_model_library_imported_at_the_top_of_a_module_file_fails_ci(tmp_path):
    package = tmp_path / "huri_stt_lazy_check"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "module.py").write_text("import faster_whisper\n\n\nclass STT: ...\n")
    target = "huri_stt_lazy_check.module:STT"
    error = import_failure(target, ["faster_whisper"], [str(tmp_path)])
    assert target in error and "faster_whisper" in error


def test_every_curated_module_lists_its_model_libraries():
    missing = unlisted(entry_points())
    assert not missing, f"add {missing} to MODEL_LIBRARIES, with their model libraries"


def test_a_new_curated_module_without_an_entry_in_the_table_fails_ci():
    assert unlisted([*entry_points(), "asr"]) == ["asr"]
