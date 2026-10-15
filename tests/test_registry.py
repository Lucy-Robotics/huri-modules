"""registry.toml and its checker (check_registry.py)."""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "registry.toml"
spec = importlib.util.spec_from_file_location(
    "check_registry", ROOT / "check_registry.py"
)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.fixture
def check(tmp_path):
    def run(text):
        path = tmp_path / "registry.toml"
        path.write_text(text)
        return checker.check(path, ROOT)

    return run


def without_entry(text, name):
    blocks = text.split("\n[[module]]\n")
    kept = [b for b in blocks if not b.startswith(f'name = "{name}"')]
    return "\n[[module]]\n".join(kept)


def test_the_registry_is_valid():
    assert checker.check(REGISTRY, ROOT) == []


def test_a_curated_module_missing_from_the_registry_is_reported(check):
    problems = check(without_entry(REGISTRY.read_text(), "tag"))
    assert problems == ["curated module 'tag' (packages/) is not in the registry"]


@pytest.mark.parametrize(
    "old,new,fragment",
    [
        (
            'package = "huri-module-tag"',
            'package = "huri-module-tagger"',
            "is huri-module-tag, not",
        ),
        (
            'source = { path = "packages/huri-module-tag" }',
            'source = { path = "packages/nope" }',
            "no package at source",
        ),
        (
            "Stitches overlapping transcript passes into one sentence",
            "Something else",
            "summary",
        ),
    ],
)
def test_an_entry_that_disagrees_with_its_package(check, old, new, fragment):
    text = REGISTRY.read_text()
    assert old in text
    problems = check(text.replace(old, new, 1))
    assert any(fragment in p for p in problems), problems


def test_verified_must_be_a_date(check):
    text = re.sub(
        r"verified = 2026-10-12",
        'verified = "yesterday"',
        REGISTRY.read_text(),
        count=2,
    )
    assert any("'verified' must be a date" in p for p in check(text))


def test_an_unknown_module_name(check):
    text = REGISTRY.read_text().replace('name = "tag"', 'name = "tagx"', 1)
    problems = check(text)
    assert any("module 'tagx'" in p and "provides ['tag']" in p for p in problems)


def test_events_namespace_must_belong_to_the_listed_package(check):
    text = REGISTRY.read_text().replace('namespace = "huri"', 'namespace = "acme"', 1)
    assert any("namespace 'acme'" in p for p in check(text))


# --- verified third-party modules: git sources pinned as submodules ----------

SERVO = (
    "\n[[module]]\n"
    'name = "servo"\n'
    'package = "huri-module-servo"\n'
    'summary = "Gesture to servo angle arrays"\n'
    'huri_core = ">=0.4.0.dev0,<0.5"\n'
    "verified = 2026-10-12\n"
    'source = { git = "https://github.com/acme/huri-module-servo", tag = "0.1.0", '
    'submodule = "modules/servo" }\n'
)


def test_a_submodule_not_checked_out_is_skipped_unless_strict(tmp_path):
    path = tmp_path / "registry.toml"
    path.write_text(REGISTRY.read_text() + SERVO)
    skipped = []
    assert checker.check(path, ROOT, skipped=skipped) == []
    assert skipped == ["module 'servo': submodule modules/servo is not checked out"]
    assert checker.check(path, ROOT, strict=True) == skipped


@pytest.mark.parametrize(
    "source,fragment",
    [
        ('{ git = "https://x.org/a", tag = "0.1.0" }', "lacks ['submodule']"),
        ('{ path = "packages/x", git = "https://x.org/a" }', "exactly one of"),
        (
            '{ git = "https://x.org", tag = "1", submodule = "modules/a", ref = "" }',
            "has unknown ['ref']",
        ),
        ('{ git = "http://x.org/a", tag = "1", submodule = "modules/a" }', "https"),
        (
            '{ git = "https://x.org/a", tag = "1", submodule = "vendor/a" }',
            "modules/<name>",
        ),
        (
            '{ git = "https://x.org/a", tag = "", submodule = "modules/a" }',
            "non-empty string",
        ),
    ],
)
def test_a_malformed_git_source_is_reported(check, source, fragment):
    text = SERVO.replace(SERVO.splitlines()[-1], f"source = {source}")
    problems = check(REGISTRY.read_text() + text)
    assert any(fragment in p for p in problems), problems


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def servo_checkout(tmp_path):
    """A repo root with modules/servo checked out at tag 0.1.0."""
    checkout = tmp_path / "modules" / "servo"
    checkout.mkdir(parents=True)
    git(checkout, "init", "-q")
    (checkout / "pyproject.toml").write_text('[project]\nname = "x"\n')
    git(checkout, "add", ".")
    git(checkout, "commit", "-qm", "0.1.0")
    git(checkout, "tag", "0.1.0")
    return tmp_path


def source_of(root, tag="0.1.0", **extra):
    source = {"git": "https://x.org/a", "tag": tag, "submodule": "modules/servo"}
    problems, skipped = [], []
    found = checker.source_dir({**source, **extra}, "servo", root, problems, skipped)
    return found, problems + skipped


def test_a_checked_out_submodule_must_be_at_its_tag(servo_checkout):
    checkout = servo_checkout / "modules" / "servo"
    assert source_of(servo_checkout) == (checkout, [])
    assert source_of(servo_checkout, subdirectory="pkg") == (checkout / "pkg", [])

    assert source_of(servo_checkout, tag="0.2.0")[1] == [
        "servo: modules/servo has no tag '0.2.0'"
    ]
    git(checkout, "commit", "-q", "--allow-empty", "-m", "after the tag")
    assert source_of(servo_checkout)[1] == [
        "servo: modules/servo is not at tag '0.1.0'"
    ]
