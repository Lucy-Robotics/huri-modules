"""Periodic memory maintenance: decay-based pruning + consolidation.

Run every N days (cron/systemd timer):
    python -m huri_rag.memory_maintenance
Strong memories are kept, weak ones are merged into a consolidated memory,
dead ones are deleted. Decay itself is computed lazily at query time in
rag.py; this job only prunes and compresses. RAGHandle runs the same job on
its own every `memory_maintenance_days`, and memory_inspect shows what it
would do to each memory.
"""

import argparse
import asyncio
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Awaitable, Callable

import httpx
from qdrant_client.models import PointIdsList, PointStruct

try:
    from .qdrant_utils import make_qdrant_client
except ImportError:
    from qdrant_utils import make_qdrant_client  # type: ignore[no-redef]

DELETE_BELOW = 0.05
CONSOLIDATE_BELOW = 0.30
HALF_LIFE_DAYS = 5.0
MARKER_TYPE = "maintenance_marker"


def recency(
    payload: dict, half_life_days: float = HALF_LIFE_DAYS, at: datetime | None = None
) -> float:
    """Exponential decay since the memory was last recalled; the more
    important it is, the slower it fades."""
    importance: int = payload.get("importance", 3)
    half_life = max(half_life_days * (importance / 5.0), 0.5)
    try:
        last = datetime.fromisoformat(
            payload.get("last_accessed") or payload["created_at"]
        )
        age_days = ((at or datetime.now()) - last).total_seconds() / 86400.0
    except Exception:
        age_days = 0.0
    return float(0.5 ** (max(age_days, 0) / half_life))


def strength(
    payload: dict, half_life_days: float = HALF_LIFE_DAYS, at: datetime | None = None
) -> float:
    """Query-independent strength: recency * importance (no relevance term)."""
    importance: int = payload.get("importance", 3)
    return recency(payload, half_life_days, at) * importance / 10.0


def memory_point(
    vector: list[float], text: str, user_id: str, kind: str, importance: int
) -> PointStruct:
    now = datetime.now().isoformat()
    return PointStruct(
        id=str(uuid.uuid4()),
        vector=vector,
        payload={
            "text": text,
            "_user_id": user_id,
            "type": kind,
            "created_at": now,
            "last_accessed": now,
            "access_count": 0,
            "importance": importance,
        },
    )


def scroll_all(qdrant, collection: str) -> list:
    points, offset = [], None
    while True:
        batch, offset = qdrant.scroll(
            collection_name=collection,
            limit=200,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            return points


def plan(points: list, half_life_days: float = HALF_LIFE_DAYS) -> tuple[list, dict]:
    """The memories to delete, and the weak ones to merge, by user."""
    to_delete, weak_by_user = [], defaultdict(list)
    for p in points:
        payload = p.payload or {}
        if payload.get("type") == MARKER_TYPE:
            continue
        s = strength(payload, half_life_days)
        if s < DELETE_BELOW:
            to_delete.append(p)
        elif s < CONSOLIDATE_BELOW:
            weak_by_user[payload.get("_user_id", "anonymous")].append(p)
    return to_delete, weak_by_user


async def consolidate_and_prune(
    qdrant,
    collection: str,
    to_delete: list,
    weak_by_user: dict,
    complete: Callable[[str, str], Awaitable[str]],  # (system, prompt) -> reply
    embed: Callable[[str], Awaitable[list[float]]],
) -> int | None:
    """Merge each user's weak memories into one, delete them and the dead
    ones. Returns the size of the vectors it embedded, if it embedded any."""
    to_delete = list(to_delete)
    vector_size = None
    for user, weak in weak_by_user.items():
        if len(weak) < 3:
            continue  # not worth merging yet; keep decaying
        texts = [(p.payload or {})["text"] for p in weak]
        merged = (
            await complete(
                "You are a memory consolidator.",
                "These are old memories about conversations with the same "
                "person. Merge them into a single 3-5 sentence memory keeping "
                "only durable facts, preferences and recurring themes. Drop "
                "one-off small talk.\n\n" + "\n---\n".join(texts),
            )
        ).strip()
        if not merged:
            continue
        vector = await embed(merged)
        vector_size = len(vector)
        imp = min(max((p.payload or {}).get("importance", 3) for p in weak) + 1, 10)
        qdrant.upsert(
            collection_name=collection,
            points=[
                memory_point(vector, merged, user, "conversation_consolidated", imp)
            ],
        )
        to_delete.extend(weak)
        print(f"[{user}] consolidated {len(weak)} → 1 (importance={imp})")

    if to_delete:
        qdrant.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=[p.id for p in to_delete]),
        )
        print(f"deleted {len(to_delete)} memories")
    return vector_size


async def _run(args) -> None:
    qdrant = make_qdrant_client(args.qdrant_url)
    points = scroll_all(qdrant, args.collection)
    print(f"{len(points)} memories in '{args.collection}'")
    to_delete, weak_by_user = plan(points)
    print(
        f"delete: {len(to_delete)}, "
        f"consolidate candidates: {sum(map(len, weak_by_user.values()))}"
    )
    if args.dry_run:
        return

    async with httpx.AsyncClient(timeout=180.0) as http:

        async def complete(system: str, prompt: str) -> str:
            r = await http.post(
                f"{args.ollama_url}/api/chat",
                json={
                    "model": args.llm_model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"num_predict": 300},
                },
            )
            r.raise_for_status()
            content: str = r.json()["message"]["content"]
            return content

        async def embed(text: str) -> list[float]:
            r = await http.post(
                f"{args.ollama_url}/v1/embeddings",
                json={"model": args.embedding_model, "input": text},
            )
            r.raise_for_status()
            embedding: list[float] = r.json()["data"][0]["embedding"]
            return embedding

        await consolidate_and_prune(
            qdrant, args.collection, to_delete, weak_by_user, complete, embed
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant-url", default="http://localhost:6333")
    ap.add_argument("--collection", default="conversations")
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--embedding-model", default="bge-large-en-v1.5-gguf-Q4_K_M")
    ap.add_argument("--llm-model", default="mistral:7b")
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    main()
