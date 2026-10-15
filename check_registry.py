#!/usr/bin/env python3
"""Check registry.toml against the packages it lists (run by CI on every change).

    python check_registry.py [--run-tests] [--strict]
    python check_registry.py --install-dirs

For each listed module: its package is where `source` says, provides the module
under that name, ships a manifest that agrees with the entry, passes HuRI's
startup compatibility checks against the installed huri-core and events, and
only produces topics of an event package it declares, and names in `extra` the
extra its package maps it to ([tool.huri-modules.extras], an extra of its
[project.optional-dependencies]), or none when the package maps it to none.
Every curated module (packages/huri-module-*) must be listed. --run-tests also
runs each listed package's own tests (huri_core.testing, no Ray needed).

The registry's schema is huri-hub's (huri_hub.registry, what `huri hub` reads
it with): this checks every entry with it, then what needs this repository. A
`source` is one of two kinds:

    source = { path = "packages/huri-module-stt" }       # curated, in this repo
    source = { git = "https://github.com/acme/huri-module-servo", tag = "0.1.0",
               submodule = "modules/servo" }            # verified third party
               # optional: subdirectory = "<dir in that repository>"

A third-party entry is checked like a curated one when its submodule is checked
out (`git submodule update --init`). Its submodule must then be registered in
.gitmodules with the entry's `git` URL, and its checkout must be at the commit
that `tag` names in that repository, read with `git ls-remote <git>` (needs the
network; a shallow submodule checkout carries no tags). `huri hub` installs
`git+<git>@<tag>`, so this ties what is checked here to what it installs. A
submodule that is not checked out is skipped and named; --strict makes it a
problem.

--install-dirs prints the package directory of each checked-out third-party
entry (`modules/<name>[/<subdirectory>]`), one per line, for CI to
`pip install -e` them; it checks nothing else.

Every distribution under packages/ is classified: a module package or an
event package is listed, and anything else must be one of the known libraries
and tools (UNLISTED), so a new package cannot be left out silently.

Needs the listed packages and huri-hub installed (pip install -e), like the
server.
"""

import argparse
import subprocess
import sys
import tomllib
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from huri_hub.registry import (
    check_events_entry,
    check_module_entry,
    check_source,
    parse,
)
from huri_hub.web import HubError, git_env
from packaging.requirements import Requirement

from huri_core.core.compat import CompatibilityError, check_manifests, check_modules
from huri_core.core.manifest import ManifestError
from huri_core.core.plugins import (
    PluginError,
    find_manifest,
    load_events,
    load_modules,
    topic_namespace,
)

REPO = Path(__file__).resolve().parent
# Distributions of packages/ that provide no module and no event, so are not
# in the registry: a tool.
UNLISTED = {
    "huri-hub": "the `huri hub` command, this registry's reader",
}


def _pyproject(path: Path) -> Dict[str, Any]:
    project: Dict[str, Any] = tomllib.loads((path / "pyproject.toml").read_text())[
        "project"
    ]
    return project


def _module_extras(path: Path) -> Dict[str, str]:
    """[tool.huri-modules.extras] of a package: module -> the extra it needs."""
    pyproject = tomllib.loads((path / "pyproject.toml").read_text())
    extras: Dict[str, str] = (
        pyproject.get("tool", {}).get("huri-modules", {}).get("extras", {})
    )
    return extras


def _norm(name: str) -> str:
    return name.replace("_", "-").lower()


def load(registry_path: Path) -> Dict[str, Any]:
    """The registry, through huri-hub's reader; HubError when `huri hub`
    could not read it (not TOML, another schema) or when it does not say its
    schema (`huri hub` assumes its own, CI does not)."""
    return parse(registry_path.read_text(), str(registry_path), require_schema=True)


def _git(directory: Path, *args: str) -> Optional[str]:
    res = subprocess.run(
        ["git", "-C", str(directory), *args], capture_output=True, text=True
    )
    return res.stdout.strip() if res.returncode == 0 else None


def source_dir(
    source: Any,
    where: str,
    repo: Path,
    problems: List[str],
    skipped: List[str],
    verify: bool = True,
) -> Optional[Path]:
    """The directory holding the entry's package, or None when it cannot be
    checked: a malformed source or a submodule that is not the reviewed one
    (added to `problems`) or a third-party submodule that is not checked out
    (added to `skipped`). `verify=False` skips the checks against .gitmodules
    and the remote tag."""
    try:
        kind = check_source(where, source)
    except HubError as e:
        problems.append(str(e))
        return None
    if kind == "path":
        return repo / str(source["path"])

    # Where this repository pins it: a rule of this repository, not of the
    # schema (`huri hub` does not need the submodule).
    submodule = Path(source["submodule"])
    if submodule.parent != Path("modules"):
        problems.append(f"{where}: submodule must be modules/<name>, not {submodule}")
        return None
    checkout = repo / submodule
    # A submodule's checkout has a .git file; an uninitialised one is empty.
    if not (checkout / ".git").exists():
        skipped.append(f"{where}: submodule {submodule} is not checked out")
        return None
    package = checkout / str(source.get("subdirectory", "."))
    if not verify:
        return package

    url = _gitmodules_url(repo, submodule)
    if url is None:
        problems.append(f"{where}: {submodule} is not a submodule in .gitmodules")
        return None
    if not _same_repo(url, source["git"]):
        problems.append(
            f"{where}: .gitmodules points {submodule} at {url}, not {source['git']}"
        )
        return None
    head = _git(checkout, "rev-parse", "HEAD")
    tagged = remote_tag(source["git"], source["tag"])
    if tagged is None:
        problems.append(f"{where}: cannot list the tags of {source['git']}")
        return None
    if not tagged:
        problems.append(f"{where}: {source['git']} has no tag {source['tag']!r}")
        return None
    if head != tagged:
        problems.append(
            f"{where}: {submodule} is at {(head or '?')[:12]}, but tag "
            f"{source['tag']!r} of {source['git']} is {tagged[:12]}"
        )
        return None
    return package


def _same_repo(a: str, b: str) -> bool:
    def norm(url: str) -> str:
        url = url.rstrip("/")
        return url[: -len(".git")] if url.endswith(".git") else url

    return norm(a) == norm(b)


def _gitmodules_url(repo: Path, submodule: Path) -> Optional[str]:
    """The URL .gitmodules gives the submodule at `submodule`, found by its
    path (a submodule's name need not be its path)."""
    gitmodules = repo / ".gitmodules"
    if not gitmodules.is_file():
        return None
    paths = _git(
        repo, "config", "-f", str(gitmodules), "--get-regexp", r"^submodule\..*\.path$"
    )
    for line in (paths or "").splitlines():
        key, _, value = line.partition(" ")
        if Path(value) == submodule:
            name = key[len("submodule.") : -len(".path")]
            return _git(repo, "config", "-f", str(gitmodules), f"submodule.{name}.url")
    return None


def remote_tag(git_url: str, tag: str) -> Optional[str]:
    """The commit `tag` names in the repository at `git_url`: the peeled commit
    of an annotated tag. "" when there is no such tag, None when the
    repository cannot be read."""
    res = subprocess.run(
        [
            "git",
            "ls-remote",
            "--tags",
            git_url,
            f"refs/tags/{tag}",
            f"refs/tags/{tag}^{{}}",
        ],
        capture_output=True,
        text=True,
        env=git_env(),
    )
    if res.returncode != 0:
        return None
    refs: Dict[str, str] = {}
    for line in res.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        refs[ref] = sha
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}", "")


def install_dirs(registry_path: Path, repo: Path = REPO) -> List[Path]:
    """The package directory of each checked-out third-party entry."""
    registry = load(registry_path)
    dirs: List[Path] = []
    for entry in registry.get("events", []) + registry.get("module", []):
        source = entry.get("source")
        if not (isinstance(source, dict) and "git" in source):
            continue
        found = source_dir(source, "", repo, [], [], verify=False)
        if found is not None and found.resolve() not in {d.resolve() for d in dirs}:
            dirs.append(found)
    return dirs


def check(
    registry_path: Path,
    repo: Path = REPO,
    strict: bool = False,
    skipped: Optional[List[str]] = None,
) -> List[str]:
    """Every problem with the registry. Entries that could not be checked (a
    submodule not checked out) are added to `skipped`, and are problems too
    when `strict`."""
    problems: List[str] = []
    skipped = [] if skipped is None else skipped
    try:
        registry = load(registry_path)
    except HubError as e:
        return [str(e)]

    # Which distribution registers each event namespace.
    namespace_owner: Dict[str, str] = {}
    for ep in entry_points(group="huri.events"):
        dist = getattr(ep, "dist", None)
        namespace_owner[topic_namespace(ep.name)] = _norm(dist.name) if dist else "?"

    for i, entry in enumerate(registry.get("events", [])):
        try:
            package = repo / check_events_entry(i, entry)
        except HubError as e:
            problems.append(str(e))
            continue
        where = f"events {entry['package']!r}"
        if not (package / "pyproject.toml").is_file():
            problems.append(f"{where}: no package at source {package}")
            continue
        if _norm(_pyproject(package)["name"]) != _norm(entry["package"]):
            problems.append(f"{where}: {package} is {_pyproject(package)['name']}")
        owner = namespace_owner.get(entry["namespace"])
        if owner != _norm(entry["package"]):
            problems.append(
                f"{where}: namespace {entry['namespace']!r} is registered by "
                f"{owner or 'no installed package'}"
            )

    # Names of the module entries, also of those huri-hub's schema refuses:
    # such an entry is reported once, as malformed, not as missing too.
    listed: Set[str] = set()
    checked: List[str] = []  # listed, well-formed and with a package at hand
    for i, entry in enumerate(registry.get("module", [])):
        try:
            name = check_module_entry(i, entry, listed)
        except HubError as e:
            problems.append(str(e))
            continue
        where = f"module {name!r}"
        source = source_dir(entry.get("source"), where, repo, problems, skipped)
        if source is None:
            continue
        if not (source / "pyproject.toml").is_file():
            problems.append(f"{where}: no package at source {source}")
            continue
        checked.append(name)
        project = _pyproject(source)
        if _norm(project["name"]) != _norm(entry.get("package", "")):
            problems.append(
                f"{where}: {source} is {project['name']}, not {entry.get('package')}"
            )
        provided = project.get("entry-points", {}).get("huri.modules", {})
        if name not in provided:
            problems.append(f"{where}: {project['name']} provides {sorted(provided)}")
            continue
        extra = _module_extras(source).get(name)
        if entry.get("extra") != extra:
            problems.append(
                f"{where}: registry extra {entry.get('extra')!r} differs from "
                f"{project['name']}'s [tool.huri-modules.extras] {extra!r}"
            )
        elif extra is not None and extra not in project.get(
            "optional-dependencies", {}
        ):
            problems.append(
                f"{where}: {project['name']} has no extra {extra!r} in "
                "[project.optional-dependencies]"
            )
        try:
            manifest = find_manifest(name)
        except ManifestError as e:
            problems.append(f"{where}: {e}")
            continue
        if manifest is None:
            problems.append(
                f"{where}: {project['name']} ships no huri_module.toml for it"
            )
            continue
        for key in ("summary", "huri_core"):
            if getattr(manifest, key) != entry.get(key):
                problems.append(
                    f"{where}: registry {key} {entry.get(key)!r} differs from the "
                    f"manifest's {getattr(manifest, key)!r}"
                )
        if manifest.output is not None:
            owner = namespace_owner.get(topic_namespace(manifest.output.topic))
            declared = {_norm(Requirement(r).name) for r in manifest.events}
            if owner not in declared:
                problems.append(
                    f"{where}: produces {manifest.output.topic!r}, registered by "
                    f"{owner or 'no installed package'}, which its manifest's "
                    f"events {manifest.events} do not declare"
                )

    curated = set()
    events_listed = {
        _norm(str(e.get("package")))
        for e in registry.get("events", [])
        if isinstance(e, dict)
    }
    for path in sorted((repo / "packages").glob("*/pyproject.toml")):
        project = _pyproject(path.parent)
        provides = project.get("entry-points", {})
        curated |= set(provides.get("huri.modules", {}))
        if "huri.modules" in provides or _norm(project["name"]) in UNLISTED:
            continue
        if "huri.events" in provides:
            if _norm(project["name"]) not in events_listed:
                problems.append(
                    f"curated event package {project['name']!r} (packages/) is not "
                    "in the registry"
                )
            continue
        problems.append(
            f"packages/{path.parent.name} provides no module and no event, and is "
            "not a known library or tool (check_registry.UNLISTED)"
        )
    for name in sorted(curated - listed):
        problems.append(f"curated module {name!r} (packages/) is not in the registry")

    try:
        # A skipped submodule's module is not expected to be installed.
        modules = load_modules(sorted(checked))
        events = load_events()
        check_modules(modules, events)
        manifests = {}
        for name in modules:
            manifest = find_manifest(name)
            if manifest is not None:
                manifests[name] = manifest
        check_manifests(modules, manifests, events)
    except (PluginError, ValueError, CompatibilityError) as e:
        problems.append(f"startup compatibility: {e}")
    if strict:
        problems += skipped
    return problems


def run_tests(registry_path: Path, repo: Path = REPO) -> List[str]:
    registry = load(registry_path)
    sources = set()
    for entry in registry.get("module", []) + registry.get("events", []):
        # Already verified by check(), which runs first.
        source = source_dir(entry.get("source"), "", repo, [], [], verify=False)
        if source is not None:
            sources.add(source.resolve())
    failed = []
    for source in sorted(sources):
        tests = source / "tests"
        if not tests.is_dir():
            continue
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(tests)], cwd=repo
        )
        if res.returncode != 0:
            failed.append(f"{source.relative_to(repo.resolve())}: tests failed")
    return failed


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--registry", type=Path, default=REPO / "registry.toml")
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="a third-party submodule that is not checked out is a problem",
    )
    parser.add_argument(
        "--install-dirs",
        action="store_true",
        help="print the package directory of each checked-out third-party entry",
    )
    args = parser.parse_args(argv)

    if args.install_dirs:
        for directory in install_dirs(args.registry, REPO):
            print(directory.relative_to(REPO))
        return 0

    skipped: List[str] = []
    problems = check(args.registry, REPO, strict=args.strict, skipped=skipped)
    if args.run_tests and not problems:
        problems = run_tests(args.registry, REPO)
    if not args.strict:
        for note in skipped:
            print(f"  skipped {note} (git submodule update --init)")
    for problem in problems:
        print(f"- {problem}")
    print("registry OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
