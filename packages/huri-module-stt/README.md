# huri-module-stt

Speech to text (faster-whisper) as a Ray Serve deployment.

| Module | Topics | What it does |
|---|---|---|
| `stt` | `huri.voice` → `huri.transcript` | Transcribes speech with faster-whisper (CPU or GPU) |

```sh
pip install -e packages/huri-module-stt     # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=stt ...   # enable it on the server
```

Hardware-specific pins: `requirements-amd.txt`, `requirements-nvidia.txt` (install them before the package).

A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). It depends on `huri-core` and
`huri-events`, which are not on PyPI: without a checkout, install them from git
in the same pip command, so pip never looks them up on an index:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # HURI_CORE_REF in that release's versions.env
pip install \
  "huri-core[server] @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events" \
  "huri-module-stt @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-stt"
```
