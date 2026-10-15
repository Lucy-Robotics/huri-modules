# huri-module-tts

HuRI's text-to-speech modules: they speak the assistant's reply. One package,
two engines, each a module of its own with its own dependencies (an extra), so
a host installs only the engine it runs:

| Module | Engine | Extra | Topics | Deployment |
|---|---|---|---|---|
| `tts` | [Piper](https://github.com/OHF-Voice/piper1-gpl): ONNX only, no torch, no GPU, ~30x faster than realtime on a laptop CPU. The default: it runs on any machine. | `piper` | `huri.token` → `huri.audio.out` | `TTSHandle` |
| `tts_cosyvoice` | CosyVoice3: clones the voice of a reference sample. Needs an NVIDIA GPU for real time (~7x slower than realtime on CPU; its vocoder faults on consumer RDNA3 GPUs). | `cosyvoice` | `huri.token` → `huri.audio.out` | `CosyVoiceTTSHandle` |

Both are built on `huri_tts.streaming.StreamingTTS`, the engine-agnostic half:
it opens one synthesis session per utterance, pushes each `huri.token` into it
in order and streams `huri.audio.out` back with presentation timestamps.

The base install has no engine library; `[tool.huri-modules.extras]` in
`pyproject.toml` names the extra each module needs (`huri hub install` and
`scripts/install_local.sh` add it). CosyVoice's hardware pins are
`requirements-<variant>.tts_cosyvoice.txt`: only a host that plans
`tts_cosyvoice` gets them, so they never meet Piper's `onnxruntime`.

Upgrading from the three packages this one replaced (huri-tts,
huri-module-tts-piper, huri-module-tts-cosyvoice): `pip uninstall huri-tts
huri-module-tts-piper huri-module-tts-cosyvoice` first (`./install.sh` does
it). Left installed, they claim the module names `tts` and `tts_cosyvoice` too,
and huri-core then loads no module at all.

Piper (`tts`):

```sh
pip install -e 'packages/huri-module-tts[piper]'
python -m piper.download_voices --download-dir assets/models/piper en_US-lessac-medium
HURI_PIPER_VOICE=assets/models/piper/en_US-lessac-medium.onnx HURI_MODULES=...,tts ...
```

CosyVoice3 (`tts_cosyvoice`):

```sh
pip install -c constraints.txt --extra-index-url https://download.pytorch.org/whl/cu121 \
  -r packages/huri-module-tts/requirements-nvidia.tts_cosyvoice.txt
pip install -e 'packages/huri-module-tts[cosyvoice]'
packages/huri-module-tts/install_cosyvoice.sh /opt/cosyvoice   # not pip-installable
export PYTHONPATH=/opt/cosyvoice:/opt/cosyvoice/third_party/Matcha-TTS
HURI_MODULES=...,tts_cosyvoice ...   # instead of tts; see the env vars in huri_tts/cosyvoice/handle.py
```

`huri hub install tts_cosyvoice` (or `pip install
'huri-module-tts[cosyvoice]'`) does only the second line: torch and torchaudio,
unpinned. The pins, the CosyVoice checkout and `PYTHONPATH` stay the steps
above (`./install.sh --tts-engine cosyvoice` does all of them).

Use `tts_cosyvoice` in place of `tts` in the server's `HURI_MODULES` and the
client's `modules:`, and name its Serve deployment `CosyVoiceTTSHandle`.

A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). It depends on `huri-core` and
`huri-events`, which are not on PyPI: without a checkout, install them from git
in the same pip command, so pip never looks them up on an index (`[cosyvoice]`
for the other engine):

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # HURI_CORE_REF in that release's versions.env
pip install \
  "huri-core[server] @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events" \
  "huri-module-tts[piper] @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-tts"
```
