"""``services.brain_knowledge_read`` — read helper for the MCP brain-knowledge tool.

Transport-agnostic thin layer over the ``brain_knowledge`` table.
Extracted from ``mcp-server/server.py`` per the transport-adapter contract
(``docs/architecture/2026-06-10-transport-adapter-contract.md``,
epic #1340 / guard #1344).
"""

from __future__ import annotations

from typing import Any

_SELECT_SQL = """
SELECT entity, attribute, value, confidence, source, tags, updated_at
FROM brain_knowledge
{where}
ORDER BY updated_at DESC
LIMIT ${limit_idx}
"""


async def query_knowledge(
    pool: Any,
    *,
    entity: str = "",
    attribute: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return brain-knowledge rows, optionally filtered by entity / attribute.

    ``entity`` is a case-sensitive LIKE match (``%entity%``).
    ``attribute`` is an exact equality match.
    ``limit`` is capped at 100.
    """
    limit = min(limit, 100)
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if entity:
        conditions.append(f"entity LIKE ${idx}")
        params.append(f"%{entity}%")
        idx += 1
    if attribute:
        conditions.append(f"attribute = ${idx}")
        params.append(attribute)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    sql = _SELECT_SQL.format(where=where, limit_idx=idx)
    rows = await pool.fetch(sql, *params)
    return [dict(r) for r in rows]


__all__ = ["query_knowledge"]
