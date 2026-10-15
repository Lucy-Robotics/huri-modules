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
core=0.4.0  # HURI_CORE_REF in that release's versions.env
pip install \
  "huri-core @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-hub @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-hub"
```

The registry is read from `--registry PATH|URL|CHECKOUT`, else `HURI_REGISTRY`,
else the huri-modules checkout the current directory is in, else GitHub at the
huri-modules ref `HURI_MODULES_REF` (a release tag; `main` when unset). Curated
modules are installed from huri-modules at that same ref, with the extra of
their package they need (registry `extra`: `huri hub install tts_cosyvoice`
installs `huri-module-tts[cosyvoice]`) and the curated packages they depend on
(huri-events) in the same pip command; verified third-party modules at their
reviewed tag. Nothing found is ever
loaded: enabling a module stays the server operator's `HURI_MODULES` decision.

pip installs the package and its extra, not what a module may need besides:
model weights, exact hardware pins (`requirements-<variant>[.<module>].txt`) or
a setup script. `huri hub install tts_cosyvoice`, for one, brings torch and
torchaudio unpinned, but not CosyVoice's pins, its checkout
(`install_cosyvoice.sh`) nor its `PYTHONPATH`; after a curated module, the
command prints where its package's README says what is left.

It refuses a module when another installed distribution registers the same
module name, or is one its package replaced (huri-module-tts replaced
huri-tts, huri-module-tts-piper and huri-module-tts-cosyvoice): huri-core would
then load no module at all. It names them, to remove with
`pip uninstall huri-tts huri-module-tts-piper huri-module-tts-cosyvoice` first.
`GITHUB_TOKEN` raises GitHub's rate limit.

`huri_hub.registry` is the one reader of the registry schema: huri-modules'
`check_registry.py` checks the real file with it. A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml`; a change of the registry schema is a huri-hub version bump in
the same release (see RELEASING.md there). huri-hub 0.2 reads registry
schema 2 (which added `extra`); 0.1 reads schema 1 only.
