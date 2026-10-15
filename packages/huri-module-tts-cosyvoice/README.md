# huri-module-tts-cosyvoice

HuRI module `tts_cosyvoice`: speaks the assistant's reply with CosyVoice3,
cloning the voice of a reference sample. Needs an NVIDIA GPU for real time
(it is ~7x slower than realtime on CPU, and its vocoder faults on consumer
RDNA3 GPUs).

| Module | Topics | Deployment |
|---|---|---|
| `tts_cosyvoice` | `huri.token` → `huri.audio.out` | `CosyVoiceTTSHandle` |

```sh
pip install -c constraints.txt --extra-index-url https://download.pytorch.org/whl/cu121 \
  -r packages/huri-module-tts-cosyvoice/requirements-nvidia.txt
pip install -e packages/huri-tts -e packages/huri-module-tts-cosyvoice
packages/huri-module-tts-cosyvoice/install_cosyvoice.sh /opt/cosyvoice   # not pip-installable
export PYTHONPATH=/opt/cosyvoice:/opt/cosyvoice/third_party/Matcha-TTS
HURI_MODULES=...,tts_cosyvoice ...   # instead of tts; see the env vars in handle.py
```

Use it in place of `tts` in the server's `HURI_MODULES` and the client's
`modules:`, and name its Serve deployment `CosyVoiceTTSHandle`.

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
  "huri-module-tts-cosyvoice @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-tts-cosyvoice"
```
