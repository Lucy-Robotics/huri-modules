# huri-module-rag

Retrieval-augmented answers with long-term memory (Qdrant + an OpenAI-compatible LLM), and the question aggregator.

| Module | Topics | What it does |
|---|---|---|
| `rag` | `huri.question` → `huri.token` | Answers questions from memory with an LLM, streaming tokens |
| `qag` | `huri.partial_question` → `huri.question` | Joins a transcript and its emotion into a question |

```sh
pip install -e 'packages/huri-module-rag[rag]'   # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=rag,qag ...   # enable it on the server
```

`qag` sends the question on as soon as TAG's sentence is in (`mode: parallel`,
the default): `rag` answers while `emo` is still reading the turn, and an
emotion that lands afterwards goes with the next question, flagged
`emotion_earlier` so the LLM reads it as the previous message's. With
`mode: sequential` it holds the question until its emotion is in, so the
answer always knows how this very sentence sounded, at the cost of waiting for
`emo`. Without `emo`/`eag` in the session, set `use_emotion: false` or the
question is held for an emotion that never comes. `rag` tells the LLM how the
user sounded, from the emotion on the question.

`rag` and the ingestion and memory tools (`python -m huri_rag.ingestion`,
`memory_inspect`, `memory_maintenance`) need the `rag` extra (Qdrant client,
sentence-transformers, httpx, pypdf, semantic_chunker); `qag` only joins events
and needs none of it, so a host that runs `qag` alone installs the package
without the extra (`[tool.huri-modules.extras]` in `pyproject.toml`; `huri hub
install` and `scripts/install_local.sh` add it). Hardware pins of `rag`:
`requirements-amd.rag.txt` (install it before the package).

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
  "huri-module-rag[rag] @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-rag"
```
