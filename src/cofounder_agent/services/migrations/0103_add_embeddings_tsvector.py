"""Migration 0103: tsvector column + GIN index on embeddings (Phase C / #210).

Adds BM25-shaped lexical search alongside the existing pgvector cosine
similarity. The hybrid retrieval layer in ``services/rag_engine.py``
combines the two via Reciprocal Rank Fusion (RRF) so paraphrased queries
that miss in vector space can still match by keyword.

Design choices
--------------

- ``GENERATED ALWAYS AS`` column — Postgres 12+ keeps the tsvector
  in sync with ``text_preview`` automatically, no triggers needed.
  Postgres 16 (our deployed version) supports this fully.
- ``GIN`` index — the canonical lexical-search index type in
  Postgres. ``USING GIN(text_search)`` covers ``@@ to_tsquery(...)``
  and ``ts_rank()`` lookups.
- ``simple`` config (not ``english``) — preserves brand names,
  product tags, and code identifiers that ``english`` stemming would
  collapse ("Cursor" → "cursor", "FastAPI" → "fastapi"). The hybrid
  layer's vector half handles semantic equivalence; tsvector handles
  exact-keyword recall.
- Scoped to ``embeddings`` table — that's where the RAG layer queries
  from. ``posts.title`` and other lexical surfaces could get the same
  treatment later if pure-keyword queries against post titles become a
  use case.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # The ``embeddings`` table is created by
        # ``infrastructure/local-db/init.sql`` at container startup, NOT
        # by a migration (predates the migration runner). On fresh CI
        # databases without init.sql (the migration smoke test, GH-229),
        # the table is absent — so this column-add migration has nothing
        # to operate on. Same defensive pattern as migration 0075
        # (``_table_exists`` skip).
        table_exists = await conn.fetchval(
            "SELECT 1 FROM pg_tables WHERE schemaname='public' "
            "AND tablename='embeddings'"
        )
        if not table_exists:
            logger.info(
                "0103: embeddings table missing (fresh DB without init.sql) — "
                "skipping tsvector column + GIN index. Will run on next "
                "deploy after the table is created."
            )
            return

        # Idempotent — IF NOT EXISTS guards both the column and index.
        await conn.execute(
            """
            ALTER TABLE embeddings
            ADD COLUMN IF NOT EXISTS text_search tsvector
                GENERATED ALWAYS AS (
                    to_tsvector('simple', COALESCE(text_preview, ''))
                ) STORED
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_embeddings_text_search
            ON embeddings USING GIN(text_search)
            """
        )
        logger.info("0103: added embeddings.text_search tsvector + GIN index")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DROP INDEX IF EXISTS idx_embeddings_text_search"
        )
        await conn.execute(
            "ALTER TABLE embeddings DROP COLUMN IF EXISTS text_search"
        )
        logger.info("0103: dropped embeddings.text_search column + index")
