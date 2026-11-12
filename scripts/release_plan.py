"""Plan a release: what the Release workflow tags, publishes and uploads.

    python scripts/release_plan.py

Run by .github/workflows/release.yml, from a full clone (tags included). The
release is the `huri-modules` metapackage's version (meta/pyproject.toml); its
distributions are meta/ and every packages/*/.

- on a pull request into main (the release PR): checks the release is
  consistent, and fails it otherwise;
- on a push to main: a release when that version is not tagged yet, nothing
  otherwise (a merge into main that is not a release);
- on a manual run on main, once the version is tagged at this commit: resumes
  the upload.

Prints the plan (and adds it to the job summary), writes `release`, `create`,
`version`, `previous`, `core`, `cli`, `uploads` and `versions` to
$GITHUB_OUTPUT, exits 1 on a problem.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
OnPyPI = Callable[[str, str], bool]
ProjectOnPyPI = Callable[[str], bool]


class Dist(NamedTuple):
    name: str
    version: str
    path: str  # relative to ROOT
    requires: List[str]  # every dependency, extras included


def on_pypi(name: str, version: str) -> bool:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def project_on_pypi(name: str) -> bool:
    """Whether the project exists at all (a new one needs a pending publisher)."""
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=30):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def release_tags(tags: List[str]) -> List[Version]:
    """The release tags (`X.Y.Z`, no pre-release), oldest first."""

    versions = []
    for tag in tags:
        try:
            version = Version(tag)
        except InvalidVersion:
            continue
        if not version.is_prerelease and str(version) == tag:
            versions.append(version)
    return sorted(versions)


def read_dist(path: str) -> Dist:
    with open(ROOT / path / "pyproject.toml", "rb") as f:
        project = tomllib.load(f)["project"]
    requires = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requires += extra
    return Dist(project["name"], project["version"], path, requires)


def read_refs() -> Dict[str, str]:
    """versions.env's KEY=value lines."""

    refs = {}
    for line in (ROOT / "versions.env").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            refs[key.strip()] = value.strip()
    return refs


def problems(
    meta: Dist,
    packages: List[Dist],
    tags: List[str],
    pypi: OnPyPI,
    changed: List[str],
    refs: Dict[str, str],
) -> List[str]:
    """Why releasing `meta`'s version with these packages now would be wrong.

    `changed`: the packages (by path) that differ from the previous release.
    """

    found = []
    version = meta.version
    for dist in [meta, *packages]:
        if Version(dist.version).is_prerelease:
            found.append(
                f"{dist.name} {dist.version} is a pre-release: drop its "
                f"pre-release suffix (.devN, rcN, ...) in {dist.path}/pyproject.toml"
            )
        elif str(Version(dist.version)) != dist.version:
            found.append(
                f"{dist.name} {dist.version} is not canonical: write it "
                f"{Version(dist.version)} in {dist.path}/pyproject.toml"
            )
    if version in tags:
        found.append(
            f"tag {version} already exists: bump meta/pyproject.toml's version"
        )
    elif pypi(meta.name, version):
        found.append(
            f"huri-modules {version} is already on PyPI: bump meta/pyproject.toml's "
            "version"
        )
    for pkg in packages:
        if pkg.path in changed and pypi(pkg.name, pkg.version):
            found.append(
                f"{pkg.name} changed without a version bump: {pkg.version} is "
                f"already on PyPI, bump its version in {pkg.path}/pyproject.toml"
            )
    for key in ("HURI_CORE_REF", "HURI_CLI_REF"):
        if not release_tags([refs.get(key, "")]):
            found.append(
                f"{key} is {refs.get(key)!r} in versions.env: a release is built "
                "against a release tag (0.4.1), never dev or a branch"
            )
    for dist in [*packages, meta]:
        for req in dist.requires:
            if Requirement(req).url:
                found.append(
                    f"{dist.name} depends on {req!r}, a direct URL: PyPI refuses "
                    "it, depend on a released version"
                )
    return found


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def _output(**values: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.writelines(f"{k}={v}\n" for k, v in values.items())


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(text + "\n")


def main(pypi: OnPyPI = on_pypi, project: ProjectOnPyPI = project_on_pypi) -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "pull_request")
    ref = os.environ.get("GITHUB_REF")
    if event != "pull_request" and ref != "refs/heads/main":
        # A tag is forever: only main's commits are ever released.
        print(f"::error::releases run on main only, not {ref}")
        _output(release="false")
        return 1
    meta = read_dist("meta")
    packages = [
        read_dist(f"packages/{p.name}") for p in sorted((ROOT / "packages").iterdir())
    ]
    refs = read_refs()
    version = meta.version

    def plan() -> Dict[str, str]:
        # The distributions to upload (the metapackage last: it pins the
        # others), and every version, for the install check.
        uploads = [
            f"{d.name}=={d.version}" for d in packages if not pypi(d.name, d.version)
        ]
        return dict(
            version=version,
            core=refs.get("HURI_CORE_REF", ""),
            cli=refs.get("HURI_CLI_REF", ""),
            uploads=" ".join([*uploads, f"{meta.name}=={version}"]),
            versions=" ".join(f"{d.name}=={d.version}" for d in [*packages, meta]),
        )

    tags = _git("tag", "--list").stdout.split()
    # The release before this one (a patch on an older line compares to its own).
    released = [str(v) for v in release_tags(tags) if v < Version(version)]
    previous: Optional[str] = released[-1] if released else None

    if version in tags:
        tagged = _git("rev-list", "-n", "1", version).stdout.strip()
        head = _git("rev-parse", "HEAD").stdout.strip()
        if event == "push":
            _summary(f"huri-modules {version} is already tagged: not a release.")
            _output(release="false")
            return 0
        if event == "workflow_dispatch" and tagged == head:
            # Resume: the tag and the GitHub release exist, upload what is missing.
            _output(release="true", create="false", previous=previous or "", **plan())
            _summary(f"Resuming the upload of release {version}.")
            return 0

    # A package that is not in the previous release counts as changed; with no
    # previous release, every package does.
    changed = (
        [
            p.path
            for p in packages
            if _git("diff", "--quiet", previous, "HEAD", "--", p.path).returncode
        ]
        if previous
        else [p.path for p in packages]
    )
    found = problems(meta, packages, tags, pypi, changed, refs)
    if found:
        for problem in found:
            print(f"::error::{problem}")
        _summary("Release blocked:\n" + "\n".join(f"- {p}" for p in found))
        _output(release="false")
        return 1

    new: List[str] = []

    def action(dist: Dist) -> str:
        if pypi(dist.name, dist.version):
            return "already on PyPI, skipped"
        if not project(dist.name):
            new.append(dist.name)
            return "upload (new project: needs its pending publisher first)"
        return "upload"

    rows = [f"| PyPI `{d.name} {d.version}` | {action(d)} |" for d in packages]
    rows.append(f"| PyPI `{meta.name} {version}` | {action(meta)} |")
    warning = (
        [
            "",
            f"Warning: {len(new)} new PyPI projects ({', '.join(new)}), and PyPI "
            "allows one pending publisher at a time: the release will fail at the "
            'second. RELEASING.md, "Publishing setup", has the loop.',
        ]
        if len(new) > 1
        else []
    )

    _summary(
        "\n".join(
            [
                f"Merging this into main releases huri-modules **{version}**"
                + (f" (previous: {previous})" if previous else "")
                + ":",
                "",
                "| what | action |",
                "|---|---|",
                f"| tag `{version}` on main's merge commit | create |",
                f"| GitHub release `{version}` | publish, marked latest |",
                *rows,
                *warning,
            ]
        )
    )
    _output(
        release=json.dumps(event != "pull_request"),
        create="true",
        previous=previous or "",
        **plan(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
