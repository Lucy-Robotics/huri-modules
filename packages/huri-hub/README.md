# huri-hub

`huri hub`: find and install HuRI modules, from the verified registry of
huri-modules ([`registry.toml`](../../registry.toml)) and from the community
(GitHub repositories tagged `huri-module`).

```sh
huri hub search gesture             # GitHub repositories tagged `huri-module`
huri hub list                       # modules supporting the installed huri-core
huri hub list --verified            # only the registry
huri hub list --core 0.5.0 --all    # another version; --all keeps unsupported ones
huri hub install stt                # pip-installs a verified module; never enables it
huri hub install servo --community  # an unreviewed one: only when asked explicitly
```

`huri` is huri-core's command: it runs the commands installed packages register
in the `huri.commands` entry point group, and this package registers `hub`.
`huri-hub ...` and `python -m huri_hub ...` are the same command.

It is light (huri-core's base install, `packaging`, and `tomli` before Python
3.11): no Ray, no torch. In a huri-modules checkout `./install.sh` installs it;
otherwise, from git, with huri-core on the same command line (neither is on
PyPI, so pip never looks them up on an index):

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # the huri-core ref in that release's HURI_CORE_REF file
pip install \
  "huri-core @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-hub @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-hub"
```

The registry is read from `--registry PATH|URL|CHECKOUT`, else `HURI_REGISTRY`,
else the huri-modules checkout the current directory is in, else GitHub at the
huri-modules ref `HURI_MODULES_REF` (a release tag; `main` when unset). Curated
modules are installed from huri-modules at that same ref, with the curated
packages they depend on (huri-events, huri-tts) in the same pip command;
verified third-party modules at their reviewed tag. Nothing found is ever
loaded: enabling a module stays the server operator's `HURI_MODULES` decision.
`GITHUB_TOKEN` raises GitHub's rate limit.

`huri_hub.registry` is the one reader of the registry schema: huri-modules'
`check_registry.py` checks the real file with it. A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml`; a change of the registry schema is a huri-hub version bump in
the same release (see RELEASING.md there).
