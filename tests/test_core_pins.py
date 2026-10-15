"""The exact pins of this repository (requirements*.txt, constraints.txt), which
the images and the installer use, satisfy the ranges the installed huri-core
declares in its extras: installing it from HURI_CORE_REF (versions.env) next to
them must not need a resolver to give way."""

import re
from importlib.metadata import requires
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def pins():
    found = {}
    for path in ("requirements.txt", "serve_requirements.txt", "constraints.txt"):
        for line in (ROOT / path).read_text().splitlines():
            match = re.match(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s#]+)", line)
            if match:
                found[canonicalize_name(Requirement(match[1]).name)] = match[2]
    return found


def test_core_extras_accept_the_repo_pins():
    repo_pins = pins()
    declared = [Requirement(raw) for raw in requires("huri-core") or []]
    assert any(canonicalize_name(r.name) == "ray" for r in declared)  # [server]
    for req in declared:
        if canonicalize_name(req.name) == "huri-core":
            continue  # an extra referring to another one
        version = repo_pins.get(canonicalize_name(req.name))
        if version and req.specifier:
            assert version in req.specifier, f"huri-core: {req} vs =={version}"
