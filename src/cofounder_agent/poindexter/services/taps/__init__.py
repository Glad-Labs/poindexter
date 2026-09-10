"""Tap implementations — data sources for the embedding pipeline.

Phase B of the plugin refactor (GitHub #66). Each file is one Tap:

- :mod:`memory <.memory>` — memory files under every Claude project scope
- :mod:`openclaw_sqlite <.openclaw_sqlite>` — OpenClaw SQLite memory chunks
- :mod:`published_posts <.published_posts>` — ``posts`` DB table
- :mod:`audit <.audit>` — ``audit_log`` DB table
- :mod:`gitea_issues <.gitea_issues>` — Gitea issues / comments via HTTP API
- :mod:`brain_knowledge <.brain_knowledge>` — ``brain_knowledge`` DB table
- :mod:`brain_decisions <.brain_decisions>` — ``brain_decisions`` DB table

All register via ``[project.entry-points."poindexter.taps"]`` in
``pyproject.toml``. ``scripts/auto-embed.py`` becomes a thin runner
that iterates ``get_taps()`` and stores every yielded Document
uniformly.

Shared infrastructure:

- :func:`chunk_text <._chunking.chunk_text>` — markdown-heading-aware
  splitting, already battle-tested by the pre-refactor auto-embed.
- :func:`content_hash <._chunking.content_hash>` — SHA-256 for dedup.
- :func:`classify_file <._chunking.classify_file>` — filename → type
  taxonomy.
"""

from ._chunking import chunk_text, classify_file, content_hash

__all__ = ["chunk_text", "classify_file", "content_hash"]
