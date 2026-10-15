# huri-module-vad

Voice activity detection (WebRTC VAD): microphone frames in, speech segments out.

| Module | Topics | What it does |
|---|---|---|
| `vad` | `huri.audio.in` → `huri.voice` | Detects speech in microphone frames (WebRTC VAD) |

```sh
pip install -e packages/huri-module-vad     # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=vad ...   # enable it on the server
```

A self-contained distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). Without a checkout:

```sh
pip install "huri-module-vad @ git+https://github.com/Lucy-Robotics/huri-modules.git@<ref>#subdirectory=packages/huri-module-vad"
```
