# huri-module-tag

Text aggregator: stitches overlapping transcripts into one question.

| Module | Topics | What it does |
|---|---|---|
| `tag` | `huri.transcript` → `huri.partial_question` | Stitches overlapping transcript passes into one sentence |

```sh
pip install -e packages/huri-module-tag     # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=tag ...   # enable it on the server
```

A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). It depends on `huri-core` and
`huri-events`, which are not on PyPI: without a checkout, install them from git
in the same pip command, so pip never looks them up on an index:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # HURI_CORE_REF in that release's versions.env
pip install \
  "huri-core[server] @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events" \
  "huri-module-tag @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-tag"
```
