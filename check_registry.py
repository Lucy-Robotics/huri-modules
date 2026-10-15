#!/usr/bin/env python3
"""Check registry.toml against the packages it lists (run by CI on every change).

    python check_registry.py [--run-tests] [--strict]

For each listed module: its package is where `source` says, provides the module
under that name, ships a manifest that agrees with the entry, passes HuRI's
startup compatibility checks against the installed huri-core and events, and
only produces topics of an event package it declares. Every curated module
(packages/huri-module-*) must be listed. --run-tests also runs each listed
package's own tests (huri_core.testing, no Ray needed).

A `source` is one of two kinds:

    source = { path = "packages/huri-module-stt" }       # curated, in this repo
    source = { git = "https://github.com/acme/huri-module-servo", tag = "0.1.0",
               submodule = "modules/servo" }            # verified third party
               # optional: subdirectory = "<dir in that repository>"

A third-party entry is checked like a curated one when its submodule is checked
out (`git submodule update --init`), and its checkout must then be at `tag`. A
submodule that is not checked out is skipped and named; --strict makes it a
problem.

Needs the listed packages installed (pip install -e), like the server.
"""

import argparse
import datetime as dt
import subprocess
import sys
import tomllib
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Dict, List, Optional

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
# Source kind -> (required keys, optional keys).
SOURCE_KEYS = {
    "path": ({"path"}, set()),
    "git": ({"git", "tag", "submodule"}, {"subdirectory"}),
}


def _pyproject(path: Path) -> Dict[str, Any]:
    project: Dict[str, Any] = tomllib.loads((path / "pyproject.toml").read_text())[
        "project"
    ]
    return project


def _norm(name: str) -> str:
    return name.replace("_", "-").lower()


def _git(directory: Path, *args: str) -> Optional[str]:
    res = subprocess.run(
        ["git", "-C", str(directory), *args], capture_output=True, text=True
    )
    return res.stdout.strip() if res.returncode == 0 else None


def source_dir(
    source: Any, where: str, repo: Path, problems: List[str], skipped: List[str]
) -> Optional[Path]:
    """The directory holding the entry's package, or None when it cannot be
    checked: a malformed source (added to `problems`) or a third-party
    submodule that is not checked out (added to `skipped`)."""
    if not isinstance(source, dict):
        problems.append(f"{where}: 'source' must be a table")
        return None
    kinds = [kind for kind in SOURCE_KEYS if kind in source]
    if len(kinds) != 1:
        problems.append(f"{where}: 'source' needs exactly one of 'path' or 'git'")
        return None
    required, optional = SOURCE_KEYS[kinds[0]]
    missing = sorted(required - set(source))
    unknown = sorted(set(source) - required - optional)
    if missing or unknown:
        problems.append(
            f"{where}: {kinds[0]} source"
            + (f" lacks {missing}" if missing else "")
            + (f" has unknown {unknown}" if unknown else "")
        )
        return None
    if not all(isinstance(value, str) and value for value in source.values()):
        problems.append(f"{where}: every 'source' value must be a non-empty string")
        return None
    if "path" in source:
        return repo / str(source["path"])

    if not source["git"].startswith("https://"):
        problems.append(f"{where}: git source must be an https URL")
        return None
    submodule = Path(source["submodule"])
    if submodule.parent != Path("modules"):
        problems.append(f"{where}: submodule must be modules/<name>, not {submodule}")
        return None
    checkout = repo / submodule
    # A submodule's checkout has a .git file; an uninitialised one is empty.
    if not (checkout / ".git").exists():
        skipped.append(f"{where}: submodule {submodule} is not checked out")
        return None
    head = _git(checkout, "rev-parse", "HEAD")
    tagged = _git(checkout, "rev-parse", f"refs/tags/{source['tag']}^{{commit}}")
    if tagged is None:
        problems.append(f"{where}: {submodule} has no tag {source['tag']!r}")
        return None
    if head != tagged:
        problems.append(f"{where}: {submodule} is not at tag {source['tag']!r}")
        return None
    return checkout / str(source.get("subdirectory", "."))


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
    registry = tomllib.loads(registry_path.read_text())
    if registry.get("schema") != 1:
        return [f"unsupported schema {registry.get('schema')!r} (expected 1)"]

    # Which distribution registers each event namespace.
    namespace_owner: Dict[str, str] = {}
    for ep in entry_points(group="huri.events"):
        dist = getattr(ep, "dist", None)
        namespace_owner[topic_namespace(ep.name)] = _norm(dist.name) if dist else "?"

    for entry in registry.get("events", []):
        where = f"events {entry.get('package')!r}"
        source = source_dir(entry.get("source"), where, repo, problems, skipped)
        if source is None:
            continue
        if not (source / "pyproject.toml").is_file():
            problems.append(f"{where}: no package at source {source}")
            continue
        if _norm(_pyproject(source)["name"]) != _norm(entry["package"]):
            problems.append(f"{where}: {source} is {_pyproject(source)['name']}")
        owner = namespace_owner.get(entry.get("namespace", ""))
        if owner != _norm(entry["package"]):
            problems.append(
                f"{where}: namespace {entry.get('namespace')!r} is registered by "
                f"{owner or 'no installed package'}"
            )

    listed: Dict[str, Dict[str, Any]] = {}
    checked: List[str] = []  # listed and with a package at hand
    for entry in registry.get("module", []):
        name = entry.get("name", "<missing>")
        where = f"module {name!r}"
        if name in listed:
            problems.append(f"{where}: listed twice")
            continue
        listed[name] = entry
        if not isinstance(entry.get("verified"), dt.date):
            problems.append(f"{where}: 'verified' must be a date (YYYY-MM-DD)")
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
    for path in sorted((repo / "packages").glob("huri-module-*")):
        curated |= set(_pyproject(path).get("entry-points", {}).get("huri.modules", {}))
    for name in sorted(curated - set(listed)):
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
    registry = tomllib.loads(registry_path.read_text())
    sources = set()
    for entry in registry.get("module", []) + registry.get("events", []):
        source = source_dir(entry.get("source"), "", repo, [], [])
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
    args = parser.parse_args(argv)

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
