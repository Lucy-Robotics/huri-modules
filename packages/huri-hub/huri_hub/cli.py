"""Finding modules: the verified registry and the community.

Two sources, merged by module name:
  - the registry of the `huri-modules` repository (registry.toml): the
    curated, verified modules (huri_hub.registry);
  - GitHub repositories tagged `huri-module`, read without installing anything
    (huri_hub.community).

Nothing found here is ever imported or loaded: listing is not installing, and
installing is not enabling (HURI_MODULES does that, on the server).

`huri hub` once huri-core's `huri` command finds it (entry point group
`huri.commands`); `huri-hub` and `python -m huri_hub` are the same command.
"""

import argparse
import os
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, List, Optional

from huri_hub.community import TOPIC, catalog, read_repository, search
from huri_hub.registry import (
    HURI_MODULES_REPO,
    HubModule,
    Registry,
    default_registry,
    find_local_registry,
    read_registry,
)
from huri_hub.web import Fetch, HubError, Warn, http_fetch, warn_stderr
from packaging.version import InvalidVersion, Version


def installed_core_version() -> str:
    try:
        return version("huri-core")
    except PackageNotFoundError:
        from huri_core import __version__

        return __version__


def _registry_location(option: Optional[str], warn: Warn = warn_stderr) -> str:
    """--registry, else HURI_REGISTRY, else the huri-modules checkout the
    current directory is in, else huri-modules on GitHub at modules_ref()."""
    if os.environ.get("HURI_REF") and not os.environ.get("HURI_MODULES_REF"):
        warn(
            "warning: HURI_REF is ignored: it was renamed HURI_MODULES_REF, and is "
            "now a ref of huri-modules (e.g. 0.1.0), not of huri-core"
        )
    if option:
        return option
    if os.environ.get("HURI_REGISTRY"):
        return os.environ["HURI_REGISTRY"]
    local = find_local_registry(Path.cwd())
    if local is None:
        return default_registry()
    if os.environ.get("HURI_MODULES_REF"):
        warn(
            f"warning: HURI_MODULES_REF={os.environ['HURI_MODULES_REF']} ignored: "
            f"using the huri-modules checkout at {local.parent} (pass "
            f"--registry {default_registry()} to use that ref)"
        )
    return str(local)


def _read_registry(args: Any, fetch: Fetch) -> Registry:
    registry = read_registry(_registry_location(args.registry), fetch)
    warn_stderr(f"registry: {registry.location}")
    return registry


def cmd_search(args: Any, fetch: Fetch, out: Callable[[str], None]) -> int:
    repos = search(" ".join(args.query), fetch)
    for repo in repos:
        out(f"{repo['repo']:40s} {repo['description']}")
    out(f"{len(repos)} repositories tagged {TOPIC!r}")
    return 0


def cmd_list(args: Any, fetch: Fetch, out: Callable[[str], None]) -> int:
    core = args.core or installed_core_version()
    try:
        Version(core)
    except InvalidVersion as e:
        raise HubError(f"invalid huri-core version {core!r}") from e
    registry = _read_registry(args, fetch).modules
    community: List[HubModule] = []
    if not args.verified and not args.no_crawl:
        for repo in search("", fetch):
            community += read_repository(repo, fetch)
    shown = 0
    for module in catalog(registry, community):
        if args.verified and not module.verified:
            continue
        supported = module.supports(core)
        if supported is False and not args.all:
            continue
        mark = "verified " if module.verified else "community"
        range_ = module.huri_core or "huri-core range unknown"
        out(f"{module.name:16s} {mark} {range_:24s} {module.summary}")
        shown += 1
    out(f"{shown} module(s) for huri-core {core}")
    return 0


def cmd_install(args: Any, fetch: Fetch, out: Callable[[str], None]) -> int:
    target, name = args.module, None
    targets = [target]
    if not re.match(r"^(git\+|https?://|\.|/)", target):
        name = target
        registry = _read_registry(args, fetch)
        known = {m.name: m for m in registry.modules}
        if name in known:
            module = known[name]
            out(f"{name}: verified, from {module.source}")
        else:
            # Anyone can tag a repository and claim a name: never install an
            # unreviewed one implicitly, nor pick one of several claimants.
            candidates = [
                m
                for repo in search(name, fetch)
                for m in read_repository(repo, fetch)
                if m.name == name
            ]
            if not candidates:
                raise HubError(f"no module named {name!r} in the registry or on GitHub")
            if len(candidates) > 1:
                listed = "\n".join(f"  {m.install}" for m in candidates)
                raise HubError(
                    f"{len(candidates)} community repositories provide {name!r}; "
                    f"install one explicitly:\n{listed}"
                )
            module = candidates[0]
            if not args.community:
                raise HubError(
                    f"{name!r} is a community module, not reviewed ({module.source}). "
                    "Read its code first; install it with --community, or by URL: "
                    f"huri hub install {module.install}"
                )
            out(f"{name}: community (not reviewed), from {module.source}")
        core = installed_core_version()
        if module.supports(core) is False:
            # pip would otherwise "fix" it by replacing the installed huri-core.
            hint = (
                "Use the huri-modules release built for it (HURI_MODULES_REF="
                f"<tag>, see https://github.com/{HURI_MODULES_REPO}#readme)"
                if module.verified
                else "Use a release of it built for that huri-core"
            )
            raise HubError(
                f"{name!r} supports huri-core {module.huri_core}, but {core} is "
                f"installed. {hint}, or install it by URL."
            )
        targets = [module.install, *registry.companions(module, fetch)]
    command = [sys.executable, "-m", "pip", "install", *targets]
    out("$ " + " ".join(command))
    if not args.dry_run:
        code = subprocess.call(command)
        if code != 0:
            return code
    label = name or "<module name>"
    out(
        "Installed, not enabled. To use it, add it to the server's HURI_MODULES and "
        f"to the client config:\n  modules:\n    {label}:\n      name: {label}"
    )
    return 0


def main(argv: Optional[List[str]] = None, fetch: Fetch = http_fetch) -> int:
    """Find and install HuRI modules: the verified registry and the community."""
    parser = argparse.ArgumentParser(
        prog="huri hub", description=(__doc__ or "").split("\n\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="GitHub repositories tagged huri-module")
    p.add_argument("query", nargs="*")
    p.set_defaults(run=cmd_search)

    p = sub.add_parser("list", help="modules supporting a huri-core version")
    p.add_argument("--core", help="huri-core version (default: the installed one)")
    p.add_argument("--verified", action="store_true", help="the registry only")
    p.add_argument("--all", action="store_true", help="also unsupported versions")
    p.add_argument("--no-crawl", action="store_true", help="skip GitHub")
    p.add_argument(
        "--registry", help="registry.toml path or URL, or a huri-modules checkout"
    )
    p.set_defaults(run=cmd_list)

    p = sub.add_parser("install", help="pip-install a module (does not enable it)")
    p.add_argument("module", help="a module name, or a pip target (git+https://...)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--community",
        action="store_true",
        help="allow a module that is not in the verified registry",
    )
    p.add_argument(
        "--registry", help="registry.toml path or URL, or a huri-modules checkout"
    )
    p.set_defaults(run=cmd_install)

    args = parser.parse_args(argv)
    try:
        code: int = args.run(args, fetch, print)
        return code
    except HubError as e:
        print(f"huri hub: {e}", file=sys.stderr)
        return 1
