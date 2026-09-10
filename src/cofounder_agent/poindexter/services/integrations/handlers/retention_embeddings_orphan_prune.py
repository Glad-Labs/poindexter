"""Handler: ``retention.embeddings_orphan_prune``.

Deletes embeddings whose source row no longer exists.  Each source table
stores embeddings with a ``source_table`` discriminator and a ``source_id``
that must resolve back to the originating row.  When the source row is
deleted the embedding becomes an orphan and wastes vector-index space.

Config keys (from the ``config`` JSONB column on the retention_policies row):
- ``source_table`` (str, required) — one of ``posts``, ``audit``, ``brain``.
- ``batch_size`` (int, default 1000) — max rows per DELETE pass.

Each source has its own join pattern because the ``source_id`` format differs:
- ``posts``:  plain UUID, joined as ``p.id::text = e.source_id``.
- ``audit``:  integer PK, joined as ``a.id::text = e.source_id``.
- ``brain``:  compound key ``brain_decisions/<int_id>``, joined via
             ``split_part(e.source_id, '/', 2)::bigint = b.id``.

SQL is built from a fixed template and operator-controlled config; there is
no user-facing string interpolation.
"""

from __future__ import annotations

import logging
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 1000


async def _delete_orphan_posts(conn: Any, batch_size: int) -> int:
    result = await conn.execute(
        """
        DELETE FROM embeddings
         WHERE id IN (
             SELECT e.id
               FROM embeddings e
               LEFT JOIN posts p ON p.id::text = e.source_id
              WHERE e.source_table = 'posts'
                AND p.id IS NULL
              LIMIT $1
         )
        """,
        batch_size,
    )
    return int(result.rsplit(" ", 1)[-1])


async def _delete_orphan_audit(conn: Any, batch_size: int) -> int:
    result = await conn.execute(
        """
        DELETE FROM embeddings
         WHERE id IN (
             SELECT e.id
               FROM embeddings e
               LEFT JOIN audit_log a ON a.id::text = e.source_id
              WHERE e.source_table = 'audit'
                AND a.id IS NULL
              LIMIT $1
         )
        """,
        batch_size,
    )
    return int(result.rsplit(" ", 1)[-1])


async def _delete_orphan_brain(conn: Any, batch_size: int) -> int:
    result = await conn.execute(
        """
        DELETE FROM embeddings
         WHERE id IN (
             SELECT e.id
               FROM embeddings e
               LEFT JOIN brain_decisions b
                      ON b.id::text = split_part(e.source_id, '/', 2)
              WHERE e.source_table = 'brain'
                AND e.source_id LIKE 'brain_decisions/%'
                AND b.id IS NULL
              LIMIT $1
         )
        """,
        batch_size,
    )
    return int(result.rsplit(" ", 1)[-1])


_SOURCE_HANDLERS = {
    "posts": _delete_orphan_posts,
    "audit": _delete_orphan_audit,
    "brain": _delete_orphan_brain,
}


@register_handler("retention", "embeddings_orphan_prune")
async def embeddings_orphan_prune(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Delete embeddings whose parent row in ``source_table`` no longer exists."""
    if pool is None:
        raise RuntimeError("retention.embeddings_orphan_prune: pool unavailable")

    config = row.get("config") or {}
    if not isinstance(config, dict):
        config = {}

    source_table = config.get("source_table")
    if not source_table:
        raise ValueError(
            "retention.embeddings_orphan_prune: config.source_table is required"
        )

    handler_fn = _SOURCE_HANDLERS.get(source_table)
    if handler_fn is None:
        raise ValueError(
            f"retention.embeddings_orphan_prune: no handler for source_table={source_table!r}. "
            f"Supported: {sorted(_SOURCE_HANDLERS)}"
        )

    batch_size = int(config.get("batch_size") or _DEFAULT_BATCH_SIZE)

    async with pool.acquire() as conn:
        deleted = await handler_fn(conn, batch_size)

    logger.info(
        "[retention.embeddings_orphan_prune] %s: deleted %d orphan embeddings "
        "(source_table=%s, batch_size=%d)",
        row.get("name"), deleted, source_table, batch_size,
    )
    return {"deleted": deleted, "source_table": source_table, "batch_size": batch_size}
