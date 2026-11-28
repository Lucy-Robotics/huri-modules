# huri-hub

`huri hub`: find and install HuRI modules, from the verified registry of
huri-modules ([`registry.toml`](../../registry.toml)) and from the community
(GitHub repositories tagged `huri-module`).

```sh
huri hub search gesture             # GitHub repositories tagged `huri-module`
huri hub list                       # modules supporting the installed huri-core
huri hub list --verified            # only the registry
huri hub list --core 0.5.0 --all    # another version; --all keeps unsupported ones
huri hub install stt                # pip-installs a verified module, not enabled
huri hub install servo --community  # an unreviewed one: only when asked explicitly
huri hub enable think               # adds an installed module to the server's HURI_MODULES
huri hub install think --enable     # both
huri hub disable think              # removes a module `enable` added
huri hub install ./servo --editable --enable  # a module under development, in place
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

`huri hub enable <name>` is that decision, for a module installed in the
server's environment. It appends the name to `.huri-local/modules.enabled`
(one name per line; blank lines and `#` comments ignored), which
`./install.sh` reads on every run, and adds it to `HURI_MODULES` in the
configuration `./install.sh` generated (`config/huri_local.generated.yaml`,
`.huri-local/huri.env`); then restart the server:
`.huri-local/stop.sh && .huri-local/start.sh`. In a checkout not installed yet,
it only writes `.huri-local/modules.enabled`, for `./install.sh` to include.
The install is `HURI_STATE` (which `.huri-local/env.sh` exports), else the
checkout whose `.venv` runs the command. `huri hub disable <name>` undoes
`enable`; a module of the install plan is left out by re-running
`./install.sh --modules ...` instead (`enable` records none, nor
`tts_cosyvoice`: that is `tts` with `./install.sh --tts-engine cosyvoice`).
`HURI_MODULES` in `.huri-local/plan.env` is the installer's record of its plan,
informational: `enable` and `disable` leave it as it is. A session uses a
module only when its client config names it: `enable` prints the snippet to add,
and the next `./install.sh` adds it to `config/client_local.generated.yaml`.

`install <dir> --editable` is for a module you write (one made from
huri-plugin-template, named after its author: `servo`, not `huri-servo`). It
runs `pip install -e <dir>` (and `-e <dir>/events` when that has a
`pyproject.toml`), with a constraint file holding huri-core and huri-events at
the installed versions, so pip never replaces them. With `--enable`, every
`huri.modules` entry point of `<dir>/pyproject.toml` is enabled once pip
succeeds. The template's `requirements-<variant>.txt` pins are not installed.

pip installs the package and its extra, not what a module may need besides:
model weights, exact hardware pins (`requirements-<variant>[.<module>].txt`) or
a setup script. `huri hub install tts_cosyvoice`, for one, brings torch and
torchaudio unpinned, but not CosyVoice's pins, its checkout
(`install_cosyvoice.sh`) nor its `HURI_COSY_DIR`; after a curated module, the
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
