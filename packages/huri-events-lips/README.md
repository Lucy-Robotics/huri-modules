# huri-events-lips

The event of the lips module: `lips.face`, the ARKit blend shape weights of
the face in step with the voice (`FaceWeights`, 52 floats per frame at 30 fps,
timed on the `huri.audio.out` it follows), registered through the
`huri.events` entry point group. huri-module-lips-sync produces it.

```sh
# from a huri-modules checkout:
pip install "huri-core[client] @ git+https://github.com/Lucy-Robotics/huri-core.git@$(. ./versions.env && echo "$HURI_CORE_REF")" \
  ./packages/huri-events-lips
```

A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). It depends on `huri-core`, which is
not on PyPI: without a checkout, install it from git in the same pip command,
so pip never looks it up on an index:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # HURI_CORE_REF in that release's versions.env
pip install \
  "huri-core @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events-lips @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events-lips"
```
