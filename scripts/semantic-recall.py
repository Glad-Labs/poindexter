"""
Semantic Recall from Memory Embeddings

Standalone script that queries pgvector for the most similar memory chunks
given a natural language query. Fast enough to run on session startup.

Usage:
    python scripts/semantic-recall.py "what do I know about cost control"
    python scripts/semantic-recall.py "deployment strategy" --top 10
"""

import asyncio
import json
import sys
from typing import Any, Dict, List

import asyncpg
import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_DSN = "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain"
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
SOURCE_TABLE = "memory"
DEFAULT_TOP_K = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def embed_query(client: httpx.AsyncClient, text: str) -> List[float]:
    """Embed query text via Ollama."""
    resp = await client.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": text},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    embeddings = data.get("embeddings", [])
    if not embeddings:
        raise RuntimeError("No embeddings returned from Ollama")
    return embeddings[0]


async def search_similar(
    conn: asyncpg.Connection,
    embedding: List[float],
    top_k: int = DEFAULT_TOP_K,
) -> List[Dict[str, Any]]:
    """Cosine similarity search against memory embeddings."""
    vector_str = "[" + ",".join(str(v) for v in embedding) + "]"

    rows = await conn.fetch(
        """
        SELECT source_table, source_id, content_hash, text_preview, metadata,
               1 - (embedding <=> $1::vector) as similarity
        FROM embeddings
        ORDER BY embedding <=> $1::vector
        LIMIT $2
        """,
        vector_str,
        top_k,
    )

    results = []
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        results.append({
            "source_table": row["source_table"],
            "source_id": row["source_id"],
            "similarity": float(row["similarity"]),
            "text_preview": row["text_preview"] or "",
            "metadata": meta,
        })
    return results


def format_results(results: List[Dict[str, Any]], query: str) -> str:
    """Format search results for display."""
    lines = [
        f'Semantic recall for: "{query}"',
        "=" * 60,
    ]

    if not results:
        lines.append("No results found.")
        return "\n".join(lines)

    for i, r in enumerate(results, 1):
        meta = r["metadata"]
        sim = r["similarity"]
        source = r["source_id"]
        source_table = r.get("source_table", "?")
        origin = meta.get("origin", source_table)
        ftype = meta.get("type", meta.get("state", "?"))
        preview = r["text_preview"][:120].replace("\n", " ")

        lines.append(
            f"\n{i}. [{sim:.4f}] [{source_table}] {source}\n"
            f"   origin={origin}  type={ftype}\n"
            f"   {preview}..."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python scripts/semantic-recall.py "your query here" [--top N]')
        sys.exit(1)

    query = sys.argv[1]
    top_k = DEFAULT_TOP_K

    # Parse optional --top N
    if "--top" in sys.argv:
        idx = sys.argv.index("--top")
        if idx + 1 < len(sys.argv):
            top_k = int(sys.argv[idx + 1])

    conn = await asyncpg.connect(DB_DSN)
    http = httpx.AsyncClient()

    try:
        embedding = await embed_query(http, query)
        results = await search_similar(conn, embedding, top_k)
        print(format_results(results, query))
    finally:
        await conn.close()
        await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
