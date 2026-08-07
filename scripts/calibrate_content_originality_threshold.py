"""Calibrate the content_originality rail threshold (whole-post max-over-chunks).

The ``qa.content_originality`` rail chunks a DRAFT with ``_chunk_draft``, embeds
each chunk, and scores the MAX chunk-vs-corpus cosine against the nearest
published post. This script measures that exact distribution across the live
published corpus: every published post is treated as if it were a fresh draft —
re-chunked with the rail's OWN ``_chunk_draft`` (so the query-side granularity
matches production), each chunk embedded with the same ``nomic-embed-text``
model, and matched to its nearest OTHER published post (self excluded) — and the
per-post MAX cosine is collected.

Pick ``content_originality_max_similarity`` at roughly p95 of that distribution.
The opening-only 0.83 was p95 of the NARROWER opening-vs-chunk distribution;
max-over-chunks takes the worst chunk of the whole body, which shifts the
distribution UP, so expect a somewhat higher ceiling. The 2026-06 VRAM-cluster
posts (choosing-a-quantization-format / the-vram-currency-problem /
why-kv-cache-quantization / single-gpu-vram-budgeting) should sit ABOVE the
chosen threshold; genuinely-distinct posts BELOW it.

Run from the repo root with the backend poetry env:
    python scripts/calibrate_content_originality_threshold.py

Read-only against the DB + local Ollama. Prints the per-post max-cosine
distribution (percentiles), the top self-echoing posts (eyeball the VRAM
cluster), and a recommended threshold. Nothing is written — seed the chosen
value via ``settings_defaults.py`` + ``poindexter settings set`` on prod.
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
from modules.content.atoms.qa_content_originality import (  # noqa: E402
    _DEFAULT_CHUNK_MAX,
    _DEFAULT_CHUNK_MIN,
    _chunk_draft,
)

from brain.bootstrap import resolve_database_url  # noqa: E402  # type: ignore

OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"


def _embed(text: str) -> list[float]:
    body = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["embedding"]


async def _nearest_other(conn: asyncpg.Connection, vec: list[float], self_id: str):
    """Nearest published-post chunk that is NOT the post being scored."""
    vec_str = "[" + ",".join(str(v) for v in vec) + "]"
    return await conn.fetchrow(
        """
        SELECT p.slug AS slug, 1 - (e.embedding <=> $1::vector) AS cosine
          FROM embeddings e
          JOIN posts p ON p.id = e.source_id::uuid
         WHERE e.source_table = 'posts'
           AND p.status = 'published'
           AND e.source_id <> $2
         ORDER BY e.embedding <=> $1::vector
         LIMIT 1
        """,
        vec_str, self_id,
    )


def _pct(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile of an already-sorted list."""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


async def _run() -> int:
    dsn = resolve_database_url()
    conn = await asyncpg.connect(dsn)
    try:
        posts = await conn.fetch(
            "SELECT id::text AS id, slug, content FROM posts "
            "WHERE status = 'published' AND content IS NOT NULL"
        )
        # (post_max_cosine, slug, worst-chunk's nearest neighbor slug)
        results: list[tuple[float, str, str]] = []
        for post in posts:
            chunks = _chunk_draft(
                post["content"],
                min_chars=_DEFAULT_CHUNK_MIN,
                max_chars=_DEFAULT_CHUNK_MAX,
            )
            best_sim, best_nbr = 0.0, None
            for chunk in chunks:
                row = await _nearest_other(conn, _embed(chunk), post["id"])
                if row and float(row["cosine"]) > best_sim:
                    best_sim, best_nbr = float(row["cosine"]), row["slug"]
            results.append((best_sim, post["slug"], best_nbr or "—"))
    finally:
        await conn.close()

    if not results:
        print("No published posts with content — nothing to calibrate.")
        return 1

    maxes = sorted(r[0] for r in results)
    print(f"\nScored {len(results)} published posts (per-post max-over-chunks cosine):")
    for q, label in ((0.50, "p50"), (0.90, "p90"), (0.95, "p95"), (0.99, "p99")):
        print(f"  {label} = {_pct(maxes, q):.3f}")

    print("\nTop 12 self-echoing posts (candidate near-duplicates):")
    for sim, slug, nbr in sorted(results, reverse=True)[:12]:
        print(f"  {sim:.3f}  {slug}  ->  nearest: {nbr}")

    rec = _pct(maxes, 0.95)
    print(
        f"\nRecommended content_originality_max_similarity ~= {rec:.2f} (p95). "
        "Confirm the VRAM-cluster posts sit above it and unrelated posts below, "
        "then seed via settings_defaults.py + set the live value on prod."
    )
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
