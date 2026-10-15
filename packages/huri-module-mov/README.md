# huri-module-mov

Co-speech gestures (EMAGE): speech audio in, SMPL-X motion out.

| Module | Topics | What it does |
|---|---|---|
| `mov` | `huri.audio.out` → `huri.motion` | Generates SMPL-X gestures matching the speech (EMAGE) |

```sh
pip install -e packages/huri-module-mov     # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=mov ...   # enable it on the server
```

Hardware-specific pins: `requirements-nvidia.txt`, `requirements-amd.txt` (install them before the package).

A self-contained distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). Without a checkout:

```sh
pip install "huri-module-mov @ git+https://github.com/Lucy-Robotics/huri-modules.git@<ref>#subdirectory=packages/huri-module-mov"
```
