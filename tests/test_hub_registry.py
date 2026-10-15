"""This repository's registry.toml, read by huri-core's `huri hub`: the two
share its schema, so a change to either is checked here against the real file
(offline: every URL is faked)."""

import tomllib
from pathlib import Path

import pytest

hub = pytest.importorskip("huri_core.hub")

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "registry.toml"
REPO = "git+https://github.com/Lucy-Robotics/huri-modules.git"
ENTRIES = tomllib.loads(REGISTRY.read_text())["module"]


def remote(text, monkeypatch, ref="0.1.0"):
    """The registry as `huri hub` reads it outside a checkout."""
    monkeypatch.setenv("HURI_MODULES_REF", ref)
    url = "https://example.org/registry.toml"
    return {m.name: m for m in hub.load_registry(url, fetch=lambda u: text)}


def test_every_entry_is_read_as_verified_with_its_range(monkeypatch):
    modules = remote(REGISTRY.read_text(), monkeypatch)
    assert set(modules) == {entry["name"] for entry in ENTRIES}
    for entry in ENTRIES:
        module = modules[entry["name"]]
        assert module.verified and module.summary == entry["summary"]
        assert module.huri_core == entry["huri_core"]


def test_curated_modules_install_from_this_repository_at_one_ref(monkeypatch):
    modules = remote(REGISTRY.read_text(), monkeypatch, ref="0.1.0")
    for entry in ENTRIES:
        path = entry["source"]["path"]
        assert modules[entry["name"]].install == f"{REPO}@0.1.0#subdirectory={path}"


def test_a_checkout_installs_its_own_packages():
    modules = {m.name: m for m in hub.load_registry(str(REGISTRY))}
    assert Path(modules["stt"].install) == ROOT / "packages" / "huri-module-stt"


@pytest.mark.parametrize(
    "extra,suffix", [("", ""), (', subdirectory = "python"', "#subdirectory=python")]
)
def test_a_verified_third_party_module_installs_from_its_pinned_tag(
    monkeypatch, extra, suffix
):
    servo = (
        '\n[[module]]\nname = "servo"\npackage = "huri-module-servo"\n'
        'summary = "Gesture to servo angle arrays"\nhuri_core = ">=0.4,<0.5"\n'
        "verified = 2026-10-12\n"
        'source = { git = "https://github.com/acme/huri-module-servo", '
        f'tag = "0.1.0", submodule = "modules/servo"{extra} }}\n'
    )
    modules = remote(REGISTRY.read_text() + servo, monkeypatch)
    assert modules["servo"].install == (
        f"git+https://github.com/acme/huri-module-servo@0.1.0{suffix}"
    )


def test_hub_lists_the_curated_modules_for_a_supported_core(capsys):
    argv = ["list", "--registry", str(REGISTRY), "--verified", "--core", "0.4.0"]
    assert hub.main(argv, fetch=lambda url: None) == 0
    listed = {line.split()[0] for line in capsys.readouterr().out.splitlines()[:-1]}
    assert listed == {entry["name"] for entry in ENTRIES}
