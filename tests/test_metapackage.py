"""meta/: the huri-modules metapackage stays in step with registry.toml and
packages/ — one extra per curated module, each pinning the exact version of
its package, with the registry's extra."""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
META = tomllib.loads((ROOT / "meta" / "pyproject.toml").read_text())["project"]
REGISTRY = tomllib.loads((ROOT / "registry.toml").read_text())
VERSIONS = {
    canonicalize_name(p["name"]): p["version"]
    for p in (
        tomllib.loads(path.read_text())["project"]
        for path in (ROOT / "packages").glob("*/pyproject.toml")
    )
}
EXTRAS = {
    canonicalize_name(name): [Requirement(r) for r in reqs]
    for name, reqs in META["optional-dependencies"].items()
}
# Extras that are not a registry module.
NOT_MODULES = {"events", "hub", "all"}


def test_each_registry_module_has_its_extra():
    modules = {canonicalize_name(m["name"]) for m in REGISTRY["module"]}
    assert modules == set(EXTRAS) - NOT_MODULES


def test_each_extra_pins_its_package_with_the_registry_extra():
    for module in REGISTRY["module"]:
        (req,) = EXTRAS[canonicalize_name(module["name"])]
        assert canonicalize_name(req.name) == canonicalize_name(module["package"])
        assert req.extras == ({module["extra"]} if "extra" in module else set())
        assert str(req.specifier) == f"=={VERSIONS[canonicalize_name(req.name)]}"


def test_events_and_hub_pin_their_package():
    for extra, package in (("events", "huri-events"), ("hub", "huri-hub")):
        (req,) = EXTRAS[extra]
        assert canonicalize_name(req.name) == package
        assert str(req.specifier) == f"=={VERSIONS[package]}"


def test_all_is_every_pip_complete_module_and_the_hub():
    (req,) = EXTRAS["all"]
    assert req.name == META["name"] and not req.specifier
    assert {canonicalize_name(e) for e in req.extras} == (
        set(EXTRAS) - NOT_MODULES - {"tts-cosyvoice"}
    ) | {"hub"}


def test_no_code_and_no_base_dependency():
    assert META["dependencies"] == []
