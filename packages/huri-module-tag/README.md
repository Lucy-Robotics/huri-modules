# huri-module-tag

Text aggregator: stitches overlapping transcripts into one question.

| Module | Topics | What it does |
|---|---|---|
| `tag` | `huri.transcript` → `huri.partial_question` | Stitches overlapping transcript passes into one sentence |

```sh
pip install -e packages/huri-module-tag     # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=tag ...   # enable it on the server
```

A self-contained distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). Without a checkout:

```sh
pip install "huri-module-tag @ git+https://github.com/Lucy-Robotics/huri-modules.git@<ref>#subdirectory=packages/huri-module-tag"
```
