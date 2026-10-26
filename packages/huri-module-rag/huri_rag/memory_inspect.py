"""Inspect conversation memories: current strength, decay projection, fate.

Usage:
    python -m huri_rag.memory_inspect
    python -m huri_rag.memory_inspect --user-id <id>
"""

import argparse
from datetime import datetime, timedelta

try:
    from .memory_maintenance import (
        CONSOLIDATE_BELOW,
        DELETE_BELOW,
        MARKER_TYPE,
        scroll_all,
        strength,
    )
    from .qdrant_utils import make_qdrant_client
except ImportError:
    from memory_maintenance import (  # type: ignore[no-redef]
        CONSOLIDATE_BELOW,
        DELETE_BELOW,
        MARKER_TYPE,
        scroll_all,
        strength,
    )
    from qdrant_utils import make_qdrant_client  # type: ignore[no-redef]


def fate(s: float) -> str:
    if s < DELETE_BELOW:
        return "DELETE"
    if s < CONSOLIDATE_BELOW:
        return "CONSOLIDATE"
    return "KEEP"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant-url", default="http://localhost:6333")
    ap.add_argument("--collection", default="conversations")
    ap.add_argument("--user-id", default=None)
    args = ap.parse_args()

    qdrant = make_qdrant_client(args.qdrant_url)
    points = scroll_all(qdrant, args.collection)

    now = datetime.now()
    rows = []
    for p in points:
        pl = p.payload or {}
        if pl.get("type") == MARKER_TYPE:
            print(f"[marker] last maintenance run: {pl.get('last_run')}\n")
            continue
        if args.user_id and pl.get("_user_id") != args.user_id:
            continue
        s_now = strength(pl, at=now)
        rows.append(
            {
                "text": pl.get("text", "")[:60].replace("\n", " "),
                "type": pl.get("type", "?"),
                "imp": pl.get("importance", "?"),
                "acc": pl.get("access_count", 0),
                "age_d": round(
                    (
                        now
                        - datetime.fromisoformat(
                            pl.get("last_accessed") or pl["created_at"]
                        )
                    ).total_seconds()
                    / 86400,
                    1,
                ),
                "now": round(s_now, 3),
                "+5d": round(strength(pl, at=now + timedelta(days=5)), 3),
                "+15d": round(strength(pl, at=now + timedelta(days=15)), 3),
                "fate": fate(s_now),
            }
        )

    rows.sort(key=lambda r: r["now"], reverse=True)
    hdr = (
        f"{'strength':>8} {'+5d':>6} {'+15d':>6} {'imp':>3} {'acc':>3} "
        f"{'age':>5} {'fate':<12} text"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['now']:>8} {r['+5d']:>6} {r['+15d']:>6} {r['imp']:>3} "
            f"{r['acc']:>3} {r['age_d']:>5} {r['fate']:<12} {r['text']}"
        )
    print(f"\n{len(rows)} memories")


if __name__ == "__main__":
    main()
