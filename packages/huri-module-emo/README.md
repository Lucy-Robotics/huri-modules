# huri-module-emo

Speech emotion from prosody (wav2vec2 classifier), and its aggregator.

| Module | Topics | What it does |
|---|---|---|
| `emo` | `huri.voice` → `huri.emotion` | Estimates the emotion of speech from its prosody |
| `eag` | `huri.emotion` → `huri.partial_question` | Aggregates emotion estimates over an utterance |

```sh
pip install -e packages/huri-module-emo     # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=emo,eag ...   # enable it on the server
```

Hardware-specific pins: `requirements-nvidia.txt`, `requirements-amd.txt` (install them before the package).

A self-contained distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). Without a checkout:

```sh
pip install "huri-module-emo @ git+https://github.com/Lucy-Robotics/huri-modules.git@<ref>#subdirectory=packages/huri-module-emo"
```
