"""scripts/module_requirements.py: per-module, per-hardware requirements."""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from packaging.requirements import Requirement as PepRequirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "module_requirements", ROOT / "scripts" / "module_requirements.py"
)
assert spec and spec.loader
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

ALL = sorted(mr.module_packages(ROOT))


def merged(variant, modules=ALL, root=ROOT):
    return mr.merge(mr.collect(list(modules), variant, root))


@pytest.mark.parametrize("variant", mr.VARIANTS)
def test_every_curated_module_installs_together_on_each_variant(variant):
    lines = merged(variant)
    assert not any(line.startswith("huri") for line in lines)  # installed separately


@pytest.mark.parametrize("variant", mr.VARIANTS)
def test_chosen_pins_satisfy_every_module_range(variant):
    reqs = mr.collect(ALL, variant, ROOT)
    pins = {r.name: r.pin for r in reqs if r.pin}
    for req in reqs:
        spec_ = PepRequirement(req.line.split(";")[0]).specifier
        if req.name in pins and spec_:
            assert pins[req.name] in spec_, f"{req.origin}: {req.line}"


def root_names(path):
    names = set()
    for line in (ROOT / path).read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match:
            names.add(canonicalize_name(match.group(1)))
    return names


@pytest.mark.parametrize(
    "variant,root_file",
    [("nvidia", "requirements-nvidia.txt"), ("amd", "requirements-amd.txt")],
)
def test_module_files_cover_the_root_hardware_files(variant, root_file):
    """Nothing a root hardware file installs is lost when modules own their pins."""
    covered = {
        canonicalize_name(mr.Requirement(line, "").name) for line in merged(variant)
    }
    assert root_names(root_file) - covered == set()


def test_variant_follows_the_host():
    nvidia = "\n".join(merged("nvidia", ["emo"]))
    amd = "\n".join(merged("amd", ["emo"]))
    assert "transformers==4.51.3" in nvidia and "torch==2.3.1" in nvidia
    assert "transformers==4.46.3" in amd and "torch==" not in amd


def write_package(
    root, name, modules, deps=(), variants=None, optional=None, extras=None
):
    """A package under root/packages. `variants` maps "nvidia" (whole package)
    or "nvidia.<module>" (one module) to a pin file's text; `optional` an extra
    to its dependencies, `extras` a module to its extra."""
    path = root / "packages" / name
    path.mkdir(parents=True)
    eps = "\n".join(f'{m} = "x:Y"' for m in modules)
    dep_lines = "".join(f'  "{d}",\n' for d in deps)
    text = (
        f'[project]\nname = "{name}"\ndependencies = [\n{dep_lines}]\n\n'
        f'[project.entry-points."huri.modules"]\n{eps}\n'
    )
    if optional:
        text += "\n[project.optional-dependencies]\n" + "".join(
            f"{extra} = {json.dumps(deps_)}\n" for extra, deps_ in optional.items()
        )
    if extras:
        text += "\n[tool.huri-modules.extras]\n" + "".join(
            f'{module} = "{extra}"\n' for module, extra in extras.items()
        )
    (path / "pyproject.toml").write_text(text)
    for variant, pins in (variants or {}).items():
        (path / f"requirements-{variant}.txt").write_text(pins)
    return path


def test_conflicting_pins_name_both_modules(tmp_path):
    write_package(
        tmp_path, "huri-module-a", ["a"], variants={"nvidia": "torch==2.3.1\n"}
    )
    write_package(
        tmp_path, "huri-module-b", ["b"], variants={"nvidia": "torch==2.8.0\n"}
    )
    with pytest.raises(
        mr.ConflictError, match="torch.*huri-module-a.*2.3.1.*huri-module-b.*2.8.0"
    ):
        merged("nvidia", ["a", "b"], tmp_path)
    assert merged("cpu", ["a", "b"], tmp_path) == []  # no cpu pins, no conflict


def test_unknown_module_lists_the_known_ones(tmp_path):
    write_package(tmp_path, "huri-module-a", ["a"])
    with pytest.raises(
        mr.ConflictError, match=r"unknown module\(s\) \['nope'\].*\['a'\]"
    ):
        merged("cpu", ["a", "nope"], tmp_path)


def test_cli_exits_2_with_the_reason(tmp_path, capsys):
    assert mr.main(["--variant", "cpu", "--modules", "nope"]) == 2
    assert "unknown module" in capsys.readouterr().err
    out = tmp_path / "req.txt"
    assert mr.main(["--variant", "amd", "--modules", "rag", "-o", str(out)]) == 0
    assert "transformers==4.46.3" in out.read_text()


def test_the_hub_and_the_events_are_not_modules():
    providers = {package.distribution for package in mr.module_packages(ROOT).values()}
    assert {"huri-hub", "huri-events"}.isdisjoint(providers)
    assert all(mr.INTERNAL.match(name) for name in ("huri-hub", "huri_hub"))


def test_parser_without_tomllib_reads_the_same(monkeypatch):
    """Python 3.10 has no tomllib: the fallback parser must agree on every package."""
    expected = {p.name: mr.read_pyproject(p) for p in (ROOT / "packages").iterdir()}
    monkeypatch.setitem(sys.modules, "tomllib", None)
    for name, package in expected.items():
        assert mr.read_pyproject(ROOT / "packages" / name) == package


def names(lines):
    return {canonicalize_name(mr.Requirement(line, "").name) for line in lines}


@pytest.mark.parametrize("variant", mr.VARIANTS)
def test_piper_never_gets_cosyvoice_s_stack(variant):
    """One package, two engines: planning `tts` brings Piper (its extra) and
    none of tts_cosyvoice's pins (its per-module files), and the reverse."""
    piper = names(merged(variant, ["vad", "stt", "tts"]))
    assert "piper-tts" in piper
    assert not {"torch", "onnxruntime-gpu", "transformers", "lightning"} & piper
    cosyvoice = merged(variant, ["tts_cosyvoice"])
    assert "piper-tts" not in names(cosyvoice)
    assert "torch" in names(cosyvoice)  # its extra
    if variant == "nvidia":
        assert any(line.startswith("onnxruntime-gpu==1.18.0") for line in cosyvoice)
        assert any(line.startswith("torch==2.3.1") for line in cosyvoice)
    if variant == "cpu":
        assert any(line.startswith("onnxruntime==1.18.0") for line in cosyvoice)


@pytest.mark.parametrize("variant", mr.VARIANTS)
def test_the_aggregators_stay_light(variant):
    """eag and qag only join events: planning them without emo / rag brings
    neither the classifier's torch stack nor rag's retrieval libraries."""
    heavy = {"torch", "torchaudio", "transformers", "sentence-transformers"}
    heavy |= {"qdrant-client", "httpx", "pypdf", "semantic-chunker"}
    assert not heavy & names(merged(variant, ["eag", "qag"]))
    assert {"torch", "transformers"} <= names(merged(variant, ["emo"]))
    assert {"qdrant-client", "sentence-transformers"} <= names(merged(variant, ["rag"]))


def test_origins_name_the_module_and_the_extra():
    lines = merged("nvidia", ["tts_cosyvoice"])
    torch = next(line for line in lines if line.startswith("torch=="))
    assert "huri-module-tts (nvidia, tts_cosyvoice)" in torch
    assert "huri-module-tts[cosyvoice]" in torch
    piper = next(line for line in merged("cpu", ["tts"]) if "piper" in line)
    assert piper.startswith("piper-tts>=1.8,<1.9") and "huri-module-tts[piper]" in piper


def test_a_module_pin_file_applies_only_to_its_module(tmp_path):
    write_package(
        tmp_path,
        "huri-module-a",
        ["light", "heavy"],
        variants={"nvidia": "numpy==1.26.4\n", "nvidia.heavy": "torch==2.3.1\n"},
    )
    assert names(merged("nvidia", ["light"], tmp_path)) == {"numpy"}
    assert names(merged("nvidia", ["heavy"], tmp_path)) == {"numpy", "torch"}
    assert merged("cpu", ["light", "heavy"], tmp_path) == []


def test_conflicts_are_found_across_module_pin_files(tmp_path):
    write_package(
        tmp_path, "huri-module-a", ["a"], variants={"nvidia.a": "torch==2.3.1\n"}
    )
    write_package(
        tmp_path, "huri-module-b", ["b", "c"], variants={"nvidia": "torch==2.8.0\n"}
    )
    with pytest.raises(
        mr.ConflictError,
        match=r"torch.*huri-module-a \(nvidia, a\).*huri-module-b \(nvidia\)",
    ):
        merged("nvidia", ["a", "c"], tmp_path)


def test_two_extras_of_one_package_conflict_too(tmp_path):
    write_package(
        tmp_path,
        "huri-module-a",
        ["x", "y"],
        optional={"ex": ["onnxruntime==1.18.0"], "ey": ["onnxruntime==1.20.0"]},
        extras={"x": "ex", "y": "ey"},
    )
    assert names(merged("cpu", ["x"], tmp_path)) == {"onnxruntime"}
    with pytest.raises(mr.ConflictError, match=r"huri-module-a\[ex\].*\[ey\]"):
        merged("cpu", ["x", "y"], tmp_path)


def test_a_module_pin_file_for_no_module_is_refused(tmp_path):
    write_package(
        tmp_path, "huri-module-a", ["a"], variants={"nvidia.typo": "torch==2.3.1\n"}
    )
    with pytest.raises(mr.ConflictError, match=r"requirements-nvidia.typo.txt"):
        merged("nvidia", ["a"], tmp_path)


@pytest.mark.parametrize(
    "extras,fragment",
    [
        ({"a": "nope"}, "needs extra 'nope'"),
        ({"b": "ex"}, "names 'b', not one of its modules"),
    ],
)
def test_a_module_extra_must_exist(tmp_path, extras, fragment):
    write_package(
        tmp_path, "huri-module-a", ["a"], optional={"ex": ["x"]}, extras=extras
    )
    with pytest.raises(mr.ConflictError, match=fragment):
        mr.module_packages(tmp_path)


def test_parser_without_tomllib_reads_extras(tmp_path, monkeypatch):
    path = write_package(
        tmp_path,
        "huri-module-a",
        ["a", "b"],
        deps=["numpy>=1.26"],
        optional={"ex": ["piper-tts>=1.8,<1.9", "x"], "ey": ["torch"]},
        extras={"a": "ex", "b": "ey"},
    )
    expected = mr.read_pyproject(path)
    assert expected.extras == {"a": "ex", "b": "ey"}
    assert expected.optional["ex"] == ["piper-tts>=1.8,<1.9", "x"]
    monkeypatch.setitem(sys.modules, "tomllib", None)
    assert mr.read_pyproject(path) == expected
