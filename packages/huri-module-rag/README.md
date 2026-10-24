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

`rag` remembers with three stores, after the CoALA framework (Sumers, Yao,
Narasimhan & Griffiths, [*Cognitive Architectures for Language
Agents*](https://arxiv.org/abs/2309.02427), TMLR 2024), itself after the
human memory models of Atkinson & Shiffrin (1968) and Baddeley & Hitch
(1974):

| Memory | Where | Holds |
|---|---|---|
| short-term | the module, per session | the last `max_history_turns` exchanges, verbatim |
| working | the module, per session | a summary of the rest of the session and at most `working_memory_capacity` notes (names, facts, goals, promises) |
| long-term | Qdrant | episodic: one summary per past session (`memory_collection`); semantic: profile facts and documents |

An exchange pushed out of short-term memory is folded into working memory by
the LLM, after the answer that pushed it out has been sent. At the end of the
session, both go into one long-term memory point. Points are recalled by
relevance, recency and importance and fade and merge over time, as in
Generative Agents (Park et al., [arXiv:2304.03442](https://arxiv.org/abs/2304.03442)).

Memories get updated, not only added: when the session's memory says
something changed ("we renamed Pixel Felix"), the user's similar older
memories are corrected (Pixel → Felix), after Mem0 (Chhikara et al.,
[arXiv:2504.19413](https://arxiv.org/abs/2504.19413)). The LLM only names the
change; it is applied when both names are in the new memory, so an unsure LLM
leaves memories as they are rather than garbling them (`memory_reconcile`,
`memory_reconcile_top_k`, `memory_reconcile_threshold` in the deployment
config). Nothing is lost: a corrected memory keeps its earlier texts in its
`history` payload, to audit or undo a correction, as Zep
([arXiv:2501.13956](https://arxiv.org/abs/2501.13956)) invalidates facts
rather than deleting them. Recalled memories reach the LLM dated and oldest
first ("3 days ago: …", "2 weeks ago (corrected yesterday): …"), told that the
more recent one is right, which also covers changes no word swap can apply
("I don't like jazz anymore"). All memory bookkeeping asks the LLM for JSON in
its JSON mode.

A session picks its memories with the `memory` argument, all three by
default:

```yaml
args:
  memory: short,working,long   # or a list; any of them, or none (stateless)
  max_history_turns: 6         # size of short-term memory
```

With `working` but no `short`, each exchange goes straight into working
memory. With `long` alone, the session's last `max_history_turns` exchanges
are still saved at the end, but never sent to the LLM during it. Without
`long`, past sessions and profile facts are not recalled and the session is not
saved.

A session answers one question at a time: one asked during an answer waits
for it, then has it in its history. Each robot's connection is its own
session, so robots never wait for each other, and one `RAGHandle` serves them
all (`max_ongoing_requests` 64; the LLM server is the real limit). Long-term
memory is per `user_id`, which is per machine: two sessions of one machine
ending at once save one after the other, so neither loses the other's
corrections.

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
