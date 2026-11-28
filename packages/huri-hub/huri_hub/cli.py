"""Finding modules: the verified registry and the community.

Two sources, merged by module name:
  - the registry of the `huri-modules` repository (registry.toml): the
    curated, verified modules (huri_hub.registry);
  - GitHub repositories tagged `huri-module`, read without installing anything
    (huri_hub.community).

Nothing found here is ever imported or loaded: listing is not installing, and
installing is not enabling. `enable` (or `install --enable`) adds a module to
the server's HURI_MODULES: in .huri-local/modules.enabled, which ./install.sh
reads too, and in the configuration it generated.

`huri hub` once huri-core's `huri` command finds it (entry point group
`huri.commands`); `huri-hub` and `python -m huri_hub` are the same command.
"""

import argparse
import importlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from huri_hub.community import TOPIC, catalog, read_repository, search
from huri_hub.registry import (
    CHECKOUT_MARKER,
    HURI_MODULES_REPO,
    HubModule,
    Registry,
    default_registry,
    find_local_registry,
    read_registry,
)
from huri_hub.web import Fetch, HubError, Warn, git_env, http_fetch, warn_stderr
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from huri_core.core.manifest import loads_toml

# Distributions a curated package replaced. Left installed next to it, they
# register its module names with other targets (huri-core then loads no module
# at all), or own some of its files.
REPLACED = {
    "huri-module-tts": (
        "huri-tts",
        "huri-module-tts-piper",
        "huri-module-tts-cosyvoice",
    ),
}


def installed_core_version() -> str:
    try:
        return version("huri-core")
    except PackageNotFoundError:
        from huri_core import __version__

        return __version__


def installed_distributions() -> Dict[str, Set[str]]:
    """The installed distributions (normalized names), each with the names of
    the huri.modules entry points it registers."""
    found: Dict[str, Set[str]] = {}
    for dist in distributions():
        name = canonicalize_name(dist.metadata["Name"] or "")
        found.setdefault(name, set()).update(
            ep.name for ep in dist.entry_points if ep.group == "huri.modules"
        )
    return found


def stale_distributions(
    module: str, package: str, installed: Dict[str, Set[str]]
) -> List[str]:
    """Installed distributions other than `package` that would clash with it:
    one registering `module` too, or one `package` replaced."""
    own = canonicalize_name(package)
    replaced = {canonicalize_name(d) for d in REPLACED.get(own, ())}
    return sorted(
        dist
        for dist, modules in installed.items()
        if dist != own and (module in modules or dist in replaced)
    )


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


def installed_events_version() -> Optional[str]:
    try:
        return version("huri-events")
    except PackageNotFoundError:
        return None


def _install_editable(args: Any, out: Callable[[str], None]) -> int:
    """pip install -e the checkout (and its events/ package, in the template's
    layout), holding huri-core and huri-events at the installed versions."""
    root = Path(args.module).resolve()
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        raise HubError(
            f"--editable takes a local checkout (a directory with a pyproject.toml), "
            f"not {args.module!r}"
        )
    try:
        registered = (
            loads_toml(pyproject.read_text(encoding="utf-8"))
            .get("project", {})
            .get("entry-points", {})
            .get("huri.modules", {})
        )
        names = list(registered)
    except (OSError, ValueError, AttributeError) as e:
        raise HubError(f"cannot read {pyproject}: {e}") from e
    state = None
    if args.enable:
        names = [_enableable(n) for n in names]
        if not names:
            raise HubError(
                f"{pyproject} registers no module (a huri.modules entry point): "
                "nothing to enable"
            )
        state = state_dir()
    pins = [f"huri-core=={installed_core_version()}"]
    events = installed_events_version()
    if events:
        pins.append(f"huri-events=={events}")
    paths = [root] + [p for p in [root / "events"] if (p / "pyproject.toml").is_file()]
    # Without the pins pip may "fix" a range by replacing the installed libraries.
    with tempfile.TemporaryDirectory() as tmp:
        constraints = Path(tmp) / "constraints.txt"
        constraints.write_text("\n".join(pins) + "\n", encoding="utf-8")
        command = [sys.executable, "-m", "pip", "install", "-c", str(constraints)]
        for path in paths:
            command += ["-e", str(path)]
        out("$ " + shlex.join(command))
        out(f"constraints: {' '.join(pins)}")
        if not args.dry_run:
            code = subprocess.call(command, env=git_env())
            if code != 0:
                return code
    if state is None:
        for label in names or ["<module name>"]:
            out(
                "Installed, not enabled. To use it, add it to the server's "
                f"HURI_MODULES (huri hub enable {label}) and {_client_config(label)}"
            )
    else:
        for name in names:
            if args.dry_run:
                out(f"would enable {name} in {state / ENABLED_FILE}")
            else:
                importlib.invalidate_caches()
                _enable(name, state, out)
    out(
        "The pins of its requirements-<variant>.txt are not installed: install "
        "them yourself if it needs them."
    )
    return 0


def cmd_install(args: Any, fetch: Fetch, out: Callable[[str], None]) -> int:
    if args.editable:
        return _install_editable(args, out)
    target, name = args.module, None
    targets = [target]
    by_url = bool(re.match(r"^(git\+|https?://|\.|/)", target))
    state = None
    if args.enable:
        # Before pip runs: a failure after it would leave a half-done install.
        if by_url:
            raise HubError(
                "--enable needs a module name, and a URL is not one: run "
                "`huri hub enable <name>` after the install"
            )
        _enableable(target)
        state = state_dir()
    if not by_url:
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
        if module.package:
            stale = stale_distributions(name, module.package, installed_distributions())
            if stale:
                raise HubError(
                    f"{', '.join(stale)} would clash with {module.package} (the same "
                    "module names, or files it replaced): huri-core would load no "
                    f"module. Remove them first: pip uninstall {' '.join(stale)}"
                )
        targets = [module.install, *registry.companions(module, fetch)]
        readme = _readme(module, registry)
    else:
        readme = None
    command = [sys.executable, "-m", "pip", "install", *targets]
    # Quoted: "<package>[<extra>] @ <url>" is one argument, and [..] a glob.
    out("$ " + shlex.join(command))
    if not args.dry_run:
        # pip clones git+https targets: never let git prompt for a login.
        code = subprocess.call(command, env=git_env())
        if code != 0:
            if any("git+" in target for target in targets):
                warn_stderr(
                    "pip failed; a git repository it could not read (missing, "
                    "private, or a wrong tag) is not prompted for credentials"
                )
            return code
    label = name or "<module name>"
    if state is None:
        out(
            "Installed, not enabled. To use it, add it to the server's HURI_MODULES "
            f"(huri hub enable {label}) and {_client_config(label)}"
        )
    elif args.dry_run:
        out(
            f"would enable {label} in {state / ENABLED_FILE}. To use it in a "
            f"session, add it {_client_config(label)}"
        )
    else:
        importlib.invalidate_caches()  # pip just added distributions
        _enable(label, state, out)
    if readme:
        # pip installs the package and its extra; model weights, hardware pins
        # (requirements-<variant>[.<module>].txt) or a setup script are not
        # pip's to install.
        out(
            "What else it may need before it runs (model weights, hardware pins, "
            f"a setup script) is in its README: {readme}"
        )
    return 0


# The modules `huri hub enable` recorded, in the state directory (shared contract
# with ./install.sh): one name per line, blank lines and # comments ignored.
ENABLED_FILE = "modules.enabled"
MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:]*$")
RESTART = ".huri-local/stop.sh && .huri-local/start.sh"


def _module_name(name: str) -> str:
    # It is written into files a shell sources: nothing but a module name.
    if not MODULE_NAME.match(name):
        raise HubError(f"{name!r} is not a module name")
    return name


def _enableable(name: str) -> str:
    # ./install.sh dies on it in modules.enabled: the engine is its decision.
    if _module_name(name) == "tts_cosyvoice":
        raise HubError(
            "tts_cosyvoice is not listed by name, use tts with --tts-engine "
            "cosyvoice (./install.sh --tts-engine cosyvoice)"
        )
    return name


def state_dir() -> Path:
    """The .huri-local directory of the HuRI install to enable modules in:
    HURI_STATE (.huri-local/env.sh exports it), else the checkout whose .venv
    runs this command."""
    if os.environ.get("HURI_STATE"):
        return Path(os.environ["HURI_STATE"])
    checkout = Path(sys.prefix).parent
    if (checkout / CHECKOUT_MARKER).is_file():
        return checkout / ".huri-local"
    raise HubError(
        f"no HuRI install found (HURI_STATE is unset, and {sys.prefix} is not the "
        ".venv of a huri-modules checkout): source .huri-local/env.sh, or run the "
        "hub of the server's .venv"
    )


def _client_config(name: str) -> str:
    # A session only uses the modules its client config names.
    return f"to the client config:\n  modules:\n    {name}:\n      name: {name}"


def _read(path: Path) -> Optional[str]:
    """The text of `path`, None when there is no such file."""
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError as e:
        raise HubError(f"cannot read {path}: {e.strerror}") from e


def _write(path: Path, text: str) -> None:
    # Through a temporary file: a crash never leaves a half-written file that
    # the server, or ./install.sh, would then read.
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise HubError(f"cannot write {path}: {e.strerror}") from e


def _allow_list_files(state: Path) -> List[Tuple[Path, str]]:
    """The files ./install.sh generated, each with its HURI_MODULES line."""
    return [
        (
            state.parent / "config" / "huri_local.generated.yaml",
            r'^(\s*HURI_MODULES: ")([^"]*)(")',
        ),
        (state / "huri.env", r'^(HURI_MODULES=")([^"]*)(")'),
    ]


def _split(value: str) -> List[str]:
    return [n.strip() for n in value.split(",") if n.strip()]


def _allow_lists(state: Path) -> List[List[str]]:
    """HURI_MODULES in each of those files that exists."""
    found = []
    for path, pattern in _allow_list_files(state):
        text = _read(path)
        if text is not None:
            found += [_split(m.group(2)) for m in re.finditer(pattern, text, re.M)]
    return found


def _edit_allow_lists(state: Path, edit: Callable[[List[str]], List[str]]) -> None:
    """Apply `edit` to HURI_MODULES in those files (a missing one is skipped)."""
    for path, pattern in _allow_list_files(state):
        text = _read(path)
        if text is None:
            continue
        new = re.sub(
            pattern,
            lambda m: m.group(1) + ",".join(edit(_split(m.group(2)))) + m.group(3),
            text,
            flags=re.M,
        )
        if new != text:
            _write(path, new)


def _enabled(path: Path) -> List[str]:
    lines = (_read(path) or "").splitlines()
    return [n for n in (line.split("#", 1)[0].strip() for line in lines) if n]


def _restart(state: Path, out: Callable[[str], None]) -> None:
    if _allow_lists(state):
        out(f"Restart the server to apply it: {RESTART}")
    else:
        out(
            f"No configuration generated in {state.parent} yet: ./install.sh "
            "will include it"
        )


def _enable(name: str, state: Path, out: Callable[[str], None]) -> None:
    installed = set().union(*installed_distributions().values())
    if name not in installed:
        raise HubError(
            f"{name!r} is not installed (installed modules: "
            f"{', '.join(sorted(installed)) or 'none'}): huri hub install {name}"
        )
    path = state / ENABLED_FILE
    listed = name in _enabled(path)
    lists = _allow_lists(state)
    # ./install.sh serves `tts` under its engine's name (tts_cosyvoice).
    served = all(
        name in names or (name == "tts" and "tts_cosyvoice" in names) for names in lists
    )
    if not listed and lists and served:
        # Recorded, a later `disable` would take it from the installer's plan.
        out(f"{name} is already enabled by the install plan")
    elif listed and served:
        out(f"{name} is already enabled")
    else:
        if not listed:
            text = _read(path) or ""
            sep = "\n" if text and not text.endswith("\n") else ""
            _write(path, f"{text}{sep}{name}\n")
        _edit_allow_lists(
            state, lambda names: names if name in names else [*names, name]
        )
        out(f"Enabled {name}.")
        _restart(state, out)
    out(f"To use it in a session, add it {_client_config(name)}")


def cmd_enable(args: Any, fetch: Fetch, out: Callable[[str], None]) -> int:
    name = _enableable(args.module)
    _enable(name, state_dir(), out)
    return 0


def cmd_disable(args: Any, fetch: Fetch, out: Callable[[str], None]) -> int:
    # Only what `enable` recorded: a module of the install plan is the
    # installer's, and ./install.sh would bring it back anyway.
    name = _module_name(args.module)
    state = state_dir()
    path = state / ENABLED_FILE
    if name not in _enabled(path):
        if any(name in names for names in _allow_lists(state)):
            raise HubError(
                f"{name!r} was not enabled by huri hub but by the install plan: "
                "re-run ./install.sh --modules <the modules to keep> to leave it out"
            )
        raise HubError(f"{name!r} is not enabled")
    lines = (_read(path) or "").splitlines(keepends=True)
    _write(
        path, "".join(line for line in lines if line.split("#", 1)[0].strip() != name)
    )
    _edit_allow_lists(state, lambda names: [n for n in names if n != name])
    out(f"Disabled {name}.")
    _restart(state, out)
    return 0


def _readme(module: HubModule, registry: Registry) -> Optional[str]:
    """Where the README of a curated module's package is: in the checkout the
    registry was read from, else on GitHub at the registry's ref."""
    path = module.curated_path
    if path is None:
        return None
    if registry.checkout is not None and (registry.checkout / path).is_dir():
        return str(registry.checkout / path / "README.md")
    return f"https://github.com/{HURI_MODULES_REPO}/tree/{registry.ref}/{path}#readme"


def _prog(argv: Optional[List[str]]) -> str:
    """The command's name in its usage and errors: `huri hub` when huri-core's
    `huri` runs it (it passes the arguments), else how it was started."""
    if argv is None and Path(sys.argv[0]).name.startswith("huri-hub"):
        return "huri-hub"
    return "huri hub"


def main(
    argv: Optional[List[str]] = None,
    fetch: Fetch = http_fetch,
    prog: Optional[str] = None,
) -> int:
    """Find and install HuRI modules: the verified registry and the community."""
    prog = prog or _prog(argv)
    parser = argparse.ArgumentParser(
        prog=prog, description=(__doc__ or "").split("\n\n")[0]
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

    p = sub.add_parser("install", help="pip-install a module")
    p.add_argument("module", help="a module name, or a pip target (git+https://...)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--enable", action="store_true", help="then enable it (see enable)")
    p.add_argument(
        "--editable",
        action="store_true",
        help="install a local checkout in place (pip install -e), for development",
    )
    p.add_argument(
        "--community",
        action="store_true",
        help="allow a module that is not in the verified registry",
    )
    p.add_argument(
        "--registry", help="registry.toml path or URL, or a huri-modules checkout"
    )
    p.set_defaults(run=cmd_install)

    for command, run, help_ in (
        ("enable", cmd_enable, "add an installed module to the server's HURI_MODULES"),
        ("disable", cmd_disable, "remove a module `enable` added"),
    ):
        p = sub.add_parser(command, help=help_)
        p.add_argument("module", help="a module name")
        p.set_defaults(run=run)

    args = parser.parse_args(argv)
    try:
        code: int = args.run(args, fetch, print)
        return code
    except HubError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 1
