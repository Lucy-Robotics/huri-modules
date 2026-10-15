"""The curated module packages (packages/huri-module-*) are consistent."""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from huri_core.core.compat import check_manifests, check_modules
from huri_core.core.plugins import find_manifest, load_events, load_modules

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = sorted((ROOT / "packages").glob("huri-module-*"))


def project(path):
    return tomllib.loads((path / "pyproject.toml").read_text())["project"]


def pins():
    found = {}
    for name in ("requirements.txt", "serve_requirements.txt", "constraints.txt"):
        for line in (ROOT / name).read_text().splitlines():
            match = re.match(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s#]+)", line)
            if match:
                found[canonicalize_name(Requirement(match[1]).name)] = match[2]
    return found


@pytest.mark.parametrize("path", PACKAGES, ids=lambda p: p.name)
def test_every_module_ships_a_matching_manifest(path):
    names = list(project(path)["entry-points"]["huri.modules"])
    modules = load_modules(names)
    manifests = {name: find_manifest(name) for name in names}
    assert all(manifests.values()), manifests
    events = load_events()
    check_modules(modules, events)
    check_manifests(modules, manifests, events)


@pytest.mark.parametrize("path", PACKAGES, ids=lambda p: p.name)
def test_dependencies_accept_the_repo_pins(path):
    repo_pins = pins()
    optional = project(path).get("optional-dependencies", {})
    for raw in project(path)["dependencies"] + sum(optional.values(), []):
        req = Requirement(raw)
        pinned = repo_pins.get(canonicalize_name(req.name))
        if pinned and req.specifier:
            assert pinned in req.specifier, f"{path.name}: {raw} vs =={pinned}"


def test_each_curated_module_lives_in_exactly_one_package():
    seen = {}
    for path in PACKAGES:
        for name in project(path)["entry-points"]["huri.modules"]:
            assert name not in seen, f"{name} in {seen.get(name)} and {path.name}"
            seen[name] = path.name
    assert set(seen) == {
        "vad",
        "stt",
        "tag",
        "emo",
        "eag",
        "qag",
        "rag",
        "mov",
        "tts",
        "tts_cosyvoice",
    }


@pytest.mark.parametrize("path", PACKAGES, ids=lambda p: p.name)
def test_module_extras_name_its_modules_and_extras(path):
    """[tool.huri-modules.extras] (read by huri-modules' tooling): each key is
    a module of the package, each value one of its extras."""
    pyproject = tomllib.loads((path / "pyproject.toml").read_text())
    extras = pyproject.get("tool", {}).get("huri-modules", {}).get("extras", {})
    modules = project(path)["entry-points"]["huri.modules"]
    optional = project(path).get("optional-dependencies", {})
    for module, extra in extras.items():
        assert module in modules, f"{path.name}: {module} is not one of its modules"
        assert extra in optional, f"{path.name}: no extra {extra!r}"


def test_tts_installs_no_engine_without_its_extra():
    tts = ROOT / "packages" / "huri-module-tts"
    base = {
        canonicalize_name(Requirement(r).name) for r in project(tts)["dependencies"]
    }
    assert not {"piper-tts", "torch", "torchaudio", "onnxruntime"} & base
    assert project(tts)["optional-dependencies"]["piper"] == ["piper-tts>=1.8,<1.9"]
