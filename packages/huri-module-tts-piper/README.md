# huri-module-tts-piper

HuRI module `tts`: speaks the assistant's reply with [Piper](https://github.com/OHF-Voice/piper1-gpl)
— ONNX only, no torch, no GPU, ~30x faster than realtime on a laptop CPU. The
default TTS: it runs on any machine.

| Module | Topics | Deployment |
|---|---|---|
| `tts` | `huri.token` → `huri.audio.out` | `TTSHandle` |

```sh
pip install -e packages/huri-tts -e packages/huri-module-tts-piper
python -m piper.download_voices --download-dir assets/models/piper en_US-lessac-medium
HURI_PIPER_VOICE=assets/models/piper/en_US-lessac-medium.onnx HURI_MODULES=...,tts ...
```

A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). It depends on `huri-core`,
`huri-events` and `huri-tts`, which are not on PyPI: without a checkout,
install them from git in the same pip command, so pip never looks them up on an
index:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # the huri-core ref in that release's HURI_CORE_REF file
pip install \
  "huri-core[server] @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events" \
  "huri-tts @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-tts" \
  "huri-module-tts-piper @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-tts-piper"
```
