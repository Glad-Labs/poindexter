"""Calibrate the content-embedding topic-dedup threshold.

Embeds a candidate topic with nomic-embed-text and shows its cosine similarity
to published-post CONTENT embeddings — the same corpus ``find_similar_posts`` /
the ``create_post`` dedup guard (``topic_dedup_guard``) search. Confirms the
auto-discovery path would catch the VRAM cluster INCLUDING "The VRAM Currency
Problem", which title-based dedup missed (only 0.55 on titles).

Run from the repo root with the backend poetry env:
    python scripts/calibrate_topic_dedup_threshold.py

Read-only against the DB + local Ollama. Prints the candidate's top content
matches and a couple of unrelated control topics so a threshold can be chosen
that separates the VRAM cluster from genuinely-distinct topics.
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import asyncpg

# Repo root on path so ``brain.bootstrap`` resolves when run as ``python scripts/…``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brain.bootstrap import resolve_database_url  # noqa: E402  # type: ignore

OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"

# The topic that slipped through word_overlap (task b740e4b8), plus controls:
# an on-niche-but-distinct topic (should NOT dup) and an off-topic one.
CANDIDATE = "GPU VRAM Budgeting for Local AI Inference"
CONTROLS = [
    "Setting up Giscus comments on a Next.js blog",
    "Prompt injection defenses for LLM agents",
]


def _embed(text: str) -> list[float]:
    body = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["embedding"]


async def _top_matches(vec: list[float], k: int = 12):
    dsn = resolve_database_url()
    vec_str = "[" + ",".join(str(v) for v in vec) + "]"
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(
            """
            SELECT p.title, MAX(1 - (e.embedding <=> $1::vector)) AS cosine
              FROM embeddings e JOIN posts p ON p.id = e.source_id::uuid
             WHERE e.source_table = 'posts' AND p.status = 'published'
             GROUP BY p.title
             ORDER BY cosine DESC
             LIMIT $2
            """,
            vec_str,
            k,
        )
    finally:
        await conn.close()


def _report(label: str, topic: str) -> float:
    rows = asyncio.run(_top_matches(_embed(topic)))
    print(f"\n{label}: {topic!r}")
    for r in rows[:8]:
        print(f"  {r['cosine']:.3f}  {r['title']}")
    return float(rows[0]["cosine"]) if rows else 0.0


def main() -> int:
    cand_best = _report("CANDIDATE (should dup)", CANDIDATE)
    control_bests = [_report("CONTROL (should NOT dup)", c) for c in CONTROLS]
    print(
        f"\nCandidate best cosine = {cand_best:.3f}; "
        f"control bests = {[round(c, 3) for c in control_bests]}"
    )
    print(
        "Pick a threshold between the highest control and the candidate best "
        "(and confirm 'The VRAM Currency Problem' appears in the candidate list "
        "above it)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
