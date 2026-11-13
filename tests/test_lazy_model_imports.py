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
# qdrant_client is not one: it is a light HTTP client RAG imports at the top
# of rag.py, memory_maintenance.py and qdrant_utils.py.
MODEL_LIBRARIES: Dict[str, Tuple[str, ...]] = {
    "vad": (),
    "stt": ("faster_whisper",),
    "tag": (),
    "emo": ("torch", "transformers"),
    "eag": (),
    "emo_react": (),
    "emo_out": ("torch", "transformers"),
    "qag": (),
    "rag": ("sentence_transformers",),
    "mov": ("torch", "transformers", "librosa"),
    "tts": ("piper",),
    "tts_cosyvoice": ("torch", "torchaudio"),
    "lips": (
        "torch",
        "transformers",
        "onnx",
        "onnxruntime",
        "huggingface_hub",
        "scipy",
    ),
    "tts_phonemes": (
        "piper",
        "torch",
        "transformers",
        "onnx",
        "onnxruntime",
        "huggingface_hub",
        "scipy",
    ),
    "think": (),
    "think_say": ("piper",),
    "think_token": (),
    "think_stop": (),
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
    # An import hook, not `sys.modules[name] = None`: a library that tests
    # `name in sys.modules` (scipy does for torch) must see it as absent.
    code = (
        "import importlib, sys\n"
        + "".join(f"sys.path.insert(0, {p!r})\n" for p in path)
        + f"HIDDEN = {sorted(hidden)!r}\n"
        + "class Hide:\n"
        + "    def find_spec(self, name, path=None, target=None):\n"
        + "        if name.partition('.')[0] in HIDDEN:\n"
        + "            raise ModuleNotFoundError(f'{name} is hidden', name=name)\n"
        + "sys.meta_path.insert(0, Hide())\n"
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


def test_a_library_that_checks_sys_modules_still_imports(tmp_path):
    # scipy does `"torch" in sys.modules`, then uses it: a hidden library must
    # stay out of sys.modules, or scipy fails when torch is hidden.
    package = tmp_path / "huri_lips_lazy_check"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "module.py").write_text(
        "import sys\n\nassert 'torch' not in sys.modules\n\n\nclass Lips: ...\n"
    )
    target = "huri_lips_lazy_check.module:Lips"
    assert not import_failure(target, ["torch"], [str(tmp_path)])


def test_every_curated_module_lists_its_model_libraries():
    missing = unlisted(entry_points())
    assert not missing, f"add {missing} to MODEL_LIBRARIES, with their model libraries"


def test_a_new_curated_module_without_an_entry_in_the_table_fails_ci():
    assert unlisted([*entry_points(), "asr"]) == ["asr"]
