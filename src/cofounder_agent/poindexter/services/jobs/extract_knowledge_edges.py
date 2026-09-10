"""ExtractKnowledgeEdgesJob — keep ``knowledge_edges`` current (poindexter#1035).

Rebuilds BOTH edge origins: ``memory_wikilink`` from ``[[slug]]`` links between
memory files, and ``post_internal_link`` from ``/posts/<slug>`` links between
published posts (poindexter#1036). They are replaced independently, so a
failure or an empty result in one never disturbs the other.

The memory corpus's ``[[wiki-link]]`` graph changes whenever a memory is
written or edited, so the edge table is derived state that must be rebuilt, not
migrated once. Extraction is a regex pass over already-indexed text: no LLM, no
GPU, no network.

Runs after the memory Tap in practice — an edge can only resolve to a document
the Tap has already indexed, so a memory written since the last tap run shows up
as ``dangling`` until then and resolves on the following pass. That is correct
behaviour, not a race to fix.
"""

from __future__ import annotations

from typing import Any

from plugins.job import JobResult
from services.knowledge_graph import (
    ORIGIN_MEMORY,
    ORIGIN_POST,
    build_edges,
    build_post_edges,
    load_memory_docs,
    load_posts,
    replace_edges,
)
from services.logger_config import get_logger

logger = get_logger(__name__)


class ExtractKnowledgeEdgesJob:
    """Rebuild the memory wiki-link edge set."""

    name = "extract_knowledge_edges"
    description = "Rebuild knowledge_edges from memory [[wiki-links]]"
    # Hourly: the graph only changes when a memory file is written, and the
    # memory Tap this depends on runs on its own cadence anyway.
    schedule = "every 60 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        del config  # no per-install config today

        docs = await load_memory_docs(pool)
        mem_edges, mem_stats = build_edges(docs)
        mem_written = await replace_edges(pool, mem_edges, origin=ORIGIN_MEMORY)

        posts = await load_posts(pool)
        post_edges, post_stats = build_post_edges(posts)
        post_written = await replace_edges(pool, post_edges, origin=ORIGIN_POST)

        written = mem_written + post_written
        detail = (
            f"memory: {mem_stats.scanned} docs -> {mem_written} edges "
            f"({mem_stats.dangling} dangling); "
            f"posts: {post_stats.scanned} published -> {post_written} edges "
            f"({post_stats.dead_slug} dead slugs, "
            f"{post_stats.unpublished_target} unpublished targets)"
        )
        logger.info("[knowledge_edges] %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=written,
            # dangling / dead_slug are surfaced deliberately. In the memory
            # convention a link to a not-yet-written file marks work to do; a
            # post link to a slug that does not exist is a live 404 on the
            # public site. Both are signal, so a rising count should be
            # visible rather than swallowed.
            metrics={
                **{f"memory_{k}": v for k, v in mem_stats.as_dict().items()},
                **{f"post_{k}": v for k, v in post_stats.as_dict().items()},
                "written": written,
            },
        )


__all__ = ["ExtractKnowledgeEdgesJob"]
