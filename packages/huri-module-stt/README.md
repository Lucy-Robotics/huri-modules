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

A self-contained distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). Without a checkout:

```sh
pip install "huri-module-stt @ git+https://github.com/Lucy-Robotics/huri-modules.git@<ref>#subdirectory=packages/huri-module-stt"
```
