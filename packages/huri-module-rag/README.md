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

A self-contained distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). Without a checkout:

```sh
pip install "huri-module-rag @ git+https://github.com/Lucy-Robotics/huri-modules.git@<ref>#subdirectory=packages/huri-module-rag"
```
