"""Reranker golden-set bootstrap (Plan 1, Task 5).

Mines (query -> relevant doc) pairs from published ``posts``: the post title
is the query, a chunk of its own body is the one relevant candidate, and the
rest of each case's candidates are distractor chunks sampled from *other*
posts. The production corpus IS the test set — no hand-labeling, no dummy data.

The set is versioned by a stable hash of the source post-id set, so the same
posts always yield the same version (and the same seeded distractor sampling),
while publishing new posts rolls the version forward.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from services.model_eval.types import GoldenCase, GoldenSet

_GOLDEN_NAME = "model_eval_reranker"
_CHUNK_CHARS = 600
_MIN_CONTENT_LEN = 200


def _chunk(content: str) -> str:
    """Collapse whitespace and take a leading window as the candidate doc text."""
    return " ".join(content.split())[:_CHUNK_CHARS]


async def _fetch_published_posts(pool: Any, *, min_len: int) -> list[dict[str, str]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, content FROM posts "
            "WHERE status = 'published' AND content IS NOT NULL "
            "AND title IS NOT NULL AND length(content) >= $1 "
            "ORDER BY id",
            min_len,
        )
    return [
        {"id": str(r["id"]), "title": str(r["title"]), "content": str(r["content"])}
        for r in rows
    ]


async def build_reranker_golden_set(*, pool: Any, site_config: Any) -> GoldenSet:
    """Build the reranker golden set from published posts.

    Raises ``RuntimeError`` (fail loud) when there aren't enough posts to fill
    a single case's candidate list — silently shrinking the eval would make a
    challenger look better than it is.
    """
    size = int(site_config.get("model_eval_reranker_golden_size", "50"))
    per_case = int(site_config.get("model_eval_reranker_candidates_per_case", "20"))

    posts = await _fetch_published_posts(pool, min_len=_MIN_CONTENT_LEN)
    if len(posts) < per_case:
        raise RuntimeError(
            f"reranker golden set needs >= {per_case} published posts "
            f"(model_eval_reranker_candidates_per_case); found {len(posts)}. "
            "Lower the setting or publish more posts."
        )

    # Stable version from the source id set -> reproducible sampling.
    ids = sorted(p["id"] for p in posts)
    version = int.from_bytes(
        hashlib.blake2b(",".join(ids).encode("utf-8"), digest_size=4).digest(), "big"
    )
    rng = random.Random(version)

    n_cases = min(size, len(posts))
    cases: list[GoldenCase] = []
    for query_post in posts[:n_cases]:
        others = [p for p in posts if p["id"] != query_post["id"]]
        distractors = rng.sample(others, per_case - 1)
        candidates: list[dict[str, Any]] = [
            {"doc_id": query_post["id"], "text": _chunk(query_post["content"]), "relevance": 1}
        ]
        for d in distractors:
            candidates.append({"doc_id": d["id"], "text": _chunk(d["content"]), "relevance": 0})
        rng.shuffle(candidates)
        cases.append(GoldenCase(query=query_post["title"], candidates=candidates))

    return GoldenSet(name=_GOLDEN_NAME, version=version, cases=cases)
