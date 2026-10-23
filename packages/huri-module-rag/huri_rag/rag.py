import asyncio
import json
import os
import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator, Sequence

import httpx
from huri_events.question import RAGQuestion
from huri_events.text import Token
from pydantic import BaseModel
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
)
from ray import serve
from ray.serve import handle

from huri_core.core.module import ModuleWithHandle, ModuleWithId

from .memory_maintenance import (
    MARKER_TYPE,
    consolidate_and_prune,
    memory_point,
    plan,
    recency,
    scroll_all,
)
from .qdrant_utils import make_qdrant_client

_RETRIABLE_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)

SHARED_USER_ID = "__shared__"

_DEFAULT_PERSONA = os.environ.get(
    "HURI_RAG_DEFAULT_PERSONA",
    "You are Mouse-Man, a witty, charismatic animated mouse character. "
    "You are NOT an AI, an assistant, or a language model, and you must "
    "never say that you are one. Stay fully in character at all times, "
    "speak naturally and with personality, and never break the illusion.",
)

# Labels of EMO's classifier (huri-module-emo), as the LLM should read them.
_EMOTION_NAMES = {"neu": "neutral", "hap": "happy", "ang": "angry", "sad": "sad"}


def _ago(iso: str | None, now: datetime | None = None) -> str:
    """How long ago an ISO date is, as the LLM should read it."""
    try:
        days = ((now or datetime.now()) - datetime.fromisoformat(iso or "")).days
    except ValueError:
        return "some time ago"
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


# The memories a session can use (see WorkingMemory), all on by default.
MEMORIES = ("short", "working", "long")


class RAGDeploymentConfig(BaseModel):
    qdrant_url: str = "http://localhost:6333"
    default_collection: str = "documents"
    embedding_model: str = "BAAI/bge-large-en-v1.5"
    embedding_url: str = ""
    llm_provider: str = "ollama"  # "vllm", "ollama", "api"
    llm_url: str = "http://localhost:11434"
    llm_model: str = "mistral:7b"
    llm_api_key: str = os.environ.get("HURI_LLM_API_KEY", "")
    verify_ssl: bool = True
    top_k: int = 5
    score_threshold: float = 0.5

    embed_retries: int = 3
    llm_retries: int = 3
    retry_base_delay: float = 1.0

    memory_collection: str = "conversations"
    memory_top_k: int = 3
    memory_half_life_days: float = 5.0
    memory_w_relevance: float = 0.5
    memory_w_recency: float = 0.3
    memory_w_importance: float = 0.2
    memory_maintenance_days: float = 5.0
    memory_maintenance_check_hours: float = 6.0
    # Saving a memory corrects the user's similar older ones it updates.
    memory_reconcile: bool = True
    memory_reconcile_top_k: int = 5
    memory_reconcile_threshold: float = 0.5

    working_memory_capacity: int = 7


@dataclass
class WorkingMemory:

    summary: str = ""
    notes: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.summary or self.notes)


@dataclass
class RAGQuery:
    """What flows from RAG module to RAGHandle."""

    _user_id: str
    question: str
    preferences: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    # How the user sounded, from EMO: {"label", "confidence"}. ``emotion_earlier``
    # when it is from their previous message (QAG's parallel mode, where the
    # emotion lands after its question was answered and goes with the next).
    emotion: dict | None = None
    emotion_earlier: bool = False
    working: WorkingMemory = field(default_factory=WorkingMemory)
    long_term: bool = True


@serve.deployment(name="RAGHandle")
class RAGHandle:
    """Stateless RAG processor. Streams LLM tokens to the caller."""

    _MAINTENANCE_MARKER_ID = "00000000-0000-0000-0000-00000000feed"

    def __init__(self, **kwargs):
        self._maintenance_task: asyncio.Task | None = None
        self._cfg = RAGDeploymentConfig(**kwargs)
        self._apply_config()

    def reconfigure(self, config: dict) -> None:
        self._cfg = RAGDeploymentConfig(**{**self._cfg.model_dump(), **config})
        self._apply_config()

    def _apply_config(self) -> None:
        cfg = self._cfg
        self.embedding_url = cfg.embedding_url or cfg.llm_url
        self._qdrant = make_qdrant_client(cfg.qdrant_url, cfg.verify_ssl)
        print(f"[RAGHandle] Connected to Qdrant at {cfg.qdrant_url}")
        self._warned_collections: set[str] = set()
        self._report_document_collection()
        self._embed_client = httpx.AsyncClient(timeout=30.0, verify=cfg.verify_ssl)
        self._llm_client = httpx.AsyncClient(timeout=120.0, verify=cfg.verify_ssl)
        task = self._maintenance_task
        if task is None or task.done():
            self._maintenance_task = asyncio.get_event_loop().create_task(
                self._maintenance_loop()
            )

    def _report_document_collection(self) -> None:
        """
        Say at startup whether the document collection exists.
        """

        collection = self._cfg.default_collection
        try:
            names = [c.name for c in self._qdrant.get_collections().collections]
        except Exception as e:  # noqa: BLE001 - never block startup on this
            print(f"[RAGHandle] WARNING: cannot list Qdrant collections: {e}")
            print("[RAGHandle]          document retrieval will return nothing")
            return

        if collection in names:
            try:
                count = self._qdrant.count(collection_name=collection).count
                print(f"[RAGHandle] documents: '{collection}' has {count} points")
                if count == 0:
                    print(
                        "[RAGHandle] WARNING: it is empty — answers will not be "
                        "grounded in any document"
                    )
            except Exception:  # noqa: BLE001
                print(f"[RAGHandle] documents: '{collection}' present")
        else:
            print(
                f"[RAGHandle] WARNING: no '{collection}' collection in Qdrant "
                f"(found: {', '.join(names) or 'none'})"
            )
            print("[RAGHandle]          document retrieval returns nothing.")
            print("[RAGHandle]          Ingest: python -m huri_rag.ingestion")

    def _resolve_user_context(self, _user_id: str) -> tuple[str, dict | None]:
        collection = self._cfg.default_collection
        filters = {"_user_id": _user_id}
        return collection, filters

    async def _retry_on_connect_failure(self, func, *, attempts: int, what: str):
        for attempt in range(1, attempts + 1):
            try:
                return await func()
            except _RETRIABLE_EXC as e:
                if attempt == attempts:
                    raise
                delay = self._cfg.retry_base_delay * (2 ** (attempt - 1))
                print(
                    f"[RAG] {what} failed (attempt {attempt}/{attempts}): "
                    f"{e!r} — retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

    async def _embed(self, text: str) -> list[float]:
        url = f"{self.embedding_url}/v1/embeddings"
        resp = await self._retry_on_connect_failure(
            lambda: self._embed_client.post(
                url,
                json={"model": self._cfg.embedding_model, "input": str(text)},
            ),
            attempts=self._cfg.embed_retries,
            what=f"Embedding request to {url}",
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Embedding HTTP {resp.status_code} from {url}: {resp.text[:1000]}"
            )
        try:
            payload = resp.json()
        except Exception as e:
            raise RuntimeError(
                f"Embedding non-JSON response from {url}: {resp.text[:1000]}"
            ) from e
        try:
            embedding: list[float] = payload["data"][0]["embedding"]
            return embedding
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"Embedding unexpected schema from {url}: {str(payload)[:1000]}"
            ) from e

    def _get_profile(self, collection: str, _user_id: str) -> list[str]:
        """
        Always-on facts about the user (name, etc.).
        """
        try:
            points, _ = self._qdrant.scroll(
                collection_name=collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="_user_id", match=MatchValue(value=_user_id)
                        ),
                        FieldCondition(key="type", match=MatchValue(value="profile")),
                    ]
                ),
                limit=50,
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            return []
        return [t for p in points if (t := (p.payload or {}).get("text", ""))]

    def _ensure_memory_collection(self, vector_size: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        names = [c.name for c in self._qdrant.get_collections().collections]
        if self._cfg.memory_collection not in names:
            self._qdrant.create_collection(
                collection_name=self._cfg.memory_collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    async def _llm_complete(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 300
    ) -> str:
        """Non-streamed convenience wrapper over _llm_stream."""
        parts = []
        async for d in self._llm_stream(
            system_prompt, user_prompt, {"max_length": max_tokens}, None
        ):
            parts.append(d)
        return "".join(parts)

    async def _llm_json(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 500
    ) -> Any:
        """The LLM's reply as JSON, in the provider's JSON mode, for memory
        bookkeeping (low temperature)."""
        preferences = {"max_length": max_tokens, "temperature": 0.2, "json": True}
        parts = [
            d
            async for d in self._llm_stream(
                system_prompt, user_prompt, preferences, None
            )
        ]
        raw = "".join(parts)
        try:
            return self._parse_json(raw)
        except ValueError:
            print(f"[RAG] LLM reply is not JSON: {raw[:300]!r}")
            raise

    def _memory_strength(self, payload: dict, relevance: float) -> float:
        cfg = self._cfg
        importance: int = payload.get("importance", 3)
        return (
            cfg.memory_w_relevance * relevance
            + cfg.memory_w_recency * recency(payload, cfg.memory_half_life_days)
            + cfg.memory_w_importance * (importance / 10.0)
        )

    def _search_memories(self, query_vector: list[float], _user_id: str) -> list[str]:
        """Retrieve, re-rank (relevance+recency+importance), reinforce, return
        texts dated and oldest first, for the LLM to prefer the latest."""
        try:
            hits = self._qdrant.query_points(
                collection_name=self._cfg.memory_collection,
                query=query_vector,
                query_filter=Filter(
                    must=[
                        FieldCondition(key="_user_id", match=MatchValue(value=_user_id))
                    ],
                    must_not=[
                        FieldCondition(key="type", match=MatchValue(value=MARKER_TYPE))
                    ],
                ),
                limit=10,
                score_threshold=0.2,  # permissive; real filtering is the re-rank
            ).points
        except Exception:
            return []  # collection missing / qdrant down → just no memories

        scored = sorted(
            hits,
            key=lambda p: self._memory_strength(p.payload or {}, p.score),
            reverse=True,
        )
        top = scored[: self._cfg.memory_top_k]

        now = datetime.now().isoformat()
        for p in top:
            try:
                self._qdrant.set_payload(
                    collection_name=self._cfg.memory_collection,
                    payload={
                        "last_accessed": now,
                        "access_count": (p.payload or {}).get("access_count", 0) + 1,
                    },
                    points=[p.id],
                )
            except Exception:
                pass
        top.sort(key=lambda p: (p.payload or {}).get("created_at") or "")
        return [
            self._dated(p.payload or {}) for p in top if (p.payload or {}).get("text")
        ]

    @staticmethod
    def _dated(payload: dict) -> str:
        when = _ago(payload.get("created_at"))
        if payload.get("history"):
            when += f" (corrected {_ago(payload['history'][-1]['until'])})"
        return f"{when}: {payload['text']}"

    @staticmethod
    def _parse_json(raw: str) -> Any:
        """The LLM's JSON reply, without the markdown fence it may add."""
        return json.loads(
            raw.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )

    @staticmethod
    def _working_memory_text(working: WorkingMemory) -> str:
        parts = []
        if working.summary:
            parts.append(f"Earlier in this conversation: {working.summary}")
        if working.notes:
            parts.append("Keep in mind:\n- " + "\n- ".join(working.notes))
        return "\n".join(parts)

    async def fold_into_working_memory(
        self, working: WorkingMemory, turns: list
    ) -> WorkingMemory:
        """
        Working memory updated with turns leaving short-term memory.
        """

        capacity = self._cfg.working_memory_capacity
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in turns)
        prompt = (
            "You keep track of an ongoing conversation. Here is what you keep "
            "in mind so far (may be empty):\n"
            f"{self._working_memory_text(working) or '(nothing yet)'}\n\n"
            "These exchanges are now leaving your short-term memory:\n"
            f"{transcript}\n\n"
            "Update what you keep in mind. Reply ONLY with JSON, no markdown: "
            '{"summary": "...", "notes": ["..."]}. The summary tells the '
            "conversation so far in at most 4 sentences. The notes are at most "
            f"{capacity} short facts about the person, worth keeping in mind "
            "for the rest of the conversation: their names, facts, preferences "
            "and goals, their open questions, and anything you promised them. "
            "Not what you said or suggested. When a fact changed (a new name, "
            "a change of plan), keep only its latest version. Drop small talk "
            "and the least useful notes first."
        )
        try:
            data = await self._llm_json("You are a working memory.", prompt)
            summary = str(data["summary"]).strip()
            notes = [str(n).strip() for n in data.get("notes", []) if str(n).strip()]
            return WorkingMemory(summary=summary, notes=notes[:capacity])
        except Exception:
            print(
                "[RAG] working memory update failed, keeping the raw turns:\n"
                f"{traceback.format_exc()}"
            )
            summary = f"{working.summary}\n{transcript}".strip()[-1500:]
            return WorkingMemory(summary=summary, notes=working.notes)

    async def save_conversation(
        self, _user_id: str, history: list, working: WorkingMemory | None = None
    ) -> None:
        """Summarize a finished session into one memory point. Called at disconnect.

        ``history`` is only the session's last turns (short-term memory); the
        rest of it is in ``working``.
        """
        if not working and (not history or len(history) < 2):
            return
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in history)[-6000:]
        if working:
            # Labelled, or the LLM reads it as background and summarizes only
            # the last turns.
            transcript = (
                "PART 1, the start of the conversation, as you noted it:\n"
                f"{self._working_memory_text(working)}\n\n"
                f"PART 2, how the conversation ended:\n{transcript}"
            )
        prompt = (
            "Summarize this whole conversation in 2-4 sentences, from start to "
            "end, keeping every personal fact, preference, name, or commitment "
            "mentioned anywhere in it. When something changed, say plainly "
            'what it was and what it is now (e.g. "the cat, formerly called '
            'Pixel, is now called Felix"). Then rate 1-10 '
            "how important it is to remember (small talk=1-2, personal facts or "
            "preferences=7-10). Reply ONLY with JSON, no markdown: "
            '{"summary": "...", "importance": N}\n\n' + transcript
        )
        try:
            data = await self._llm_json("You are a memory summarizer.", prompt)
            summary = str(data["summary"])
            importance = max(1, min(int(data["importance"]), 10))
        except Exception:
            print(
                "[RAG] memory summarization failed, storing raw tail:\n"
                f"{traceback.format_exc()}"
            )
            summary, importance = transcript[-500:], 3

        if importance <= 1:
            print("[RAG] Session judged not memorable, skipping save")
            return

        vector = await self._embed(summary)
        self._ensure_memory_collection(len(vector))
        if self._cfg.memory_reconcile:
            await self._reconcile_memories(_user_id, summary, vector)
        point = memory_point(vector, summary, _user_id, "conversation", importance)
        self._qdrant.upsert(collection_name=self._cfg.memory_collection, points=[point])
        print(
            f"[RAG] Saved conversation memory (importance={importance}): "
            f"{summary[:80]}..."
        )

    @staticmethod
    def _stated_change(old: str, repl: str, new_memory: str) -> tuple[str, str] | None:
        """What changed from `old` to `repl` (Pixel -> Felix in "orange cat
        named Pixel" -> "orange cat named Felix"), if the new memory says
        both and it is short; None otherwise."""
        a, b = old.split(), repl.split()
        while a and b and a[0].lower() == b[0].lower():
            a, b = a[1:], b[1:]
        while a and b and a[-1].lower() == b[-1].lower():
            a, b = a[:-1], b[:-1]
        was, now = (" ".join(w).strip(".,;:!?\"'") for w in (a, b))
        lowered = new_memory.lower()
        if (
            was
            and now
            and len(a) <= 4
            and was.lower() in lowered
            and now.lower() in lowered
        ):
            return was, now
        return None

    async def _reconcile_memories(
        self, _user_id: str, new: str, vector: list[float]
    ) -> None:
        """
        Correct the user's older memories with what a new one says changed,
        so that "the cat was renamed Felix" turns "the cat is called Pixel"
        into "the cat is called Felix" instead of being recalled next to it.
        After Mem0 (Chhikara et al., 2025, arXiv:2504.19413), but the LLM
        only names each change (old -> new): the code applies it, and only
        when both are in the new memory and the old one in an older memory,
        so a confused LLM changes nothing rather than corrupting memories.
        """

        cfg = self._cfg
        try:
            hits = self._qdrant.query_points(
                collection_name=cfg.memory_collection,
                query=vector,
                query_filter=Filter(
                    must=[
                        FieldCondition(key="_user_id", match=MatchValue(value=_user_id))
                    ],
                    must_not=[
                        FieldCondition(key="type", match=MatchValue(value=MARKER_TYPE))
                    ],
                ),
                limit=cfg.memory_reconcile_top_k,
                score_threshold=cfg.memory_reconcile_threshold,
            ).points
        except Exception:
            return  # nothing stored yet
        if not hits:
            return

        older = "\n".join(f"- {(p.payload or {}).get('text', '')}" for p in hits)
        prompt = (
            f"A new memory about a person:\n{new}\n\n"
            f"Older memories about them:\n{older}\n\n"
            "Does the new memory say that something in the older ones changed "
            "(a new name, a move, a change of plan)? List each change: the "
            "outdated word or short phrase, exactly as written in an older "
            "memory, and what replaces it, exactly as written in the new one. "
            'Reply ONLY with JSON: {"changes": [{"old": "...", "new": "..."}]}, '
            "with an empty list when nothing changed."
        )
        try:
            reply = await self._llm_json("You are a memory keeper.", prompt)
            proposed = [
                (str(c["old"]).strip(), str(c["new"]).strip())
                for c in reply.get("changes", [])
                if isinstance(c, dict) and "old" in c and "new" in c
            ]
        except Exception:
            print(f"[RAG] memory reconciliation failed:\n{traceback.format_exc()}")
            return

        stated = [self._stated_change(*c, new) for c in proposed]
        changes = {c for c in stated if c is not None}
        if len(changes) < len(proposed):
            print(f"[RAG] Memory changes proposed: {proposed}, kept: {changes}")
        for p in hits:
            text = (p.payload or {}).get("text", "")
            fixed, applied = text, []
            for was, now in sorted(changes):
                pattern = rf"\b{re.escape(was)}\b"
                if re.search(pattern, fixed, flags=re.IGNORECASE):
                    fixed = re.sub(pattern, now, fixed, flags=re.IGNORECASE)
                    applied.append(f"{was} -> {now}")
            if not applied:
                continue
            # The text before, kept to audit or undo the correction.
            history = [
                *(p.payload or {}).get("history", []),
                {
                    "text": text,
                    "until": datetime.now().isoformat(),
                    "changes": applied,
                },
            ]
            self._qdrant.upsert(
                collection_name=cfg.memory_collection,
                points=[
                    PointStruct(
                        id=p.id,
                        vector=await self._embed(fixed),
                        payload={
                            **(p.payload or {}),
                            "text": fixed,
                            "history": history,
                        },
                    )
                ],
            )
            print(f"[RAG] Corrected memory: {fixed[:80]}...")

    def _last_maintenance(self) -> datetime | None:
        try:
            pts = self._qdrant.retrieve(
                collection_name=self._cfg.memory_collection,
                ids=[self._MAINTENANCE_MARKER_ID],
                with_payload=True,
                with_vectors=False,
            )
            if pts:
                payload = pts[0].payload or {}
                return datetime.fromisoformat(payload["last_run"])
        except Exception:
            pass
        return None

    def _mark_maintenance_done(self, vector_size: int) -> None:
        self._qdrant.upsert(
            collection_name=self._cfg.memory_collection,
            points=[
                PointStruct(
                    id=self._MAINTENANCE_MARKER_ID,
                    vector=[0.0] * vector_size,
                    payload={
                        "type": MARKER_TYPE,
                        "last_run": datetime.now().isoformat(),
                    },
                )
            ],
        )

    async def _maintenance_loop(self) -> None:
        check_secs = self._cfg.memory_maintenance_check_hours * 3600
        while True:
            try:
                last = self._last_maintenance()
                due = (
                    last is None
                    or (datetime.now() - last).total_seconds()
                    >= self._cfg.memory_maintenance_days * 86400
                )
                if due:
                    print("[RAG] Running memory maintenance...")
                    await self._run_maintenance()
            except Exception:
                print(f"[RAG] maintenance loop error:\n{traceback.format_exc()}")
            await asyncio.sleep(check_secs)

    async def _run_maintenance(self) -> None:
        """Decay-based pruning + consolidation, as memory_maintenance.py does."""
        collection = self._cfg.memory_collection
        try:
            points = scroll_all(self._qdrant, collection)
        except Exception:
            return  # collection doesn't exist yet — nothing to do
        to_delete, weak_by_user = plan(points, self._cfg.memory_half_life_days)
        vector_size = await consolidate_and_prune(
            self._qdrant,
            collection,
            to_delete,
            weak_by_user,
            self._llm_complete,
            self._embed,
        )
        if vector_size is None:
            vector_size = len(await self._embed("marker"))
        self._ensure_memory_collection(vector_size)
        self._mark_maintenance_done(vector_size)
        print("[RAG] Memory maintenance complete")

    def _search(
        self,
        qdrant,
        query_vector: list[float],
        collection: str,
        filters: dict | None = None,
    ) -> list[dict]:
        qdrant_filter: Any = None
        if filters:
            must: Any = []
            should: Any = None
            for k, v in filters.items():
                if k == "_user_id":
                    should = [
                        FieldCondition(key=k, match=MatchValue(value=v)),
                        FieldCondition(key=k, match=MatchValue(value=SHARED_USER_ID)),
                    ]
                else:
                    must.append(FieldCondition(key=k, match=MatchValue(value=v)))
            qdrant_filter = Filter(must=must or None, should=should)

        try:
            results = qdrant.query_points(
                collection_name=collection,
                query=query_vector,
                query_filter=qdrant_filter,
                limit=self._cfg.top_k,
                score_threshold=self._cfg.score_threshold,
            ).points
        except Exception as e:  # noqa: BLE001
            if collection not in self._warned_collections:
                self._warned_collections.add(collection)
                print(
                    f"[RAGHandle] WARNING: search on '{collection}' failed — "
                    f"{type(e).__name__}: {e}"
                )
                print(
                    "[RAGHandle]          answering without document context "
                    "from now on (logged once)"
                )
            results = []
        return [
            {
                "text": point.payload.get("text", ""),
                "score": point.score,
                "metadata": {k: v for k, v in point.payload.items() if k != "text"},
            }
            for point in results
        ]

    @staticmethod
    def _emotion_hint(emotion: dict, earlier: bool) -> str:
        name = _EMOTION_NAMES.get(emotion["label"], emotion["label"])
        sounds = (
            f"sounded {name} in their previous message"
            if earlier
            else f"sounds {name} right now"
        )
        return (
            f"From the tone of their voice, the person {sounds} "
            f"({emotion['confidence']:.0%} confident). Let it shape how you "
            "answer; only mention it if that helps."
        )

    def _build_prompt(
        self,
        question: str,
        chunks: list[dict],
        preferences: dict,
        profile_facts: list[str] | None = None,
        memories: list[str] | None = None,
        emotion: dict | None = None,
        emotion_earlier: bool = False,
        working: WorkingMemory | None = None,
    ) -> tuple[str, str]:
        persona = preferences.get("persona", _DEFAULT_PERSONA)
        parts = [persona]

        if profile_facts:
            parts.append(
                "Here is what you know about the person you're talking to: "
                + " ".join(profile_facts)
            )

        if preferences.get("language"):
            parts.append(f"Always respond in {preferences['language']}.")
        if preferences.get("tone"):
            parts.append(f"Use a {preferences['tone']} tone.")
        if preferences.get("response_format") == "bullet_points":
            parts.append("Format your answer as bullet points.")
        if preferences.get("extra_instructions"):
            parts.append(preferences["extra_instructions"])
        if emotion:
            parts.append(self._emotion_hint(emotion, emotion_earlier))

        parts.append("Keep your answer to 2-3 sentences maximum. This is a discussion.")
        parts.append(
            "Use the context and memories in the user's message to inform your "
            "answers when relevant, but always answer in character. If you have "
            "relevant memories of past conversations, use them naturally. Only if "
            "you genuinely know nothing relevant, improvise in character rather "
            "than admitting you lack information or breaking character. "
        )

        system_prompt = " ".join(parts)

        memory_block = ""
        if memories:
            memory_block = (
                "Things you remember from previous conversations with this person, "
                "oldest first (when two disagree, the more recent one is right):\n- "
                + "\n- ".join(memories)
                + "\n\n"
            )
        if working:
            memory_block += self._working_memory_text(working) + "\n\n"

        if not chunks:
            user_prompt = (
                memory_block + "No relevant context was found.\n\n"
                f"Question: {question}\n\n"
                "Answer based on your memories above if relevant, otherwise "
                "general knowledge."
            )
        else:
            context_parts = []
            for i, chunk in enumerate(chunks, 1):
                source = chunk["metadata"].get("source", "unknown")
                context_parts.append(
                    f"[{i}] (source: {source}, score: {chunk['score']:.2f})\n"
                    f"{chunk['text']}"
                )
            context_block = "\n\n".join(context_parts)
            user_prompt = (
                memory_block + f"Context:\n{context_block}\n\n"
                f"Question: {question}\n\n"
                "Answer based on the context and your memories above. "
                "Don't speak about the sources, just use them to answer."
            )

        user_prompt += (
            "\n\nStart your answer straight on the substance — do not open with "
            '"Ah" or "Oh".'
        )

        return system_prompt, user_prompt

    @staticmethod
    def _ollama_delta(line: str) -> tuple[str, bool]:
        """Text and end of stream from one line of Ollama's /api/chat."""
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            return "", False
        return chunk.get("message", {}).get("content", ""), bool(chunk.get("done"))

    @staticmethod
    def _openai_delta(line: str) -> tuple[str, bool]:
        """Text and end of stream from one server-sent event of
        /v1/chat/completions."""
        if not line.startswith("data:"):
            return "", False
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return "", True
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return "", False
        return chunk.get("choices", [{}])[0].get("delta", {}).get("content", ""), False

    async def _llm_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        preferences: dict,
        history: list | None = None,
    ) -> AsyncGenerator[str, None]:
        cfg = self._cfg
        max_tokens = preferences.get("max_length", 1024)
        temperature = preferences.get("temperature", 0.7)
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_prompt})

        headers: dict[str, str] = {}
        body: dict[str, Any]
        if cfg.llm_provider == "ollama":
            url, parse = f"{cfg.llm_url}/api/chat", self._ollama_delta
            options = {"num_predict": max_tokens, "temperature": temperature}
            body = {"messages": messages, "options": options}
        elif cfg.llm_provider in ("vllm", "api"):
            url, parse = f"{cfg.llm_url}/v1/chat/completions", self._openai_delta
            body = {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if cfg.llm_provider == "api" and cfg.llm_api_key:
                headers["Authorization"] = f"Bearer {cfg.llm_api_key}"
        else:
            raise ValueError(f"Unknown llm_provider: {cfg.llm_provider}")
        body |= {"model": cfg.llm_model, "stream": True}
        if preferences.get("json"):  # the reply must be a JSON object
            if cfg.llm_provider == "ollama":
                body["format"] = "json"
            else:
                body["response_format"] = {"type": "json_object"}

        for attempt in range(1, cfg.llm_retries + 1):
            yielded_any = False
            try:
                async with self._llm_client.stream(
                    "POST", url, headers=headers, json=body
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        delta, done = parse(line)
                        if delta:
                            yielded_any = True
                            yield delta
                        if done:
                            return
                return
            except _RETRIABLE_EXC as e:
                # Once the answer has started, a retry would repeat it.
                if yielded_any or attempt == cfg.llm_retries:
                    raise
                delay = cfg.retry_base_delay * (2 ** (attempt - 1))
                print(
                    f"[RAG] LLM stream connect to {url} failed (attempt "
                    f"{attempt}/{cfg.llm_retries}): {e!r} — retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

    async def stream(self, query: RAGQuery) -> AsyncGenerator[str, None]:
        """Main streaming entry point — yields LLM text deltas."""
        print(f"[RAG] Question: {query.question}")

        collection, filters = self._resolve_user_context(query._user_id)
        try:
            query_vector = await self._embed(query.question)
        except Exception:
            print(f"[RAG] FAILED during embedding:\n{traceback.format_exc()}")
            raise

        try:
            chunks = self._search(self._qdrant, query_vector, collection, filters)
        except Exception:
            print(f"[RAG] FAILED during Qdrant search:\n{traceback.format_exc()}")
            raise

        print(f"[RAG] Found {len(chunks)} chunks")
        memories, profile_facts = [], []
        if query.long_term:
            memories = self._search_memories(query_vector, query._user_id)
            profile_facts = self._get_profile(collection, query._user_id)
        if memories:
            print(f"[RAG] Recalled {len(memories)} memory(ies)")
        if profile_facts:
            print(f"[RAG] Loaded {len(profile_facts)} profile fact(s)")
        if query.emotion:
            print(
                f"[RAG] Emotion: {query.emotion['label']} "
                f"({'previous message' if query.emotion_earlier else 'this message'})"
            )
        system_prompt, user_prompt = self._build_prompt(
            query.question,
            chunks,
            query.preferences,
            profile_facts,
            memories,
            query.emotion,
            query.emotion_earlier,
            working=query.working,
        )

        print(
            f"[RAG] Streaming from LLM at {self._cfg.llm_url} "
            f"(provider={self._cfg.llm_provider}, model={self._cfg.llm_model}, "
            f"history_msgs={len(query.history)}, "
            f"working_notes={len(query.working.notes)})"
        )
        try:
            async for delta in self._llm_stream(
                system_prompt, user_prompt, query.preferences, query.history
            ):
                yield delta
        except Exception:
            print(f"[RAG] FAILED during LLM stream:\n{traceback.format_exc()}")
            raise


class RAG(ModuleWithHandle, ModuleWithId):
    """RAG Module — streams LLM tokens.

    input:  huri.question (RAGQuestion)
    output: huri.token    (Token)

    The question's emotion, when it has one, tells the LLM how the user
    sounded: this message's, or the previous one's when QAG's parallel mode
    sent the question before EMO had finished (``emotion_earlier``).
    """

    _handle_cls = RAGHandle
    input_type = "huri.question"
    output_type = "huri.token"
    input_event = RAGQuestion
    output_event = Token

    def __init__(
        self,
        _handle: handle.DeploymentHandle,
        _user_id: str,
        language="en",
        tone="formal",
        response_format="paragraph",
        max_length=220,
        extra_instructions="",
        persona="",
        temperature=0.7,
        max_history_turns=6,
        memory: str | Sequence[str] = MEMORIES,
        **kwargs,
    ):
        super().__init__(_handle=_handle, _user_id=_user_id, **kwargs)

        print(
            f"[RAG] Initialized with user_id={_user_id}, language={language}, "
            f"tone={tone}, response_format={response_format}, "
            f"max_length={max_length}, temperature={temperature}, "
            f"max_history_turns={max_history_turns}, memory={memory}"
        )

        if isinstance(memory, str):
            memory = [m for m in memory.replace(" ", "").split(",") if m]
        unknown = set(memory) - set(MEMORIES)
        if unknown:
            raise ValueError(
                f"Unknown memory {sorted(unknown)}: pick among {', '.join(MEMORIES)}"
            )
        self._short = "short" in memory
        self._working = "working" in memory
        self._long = "long" in memory

        self.preferences = {
            "language": language,
            "tone": tone,
            "response_format": response_format,
            "max_length": max_length,
            "extra_instructions": extra_instructions,
            "temperature": temperature,
        }
        if persona:
            self.preferences["persona"] = persona

        self._max_history_turns = (
            max_history_turns if self._short or not self._working else 0
        )
        self.history: list[dict] = []  # short-term memory
        self.working = WorkingMemory()
        self._working_lock = asyncio.Lock()

    async def process(self, data: RAGQuestion) -> AsyncGenerator[Token, None]:
        """
        Called when a "question" event arrives through the event bus.
        Packages _user_id + question, sends to the stateless RAGHandle.
        """
        question_text = data.transcript.text

        emotion = data.emotion
        query = RAGQuery(
            _user_id=self._user_id if self._user_id else "anonymous",
            question=question_text,
            preferences=self.preferences,
            emotion=(
                {"label": emotion.label, "confidence": float(emotion.confidence)}
                if emotion is not None
                else None
            ),
            emotion_earlier=(
                bool(data.emotion_earlier) if emotion is not None else False
            ),
            history=list(self.history) if self._short else [],  # prior turns
            working=self.working,
            long_term=self._long,
        )

        parts: list[str] = []
        stream = self._handle.options(stream=True).stream.remote(query)
        async for delta in stream:  # type: ignore[union-attr]
            parts.append(delta)
            yield Token(text=delta, end=False)
        yield Token(text="", end=True)

        evicted = self._record_turn(question_text, "".join(parts))
        if evicted and self._working:
            await self._fold_into_working_memory(evicted)

    def _record_turn(self, question: str, answer: str) -> list[dict]:
        answer = answer.strip()
        if not answer:
            return []
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})
        max_msgs = self._max_history_turns * 2
        evicted = self.history[: max(len(self.history) - max_msgs, 0)]
        del self.history[: len(evicted)]
        return evicted

    async def _fold_into_working_memory(self, turns: list[dict]) -> None:
        async with self._working_lock:
            try:
                self.working = await self._handle.fold_into_working_memory.remote(
                    self.working, turns
                )
            except Exception:
                print(f"[RAG] working memory update failed:\n{traceback.format_exc()}")

    async def finalize(self) -> None:
        if not self._long:
            return
        async with self._working_lock:
            if not self.history and not self.working:
                return
            try:
                await self._handle.save_conversation.remote(
                    self._user_id or "anonymous", list(self.history), self.working
                )
            except Exception:
                print(f"[RAG] finalize failed:\n{traceback.format_exc()}")
