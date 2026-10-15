# huri-module-emo

Speech emotion from prosody (wav2vec2 classifier), and its aggregator.

| Module | Topics | What it does |
|---|---|---|
| `emo` | `huri.voice` → `huri.emotion` | Estimates the emotion of speech from its prosody |
| `eag` | `huri.emotion` → `huri.partial_question` | Aggregates emotion estimates over an utterance |

```sh
pip install -e 'packages/huri-module-emo[model]'   # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=emo,eag ...   # enable it on the server
```

`emo` needs the `model` extra (torch and transformers, for its wav2vec2
classifier); `eag` only aggregates events and needs none of it, so a host that
runs `eag` alone installs the package without the extra
(`[tool.huri-modules.extras]` in `pyproject.toml` says which module needs which
extra; `huri hub install` and `scripts/install_local.sh` add it). Hardware pins
of `emo`: `requirements-<variant>.emo.txt` (`cpu`, `nvidia`, `amd`; install them
before the package).

A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). It depends on `huri-core` and
`huri-events`, which are not on PyPI: without a checkout, install them from git
in the same pip command, so pip never looks them up on an index:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # the huri-core ref in that release's HURI_CORE_REF file
pip install \
  "huri-core[server] @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events" \
  "huri-module-emo[model] @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-emo"
```
