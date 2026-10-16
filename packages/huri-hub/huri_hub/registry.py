"""The verified registry: registry.toml of huri-modules, its schema and loading.

The one reader of that schema: `huri hub` installs from it, and huri-modules'
check_registry.py (run by CI on every change of the registry) checks the real
file with these same functions, adding only the checks that need the
repository itself (packages on disk, submodules, the modules' own tests). A new
key of the registry is a change here, released with huri-hub.

    schema = 2

    [[events]]                      # a curated event package
    package = "huri-events"
    namespace = "huri"
    source = { path = "packages/huri-events" }

    [[module]]
    name = "stt"
    package = "huri-module-stt"
    summary = "Speech to text"
    huri_core = ">=0.4,<0.5"
    verified = 2026-10-12
    source = { path = "packages/huri-module-stt" }    # curated, in huri-modules
    # or a verified third-party module, pinned as a submodule of huri-modules:
    # source = { git = "https://github.com/acme/huri-module-servo",
    #            tag = "0.1.0", submodule = "modules/servo" }
    #            (optional: subdirectory = "<dir in that repository>")
    extra = "cosyvoice"   # optional: the extra of `package` the module needs,
                          # installed as `<package>[<extra>] @ <source>`

Schema 2 added `extra`, and refuses a module entry key it does not know: an
older huri-hub would have ignored `extra` and installed a module without its
engine, so it reads no schema-2 registry (it says to upgrade instead), and a
key added later cannot be ignored silently either.
"""

import datetime as dt
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from huri_hub.web import RAW, Fetch, HubError, Warn, http_fetch, warn_stderr
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name

from huri_core.core.manifest import loads_toml

HURI_MODULES_REPO = "Lucy-Robotics/huri-modules"
REGISTRY_PATH = "registry.toml"
# What makes a directory a huri-modules checkout, besides its registry.
CHECKOUT_MARKER = "check_registry.py"
# The registry schema this huri-hub reads.
SCHEMA = 2
# The keys of a [[module]] entry; any other is refused.
MODULE_KEYS = {"name", "package", "summary", "huri_core", "verified", "source", "extra"}
# An extra's name (PEP 685: normalized names are what pip compares).
_EXTRA = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# The `source` of a registry entry: kind -> (required keys, optional keys).
SOURCE_KEYS = {
    "path": ({"path"}, set()),
    "git": ({"git", "tag", "submodule"}, {"subdirectory"}),
}


def modules_ref() -> str:
    """The git ref of huri-modules the default registry is read from and its
    curated packages installed from: one ref for both, so what is installed is
    what the registry describes. HURI_MODULES_REF (a huri-modules release tag,
    e.g. `0.1.0`, or a branch) overrides `main`, its latest release.

    huri-modules has its own versions, so this is not derived from the
    installed huri-core; instead `list` and `install` check each module's
    huri_core range against the installed huri-core."""

    return os.environ.get("HURI_MODULES_REF") or "main"


def default_registry() -> str:
    return RAW.format(repo=HURI_MODULES_REPO, ref=modules_ref(), path=REGISTRY_PATH)


# The registry of huri-modules at one ref, on GitHub.
_HURI_MODULES_REGISTRY = re.compile(
    re.escape(RAW.format(repo=HURI_MODULES_REPO, ref="@", path=REGISTRY_PATH))
    .replace("@", "(?P<ref>.+)")
    .join("^$")
)


def curated_install(path: str, ref: Optional[str] = None) -> str:
    """pip target of a curated package, a directory of huri-modules at `ref`
    (default: modules_ref())."""
    return (
        f"git+https://github.com/{HURI_MODULES_REPO}.git@{ref or modules_ref()}"
        f"#subdirectory={path}"
    )


@dataclass
class HubModule:
    name: str
    summary: str
    huri_core: Optional[str]  # supported range, None when unknown
    source: str  # where it comes from, for humans
    install: str  # what to give pip
    verified: bool
    # A curated package of huri-modules: its directory there ("packages/...").
    curated_path: Optional[str] = None
    # The extra of its package the module needs (registry `extra`), if any.
    extra: Optional[str] = None
    # The distribution providing it (registry `package`); None when unknown.
    package: Optional[str] = None

    def supports(self, core_version: str) -> Optional[bool]:
        """Whether this module supports that huri-core; None if unknown."""
        if not self.huri_core:
            return None
        return SpecifierSet(self.huri_core).contains(core_version, prereleases=True)


# --- the schema -------------------------------------------------------------------


def parse(text: str, location: str, require_schema: bool = False) -> Dict[str, Any]:
    """The registry in `text` (read from `location`), as TOML of a schema this
    huri-hub reads; HubError otherwise. `huri hub` reads a registry without a
    `schema` key as this one; `require_schema` (check_registry.py) refuses it.
    """
    try:
        registry = loads_toml(text)
    except ValueError as e:  # tomllib.TOMLDecodeError
        raise HubError(f"{location} is not valid TOML: {e}") from e
    if require_schema and "schema" not in registry:
        raise HubError(f"{location}: missing `schema` (expected {SCHEMA})")
    if registry.get("schema", SCHEMA) != SCHEMA:
        raise HubError(
            f"{location} has registry schema {registry.get('schema')!r}; this "
            f"huri-hub reads schema {SCHEMA}: upgrade huri-hub, or use a "
            "huri-modules release built for it (HURI_MODULES_REF)"
        )
    return registry


def check_source(where: str, source: Any) -> str:
    """The kind of a well-formed `source` table; HubError otherwise."""
    if not isinstance(source, dict):
        raise HubError(f"{where} has no `path` or `git` source")
    kinds = [kind for kind in SOURCE_KEYS if kind in source]
    if not kinds:
        raise HubError(f"{where} has no `path` or `git` source")
    if len(kinds) > 1:
        raise HubError(f"{where}: a source is either `path` or `git`, not both")
    kind = kinds[0]
    required, optional = SOURCE_KEYS[kind]
    if kind == "git" and not source.get("tag"):
        raise HubError(f"{where}: a git source must pin a `tag`")
    missing = sorted(required - set(source))
    unknown = sorted(set(source) - required - optional)
    if missing or unknown:
        raise HubError(
            f"{where}: {kind} source"
            + (f" lacks {missing}" if missing else "")
            + (f" has unknown {unknown}" if unknown else "")
        )
    if not all(isinstance(value, str) and value for value in source.values()):
        raise HubError(f"{where}: every `source` value must be a non-empty string")
    if kind == "git" and not source["git"].startswith("https://"):
        raise HubError(f"{where}: a git source must be an https URL")
    return kind


def _check_string(where: str, entry: Mapping[str, Any], key: str) -> None:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise HubError(f"{where}: `{key}` must be a non-empty string")


def check_events_entry(index: int, entry: Any) -> str:
    """The directory of the `index`-th (from 0) [[events]] entry, a curated
    package with its `package` and `namespace`; HubError when the entry is
    malformed."""
    if not isinstance(entry, dict):
        raise HubError(f"registry events #{index + 1} is not a table")
    package = entry.get("package")
    where = f"registry events {package or f'#{index + 1}'!r}"
    _check_string(where, entry, "package")
    _check_string(where, entry, "namespace")
    source: Any = entry.get("source")
    if check_source(where, source) != "path":
        raise HubError(f"{where}: event packages are curated, a `path` source")
    return str(source["path"])


def check_module_entry(index: int, entry: Any, seen: Optional[Set[str]] = None) -> str:
    """The name of the `index`-th (from 0) [[module]] entry, once its keys,
    name, huri_core range, `verified` date and `extra` are checked (its source
    is check_source's); HubError when one is malformed. With `seen`, the names
    of the entries before it: its name is added there, and is an error if it
    is already (a module is listed once)."""
    name = entry.get("name") if isinstance(entry, dict) else None
    if not isinstance(name, str) or not name:
        raise HubError(f"registry module #{index + 1} has no `name`")
    where = f"registry entry {name!r}"
    if seen is not None:
        if name in seen:
            raise HubError(f"{where}: listed twice")
        seen.add(name)
    unknown = sorted(set(entry) - MODULE_KEYS)
    if unknown:
        raise HubError(f"{where}: unknown key(s) {unknown}")
    if "extra" in entry:
        extra = entry["extra"]
        if not isinstance(extra, str) or not _EXTRA.match(extra):
            raise HubError(
                f"{where}: `extra` must be the name of an extra of its package "
                f'(lowercase, e.g. "cosyvoice"), not {extra!r}'
            )
        # The install target names the package: `<package>[<extra>] @ ...`.
        _check_string(where, entry, "package")
    huri_core = entry.get("huri_core")
    if huri_core is not None:
        try:
            SpecifierSet(str(huri_core))
        except InvalidSpecifier as e:
            raise HubError(f"{where}: invalid huri_core range {huri_core!r}") from e
    if not isinstance(entry.get("verified"), dt.date):
        raise HubError(f"{where}: 'verified' must be a date (YYYY-MM-DD)")
    return name


# --- reading it -------------------------------------------------------------------


def find_local_registry(start: Path) -> Optional[Path]:
    """registry.toml of the huri-modules checkout `start` is in, if any."""
    for directory in [start, *start.parents]:
        candidate = directory / REGISTRY_PATH
        if candidate.is_file() and (directory / CHECKOUT_MARKER).is_file():
            return candidate
    return None


@dataclass
class Registry:
    """A registry.toml, and where its curated packages are installed from."""

    location: str
    # huri-modules ref the curated packages not found in `checkout` come from.
    ref: str
    # Directory the registry was read from (a huri-modules checkout, or any
    # directory holding a registry.toml); None for a URL.
    checkout: Optional[Path]
    modules: List[HubModule]
    # Directories of the curated event packages ([[events]], "packages/...").
    events: List[str]

    def target(self, path: str) -> str:
        """pip target of a curated package: the checkout's directory when it
        has it, otherwise huri-modules at `ref`."""
        if self.checkout is not None and (self.checkout / path).is_dir():
            return str(self.checkout / path)
        return curated_install(path, self.ref)

    def read(self, path: str, fetch: Fetch) -> Optional[str]:
        """A file of huri-modules, from where `target` installs it."""
        if self.checkout is not None and (self.checkout / path).is_file():
            return (self.checkout / path).read_text()
        if self.checkout is not None and (self.checkout / path).parent.is_dir():
            return None  # that package is in the checkout, without this file
        return fetch(RAW.format(repo=HURI_MODULES_REPO, ref=self.ref, path=path))

    def companions(self, module: HubModule, fetch: Fetch) -> List[str]:
        """pip targets to install along with `module`.

        Modules depend on the curated packages of huri-modules by bare name
        (`huri-events>=0.1,<0.2`), and those are on no package index: they come
        from huri-modules, at the same ref as the module, in the same pip
        command, so pip never looks them up anywhere else (an index would let
        anyone publish a package of that name). Always the event packages
        ([[events]]); for a curated module also the other curated libraries it
        depends on, read from its pyproject.toml: a curated library is the
        directory packages/<its distribution name>.
        """
        paths = list(self.events)
        if module.curated_path is not None:
            paths += _curated_dependencies(module.curated_path, self, fetch)
        seen = {module.curated_path}
        targets = []
        for path in paths:
            if path not in seen:
                seen.add(path)
                targets.append(self.target(path))
        return targets


def _curated_dependencies(path: str, registry: Registry, fetch: Fetch) -> List[str]:
    """Directories of the curated libraries the package at `path` needs,
    directly or not (huri-core excluded: it is installed already)."""
    found: List[str] = []
    queue, seen = [path], {path}
    while queue:
        text = registry.read(f"{queue.pop(0)}/pyproject.toml", fetch)
        if text is None:
            continue
        try:
            requires = loads_toml(text).get("project", {}).get("dependencies", [])
        except ValueError:  # malformed TOML: pip will say so
            continue
        for requirement in requires:
            try:
                name = canonicalize_name(Requirement(requirement).name)
            except InvalidRequirement:
                continue
            directory = f"packages/{name}"
            if not name.startswith("huri-") or name == "huri-core":
                continue
            if directory in seen:
                continue
            seen.add(directory)
            if registry.read(f"{directory}/pyproject.toml", fetch) is not None:
                found.append(directory)
                queue.append(directory)
    return found


def _install_target(
    name: str,
    source: Mapping[str, Any],
    checkout: Optional[Path],
    ref: str,
    extra: Optional[Tuple[str, str]] = None,
) -> Tuple[str, str]:
    """(pip target, where it comes from) of a registry entry's `source`.

    path = "packages/<pkg>": a curated package of huri-modules. From a local
        checkout that has it, that directory; otherwise the huri-modules
        repository at `ref`.
    git = "<url>", tag = "<tag>" (subdirectory = "<dir>" optional): a verified
        third-party module, always installed at the reviewed tag. Its pinned
        submodule (`submodule = "modules/<name>"`) is for huri-modules' own CI
        and review, not needed here.

    `extra` = (package, extra): the module needs that extra of its package,
    named in the target: `<dir>[<extra>]` for a directory, otherwise
    `<package>[<extra>] @ <url>`.
    """

    kind = check_source(f"registry entry {name!r}", source)
    if kind == "path":
        path = str(source["path"])
        if checkout is not None and (checkout / path).is_dir():
            local = str(checkout / path)
            return (f"{local}[{extra[1]}]" if extra else local), path
        url, where = curated_install(path, ref), path
    else:
        url = f"git+{source['git']}@{source['tag']}"
        if source.get("subdirectory"):
            url += f"#subdirectory={source['subdirectory']}"
        where = f"{source['git']}@{source['tag']}"
    if extra:
        return f"{extra[0]}[{extra[1]}] @ {url}", where
    return url, where


def _curated_ref(location: str, is_url: bool, local: bool, warn: Warn) -> str:
    """The huri-modules ref curated packages are installed from, for a registry
    read at `location`: the ref of that registry when it is huri-modules' own
    on GitHub, otherwise modules_ref() (with a note, since nothing ties that
    registry to that ref)."""
    match = _HURI_MODULES_REGISTRY.match(location) if is_url else None
    override = os.environ.get("HURI_MODULES_REF")
    if match:
        ref = match.group("ref")
        if override and override != ref:
            warn(
                f"warning: HURI_MODULES_REF={override} ignored, the registry read "
                f"is huri-modules@{ref}: its curated packages come from {ref} too"
            )
        return ref
    if is_url or not local:
        warn(
            f"note: {location} is not huri-modules' own registry; curated packages "
            f"missing from it are installed from huri-modules@{modules_ref()} "
            "(HURI_MODULES_REF)"
        )
    return modules_ref()


def read_registry(
    location: str, fetch: Fetch = http_fetch, warn: Warn = warn_stderr
) -> Registry:
    """The verified registry, from a registry.toml path or URL, or a
    huri-modules checkout directory."""
    is_url = location.startswith(("http://", "https://"))
    if not is_url and Path(location).is_dir():
        location = str(Path(location) / REGISTRY_PATH)
    text: Optional[str] = None
    if is_url:
        text = fetch(location)
    elif Path(location).is_file():
        text = Path(location).read_text()
    if text is None:
        raise HubError(f"no registry at {location}")
    registry = parse(text, location)
    checkout = None if is_url else Path(location).resolve().parent
    local = checkout is not None and (checkout / CHECKOUT_MARKER).is_file()
    ref = _curated_ref(location, is_url, local, warn)

    events = [
        check_events_entry(i, entry)
        for i, entry in enumerate(registry.get("events", []))
    ]

    modules = []
    names: Set[str] = set()
    for i, entry in enumerate(registry.get("module", [])):
        name = check_module_entry(i, entry, names)
        source = entry.get("source", {})
        extra = entry.get("extra")
        install, where = _install_target(
            name, source, checkout, ref, (entry["package"], extra) if extra else None
        )
        modules.append(
            HubModule(
                name=name,
                summary=str(entry.get("summary", "")),
                huri_core=entry.get("huri_core"),
                source=where,
                install=install,
                verified=True,
                curated_path=str(source["path"]) if "path" in source else None,
                extra=extra,
                package=str(entry["package"]),
            )
        )
    return Registry(location, ref, checkout, modules, events)


def load_registry(
    location: str, fetch: Fetch = http_fetch, warn: Warn = warn_stderr
) -> List[HubModule]:
    """The verified modules of a registry (see read_registry)."""
    return read_registry(location, fetch, warn).modules
