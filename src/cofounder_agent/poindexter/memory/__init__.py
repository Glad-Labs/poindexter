"""poindexter.memory — shared pgvector memory client.

One client library that Claude Code, OpenClaw, the content worker, both MCP
servers, and the poindexter CLI all import. Built for the "no tool drift"
goal from Gitea #192.

Usage::

    from poindexter.memory import MemoryClient

    mem = MemoryClient()
    await mem.connect()

    # Write
    await mem.store(
        text="Decision: default pipeline_writer_model = gemma3:27b because...",
        writer="claude-code",
        source_id="claude-code/decision_writer_model.md",
        tags=["decisions", "models"],
    )

    # Read
    hits = await mem.search(
        query="why did we pick gemma3 for writing",
        writer="claude-code",           # or None for all writers
        source_table="memory",          # or None for all tables
        min_similarity=0.65,
        limit=5,
    )
    for h in hits:
        print(f"{h.similarity:.3f} [{h.writer}] {h.source_id}: {h.text_preview[:80]}")

    await mem.close()

Embedding model is `nomic-embed-text` (768 dim) — matches every existing
writer in the pipeline, so cosine math lines up across tools.
"""

from .client import MemoryClient, MemoryHit

__all__ = ["MemoryClient", "MemoryHit"]
