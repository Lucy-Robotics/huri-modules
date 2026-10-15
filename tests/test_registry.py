"""registry.toml and its checker (check_registry.py), which reads it with
huri-hub's parser (huri_hub.registry): the schema rules have one home."""

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


def test_the_schema_is_huri_hubs(check):
    # The same reader as `huri hub`: what it refuses, CI refuses.
    assert check("schema = 3\n")[0].endswith(
        "has registry schema 3; this huri-hub reads schema 2: upgrade huri-hub, "
        "or use a huri-modules release built for it (HURI_MODULES_REF)"
    )
    text = REGISTRY.read_text().replace(
        'huri_core = ">=0.4.0.dev0,<0.5"', 'huri_core = "bogus"', 1
    )
    # Reported once, as malformed: the entry is there, not missing.
    problems = check(text)
    assert len(problems) == 1, problems
    assert "invalid huri_core range 'bogus'" in problems[0]


def test_the_schema_key_is_required(check):
    # `huri hub` reads a registry without it as its own schema; CI does not.
    text = REGISTRY.read_text()
    assert text.startswith("schema = 2\n") or "\nschema = 2\n" in text
    problems = check(text.replace("schema = 2\n", "", 1))
    assert len(problems) == 1 and problems[0].endswith("missing `schema` (expected 2)")


def test_a_module_listed_twice(check):
    text = REGISTRY.read_text()
    start = text.index("[[module]]")
    end = text.index("[[module]]", start + 1)
    problems = check(text + "\n" + text[start:end])
    assert len(problems) == 1 and problems[0].endswith(": listed twice"), problems


@pytest.mark.parametrize(
    "key,fragment",
    [
        ("package", "`package` must be a non-empty string"),
        ("namespace", "`namespace` must be a non-empty string"),
    ],
)
def test_a_malformed_events_entry_is_reported(check, key, fragment):
    text = REGISTRY.read_text()
    start = text.index("[[events]]")
    end = text.index("[[module]]")
    entry = "\n".join(
        line for line in text[start:end].splitlines() if not line.startswith(key)
    )
    problems = check(text[:start] + entry + "\n\n" + text[end:])
    assert any(fragment in p for p in problems), problems


def test_every_package_is_listed_or_a_known_library_or_tool(check, monkeypatch):
    assert set(checker.UNLISTED) == {"huri-tts", "huri-hub"}
    monkeypatch.delitem(checker.UNLISTED, "huri-hub")
    assert check(REGISTRY.read_text()) == [
        "packages/huri-hub provides no module and no event, and is not a known "
        "library or tool (check_registry.UNLISTED)"
    ]


def test_an_event_package_missing_from_the_registry_is_reported(check):
    text = REGISTRY.read_text()
    start = text.index("[[events]]")
    end = text.index("[[module]]")
    problems = check(text[:start] + text[end:])
    assert "curated event package 'huri-events' (packages/) is not in the " in (
        " ".join(problems)
    )


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
        ('{ path = "packages/x", git = "https://x.org/a" }', "not both"),
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
            "must pin a `tag`",
        ),
        (
            '{ git = "https://x.org/a", tag = "1", submodule = "modules/a", '
            'subdirectory = "" }',
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


UPSTREAM = "https://x.org/a"  # the entry's `git`, served from a local repo


@pytest.fixture
def servo_checkout(tmp_path, monkeypatch):
    """A repo root with modules/servo checked out at tag 0.1.0 of UPSTREAM, a
    local repository that git reaches through `url.<file>.insteadOf`. The
    checkout has no tags of its own, as after CI's shallow submodule fetch."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q")
    (upstream / "pyproject.toml").write_text('[project]\nname = "x"\n')
    git(upstream, "add", ".")
    git(upstream, "commit", "-qm", "0.1.0")
    git(upstream, "tag", "0.1.0")
    git(upstream, "tag", "-a", "-m", "annotated", "0.1.0-annotated")
    git(upstream, "commit", "-q", "--allow-empty", "-m", "0.2.0")
    git(upstream, "tag", "0.2.0")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{upstream.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", UPSTREAM)

    root = tmp_path / "root"
    (root / "modules").mkdir(parents=True)
    git(root / "modules", "clone", "-q", "--no-tags", UPSTREAM, "servo")
    git(root / "modules" / "servo", "checkout", "-q", "HEAD~1")
    (root / ".gitmodules").write_text(
        '[submodule "servo"]\n\tpath = modules/servo\n\turl = ' + UPSTREAM + "\n"
    )
    return root


def source_of(root, tag="0.1.0", git_url=UPSTREAM, **extra):
    source = {"git": git_url, "tag": tag, "submodule": "modules/servo"}
    problems, skipped = [], []
    found = checker.source_dir({**source, **extra}, "servo", root, problems, skipped)
    return found, problems + skipped


def test_a_checked_out_submodule_must_be_at_its_remote_tag(servo_checkout):
    checkout = servo_checkout / "modules" / "servo"
    assert source_of(servo_checkout) == (checkout, [])
    assert source_of(servo_checkout, subdirectory="pkg") == (checkout / "pkg", [])
    # an annotated tag is compared by the commit it points to
    assert source_of(servo_checkout, tag="0.1.0-annotated") == (checkout, [])

    assert source_of(servo_checkout, tag="0.9.0")[1] == [
        f"servo: {UPSTREAM} has no tag '0.9.0'"
    ]
    (problem,) = source_of(servo_checkout, tag="0.2.0")[1]
    assert problem.startswith("servo: modules/servo is at ")
    assert f"but tag '0.2.0' of {UPSTREAM} is" in problem


def test_the_submodule_must_come_from_the_entry_repository(servo_checkout):
    gitmodules = servo_checkout / ".gitmodules"
    text = gitmodules.read_text()
    # the same repository, spelled with .git or a trailing slash
    gitmodules.write_text(text.replace(UPSTREAM, UPSTREAM + ".git"))
    assert source_of(servo_checkout)[1] == []
    assert source_of(servo_checkout, git_url=UPSTREAM + "/")[1] == []

    gitmodules.write_text(text.replace(UPSTREAM, "https://evil.org/a"))
    assert source_of(servo_checkout)[1] == [
        f"servo: .gitmodules points modules/servo at https://evil.org/a, "
        f"not {UPSTREAM}"
    ]
    gitmodules.unlink()
    assert source_of(servo_checkout)[1] == [
        "servo: modules/servo is not a submodule in .gitmodules"
    ]


def test_an_unreadable_repository_is_a_problem(servo_checkout, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "GIT_CONFIG_KEY_0", f"url.{(tmp_path / 'gone').as_uri()}.insteadOf"
    )
    assert source_of(servo_checkout)[1] == [
        f"servo: cannot list the tags of {UPSTREAM}"
    ]


def test_install_dirs_follow_the_registry(servo_checkout, tmp_path, monkeypatch):
    registry = tmp_path / "registry.toml"
    servo = SERVO.replace("https://github.com/acme/huri-module-servo", UPSTREAM)
    registry.write_text(REGISTRY.read_text() + servo)
    checkout = servo_checkout / "modules" / "servo"
    assert checker.install_dirs(registry, servo_checkout) == [checkout]
    registry.write_text(
        REGISTRY.read_text()
        + servo.replace(
            'submodule = "modules/servo"',
            'submodule = "modules/servo", subdirectory = "python"',
        )
    )
    assert checker.install_dirs(registry, servo_checkout) == [checkout / "python"]
    # no network needed: a tag that does not exist is not looked up
    monkeypatch.setenv(
        "GIT_CONFIG_KEY_0", f"url.{(tmp_path / 'gone').as_uri()}.insteadOf"
    )
    assert checker.install_dirs(registry, servo_checkout) == [checkout / "python"]
    # a submodule that is not checked out installs nothing
    assert checker.install_dirs(registry, ROOT) == []
