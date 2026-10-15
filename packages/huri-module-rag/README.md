# huri-module-rag

Retrieval-augmented answers with long-term memory (Qdrant + an OpenAI-compatible LLM), and the question aggregator.

| Module | Topics | What it does |
|---|---|---|
| `rag` | `huri.question` → `huri.token` | Answers questions from memory with an LLM, streaming tokens |
| `qag` | `huri.partial_question` → `huri.question` | Joins a transcript and its emotion into a question |

```sh
pip install -e packages/huri-module-rag     # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=rag,qag ...   # enable it on the server
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
  "huri-module-rag @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-rag"
```
