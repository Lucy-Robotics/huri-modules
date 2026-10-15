# huri-events

The curated HuRI event vocabulary: one event class per `huri.*` topic, with its
wire format, registered through the `huri.events` entry point group. The
`huri.` namespace is reserved for this package; third-party events use their
own (`acme.*`), see [Modules as plugins](../../README.md#modules-as-plugins).

It is deliberately light (huri-core and numpy): a client installs it next to
`huri-core[client]` to decode every curated topic.

```sh
# from a huri-modules checkout:
pip install "huri-core[client] @ git+https://github.com/Lucy-Robotics/huri-core.git@$(cat HURI_CORE_REF)" \
  ./packages/huri-events
```

A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). It depends on `huri-core`, which is
not on PyPI: without a checkout, install it from git in the same pip command,
so pip never looks it up on an index:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # the huri-core ref in that release's HURI_CORE_REF file
pip install \
  "huri-core @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events"
```
