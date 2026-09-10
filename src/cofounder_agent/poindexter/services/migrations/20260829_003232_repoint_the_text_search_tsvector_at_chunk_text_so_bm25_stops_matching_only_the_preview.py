"""Migration 20260829_003232: point ``text_search`` at the full chunk payload.

ISSUE: Glad-Labs/poindexter#1033 (follow-up)

`20260829_001056` added ``embeddings.chunk_text`` and routed the *vector*
retriever's payload through it, but left ``text_search`` — the generated
tsvector the hybrid retriever's BM25 half matches against — defined over
``text_preview`` alone. So the lexical half kept the exact blind spot the
vector half was just cured of: a document whose matching term sits past
character 500 is unfindable by BM25, no matter how well `chunk_text` now
serves the consumer that finally receives it.

Concretely: with a term planted at character 775,
``websearch_to_tsquery('simple', <term>)`` returned zero rows before this
migration and one after. That is the whole change.

Redefining a generation expression needs drop + re-add, which rewrites the
column and rebuilds the GIN index. ``COALESCE(chunk_text, text_preview)``
keeps rows the backfill has not reached indexing exactly as they do today.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_GENERATED = """
    ALTER TABLE embeddings
      ADD COLUMN text_search tsvector
      GENERATED ALWAYS AS (
          to_tsvector('simple'::regconfig, {expr})
      ) STORED
"""

_PAYLOAD_EXPR = (
    "COALESCE(chunk_text, COALESCE(text_preview, ''::character varying)::text)"
)
_PREVIEW_EXPR = "COALESCE(text_preview, ''::character varying)::text"


async def _generation_expr(conn) -> str | None:
    return await conn.fetchval(
        """
        SELECT pg_get_expr(d.adbin, d.adrelid)
          FROM pg_attribute a
          JOIN pg_attrdef d
            ON d.adrelid = a.attrelid AND d.adnum = a.attnum
         WHERE a.attrelid = 'public.embeddings'::regclass
           AND a.attname = 'text_search'
           AND NOT a.attisdropped
        """
    )


async def _rebuild(conn, expr: str) -> None:
    await conn.execute("DROP INDEX IF EXISTS idx_embeddings_text_search")
    await conn.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS text_search")
    await conn.execute(_GENERATED.format(expr=expr))
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_embeddings_text_search "
        "ON public.embeddings USING gin (text_search)"
    )


async def up(pool) -> None:
    """Redefine ``text_search`` over ``COALESCE(chunk_text, text_preview)``."""
    async with pool.acquire() as conn:
        # Guard on the EXPRESSION, not the column name: the column always
        # exists, so an unguarded re-run would drop the new definition and
        # rebuild the old one — a silent revert that looks like success.
        current = await _generation_expr(conn)
        if current and "chunk_text" in current:
            logger.info(
                "Migration repoint_text_search: already covers chunk_text; "
                "nothing to rebuild"
            )
            return
        await _rebuild(conn, _PAYLOAD_EXPR)
    logger.info(
        "Migration repoint_text_search: applied — BM25 now matches the full "
        "chunk, not the 500-char preview"
    )


async def down(pool) -> None:
    """Restore the preview-only tsvector."""
    async with pool.acquire() as conn:
        current = await _generation_expr(conn)
        if current and "chunk_text" not in current:
            logger.info("Migration repoint_text_search: already reverted")
            return
        await _rebuild(conn, _PREVIEW_EXPR)
    logger.info("Migration repoint_text_search: reverted")
