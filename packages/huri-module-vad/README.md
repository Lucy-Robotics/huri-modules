# huri-module-vad

Voice activity detection (WebRTC VAD): microphone frames in, speech segments out.

| Module | Topics | What it does |
|---|---|---|
| `vad` | `huri.audio.in` → `huri.voice` | Detects speech in microphone frames (WebRTC VAD) |

```sh
pip install -e packages/huri-module-vad     # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=vad ...   # enable it on the server
```

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
  "huri-module-vad @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-vad"
```
