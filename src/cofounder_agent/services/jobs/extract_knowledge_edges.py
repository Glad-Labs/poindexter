"""ExtractKnowledgeEdgesJob — keep ``knowledge_edges`` current (poindexter#1035).

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
    build_edges,
    load_memory_docs,
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
        edges, stats = build_edges(docs)
        written = await replace_edges(pool, edges, origin=ORIGIN_MEMORY)

        detail = (
            f"scanned {stats.scanned} memory docs -> {written} edges "
            f"({stats.dangling} dangling, {stats.self_links} self-links)"
        )
        logger.info("[knowledge_edges] %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=written,
            # dangling is surfaced deliberately: in the memory convention a link
            # to a not-yet-written file marks work to do, so a rising count is
            # signal rather than error.
            metrics={**stats.as_dict(), "written": written},
        )


__all__ = ["ExtractKnowledgeEdgesJob"]
