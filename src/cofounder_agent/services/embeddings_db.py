"""
Embeddings Database Module

Handles all embedding-related database operations using pgvector including:
- Storing and retrieving embedding vectors
- Similarity search via cosine distance (<=>)
- Content hash deduplication to skip re-embedding unchanged content
"""

from datetime import datetime, timezone
from typing import Any

from asyncpg import Pool

from services.logger_config import get_logger

from .database_mixin import DatabaseServiceMixin

logger = get_logger(__name__)


class EmbeddingsDatabase(DatabaseServiceMixin):
    """Embedding-related database operations using pgvector."""

    def __init__(self, pool: Pool):
        """
        Initialize embeddings database module.

        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool

    async def store_embedding(
        self,
        source_type: str,
        source_id: str,
        content_hash: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
        embedding_model: str | None = None,
        text_preview: str | None = None,
        writer: str | None = None,
        chunk_index: int = 0,
    ) -> str:
        """
        Store an embedding vector in the database.

        Upserts on the natural key
        ``(source_table, source_id, chunk_index, embedding_model)`` so
        re-embedding the SAME chunk replaces the old vector while distinct
        chunks of one source coexist. Before #625 the INSERT omitted
        ``chunk_index`` (defaulting it to 0), so chunk 1 silently overwrote
        chunk 0 — every multi-chunk source collapsed to a single row.

        Args:
            source_type: Type of content (e.g. 'posts', 'brain_knowledge').
            source_id: Unique identifier for the source content.
            content_hash: SHA-256 hash of the content that was embedded.
            embedding: The embedding vector as a list of floats.
            metadata: Optional JSON metadata about the embedding.
            text_preview: First ~500 chars of the embedded text. Required by
                the DB schema — falls back to the title from `metadata` if
                omitted, then to the source_id.
            writer: Origin label (worker, auto-embed, claude-code, etc).
            chunk_index: Zero-based ordinal of this chunk within the source.
                Part of the natural key, so distinct chunks of one source
                must pass distinct values to avoid clobbering each other.

        Returns:
            The embedding row ID (string).
        """
        import json

        now = datetime.now(timezone.utc)
        vector_str = "[" + ",".join(str(v) for v in embedding) + "]"
        metadata_json = json.dumps(metadata) if metadata else None

        # text_preview is NOT NULL in the schema. Best-effort derive one
        # from any text-shaped metadata so old callers keep working (#198
        # follow-up — caught during the post-embedding backfill).
        if not text_preview:
            if metadata:
                text_preview = str(
                    metadata.get("title")
                    or metadata.get("preview")
                    or metadata.get("text")
                    or source_id
                )
            else:
                text_preview = source_id
        text_preview = (text_preview or "")[:500]

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO embeddings (source_table, source_id, content_hash,
                                            embedding, embedding_model, metadata,
                                            text_preview, writer,
                                            created_at, updated_at, chunk_index)
                    VALUES ($1, $2, $3, $4::vector, $5, $6::jsonb, $7, $8, $9, $10, $11)
                    ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
                    DO UPDATE SET content_hash = EXCLUDED.content_hash,
                                  embedding   = EXCLUDED.embedding,
                                  metadata    = EXCLUDED.metadata,
                                  text_preview = EXCLUDED.text_preview,
                                  writer      = COALESCE(EXCLUDED.writer, embeddings.writer),
                                  updated_at  = EXCLUDED.updated_at
                    RETURNING id
                    """,
                    source_type,
                    source_id,
                    content_hash,
                    vector_str,
                    embedding_model or "nomic-embed-text",
                    metadata_json,
                    text_preview,
                    writer,
                    now,
                    now,
                    chunk_index,
                )
            embedding_id = str(row["id"]) if row else None
            logger.info(
                "Stored embedding",
                source_type=source_type,
                source_id=source_id,
                dimensions=len(embedding),
            )
            return embedding_id  # type: ignore[return-value]
        except Exception as e:
            logger.error(
                "[store_embedding] Failed to store embedding: %s",
                e,
                exc_info=True,
                source_type=source_type,
                source_id=source_id,
            )
            raise

    async def search_similar(
        self,
        embedding: list[float],
        limit: int = 10,
        source_type: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """
        Search for similar embeddings using cosine similarity.

        Args:
            embedding: Query embedding vector.
            limit: Maximum number of results.
            source_type: Optional filter by source type.
            min_similarity: Minimum similarity threshold (0-1).

        Returns:
            List of dicts with source_type, source_id, similarity, metadata.
        """
        vector_str = "[" + ",".join(str(v) for v in embedding) + "]"

        try:
            async with self.pool.acquire() as conn:
                if source_type:
                    rows = await conn.fetch(
                        """
                        SELECT source_table, source_id, content_hash, metadata,
                               1 - (embedding <=> $1::vector) as similarity
                        FROM embeddings
                        WHERE source_table = $2
                          AND 1 - (embedding <=> $1::vector) >= $3
                        ORDER BY embedding <=> $1::vector
                        LIMIT $4
                        """,
                        vector_str,
                        source_type,
                        min_similarity,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT source_table, source_id, content_hash, metadata,
                               1 - (embedding <=> $1::vector) as similarity
                        FROM embeddings
                        WHERE 1 - (embedding <=> $1::vector) >= $2
                        ORDER BY embedding <=> $1::vector
                        LIMIT $3
                        """,
                        vector_str,
                        min_similarity,
                        limit,
                    )

            results = [self._convert_row_to_dict(row) for row in rows]
            logger.info(
                "Similarity search complete",
                results=len(results),
                source_type=source_type,
            )
            return results
        except Exception as e:
            logger.error(
                "[search_similar] Similarity search failed: %s", e, exc_info=True
            )
            return []

    async def get_embedding(
        self,
        source_type: str,
        source_id: str,
        embedding_model: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Get a specific embedding by source type and ID.

        The natural key is
        ``(source_table, source_id, chunk_index, embedding_model)``, so a
        bare ``(source_table, source_id)`` filter can match multiple rows
        (one per chunk / model). Before #626 the bare ``fetchrow`` returned
        a non-deterministic row in that case. We now ``ORDER BY chunk_index,
        embedding_model LIMIT 1`` for a stable result (the first chunk of
        the earliest model), and accept an optional ``embedding_model``
        filter to pin a specific model.

        Args:
            source_type: Type of content.
            source_id: Source content identifier.
            embedding_model: Optional model filter. ``None`` = any model.

        Returns:
            Dict with embedding data, or None if not found.
        """
        try:
            async with self.pool.acquire() as conn:
                if embedding_model is not None:
                    row = await conn.fetchrow(
                        """
                        SELECT id, source_table, source_id, content_hash, metadata,
                               created_at, updated_at
                        FROM embeddings
                        WHERE source_table = $1 AND source_id = $2
                          AND embedding_model = $3
                        ORDER BY chunk_index, embedding_model
                        LIMIT 1
                        """,
                        source_type,
                        source_id,
                        embedding_model,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT id, source_table, source_id, content_hash, metadata,
                               created_at, updated_at
                        FROM embeddings
                        WHERE source_table = $1 AND source_id = $2
                        ORDER BY chunk_index, embedding_model
                        LIMIT 1
                        """,
                        source_type,
                        source_id,
                    )
            if row:
                return self._convert_row_to_dict(row)
            return None
        except Exception as e:
            logger.error(
                "[get_embedding] Failed to get embedding: %s",
                e,
                exc_info=True,
                source_type=source_type,
                source_id=source_id,
            )
            return None

    async def delete_embeddings(
        self, source_type: str, source_id: str | None = None
    ) -> int:
        """
        Delete embeddings by source type and optionally source ID.

        Args:
            source_type: Type of content to delete embeddings for.
            source_id: Optional specific source ID. If None, deletes all for source_type.

        Returns:
            Number of rows deleted.
        """
        try:
            async with self.pool.acquire() as conn:
                if source_id:
                    result = await conn.execute(
                        "DELETE FROM embeddings WHERE source_table = $1 AND source_id = $2",
                        source_type,
                        source_id,
                    )
                else:
                    result = await conn.execute(
                        "DELETE FROM embeddings WHERE source_table = $1",
                        source_type,
                    )
            # asyncpg returns "DELETE N"
            deleted = int(result.split()[-1])
            logger.info(
                "Deleted embeddings",
                source_type=source_type,
                source_id=source_id,
                deleted=deleted,
            )
            return deleted
        except Exception as e:
            logger.error(
                "[delete_embeddings] Failed to delete embeddings: %s",
                e,
                exc_info=True,
                source_type=source_type,
            )
            return 0

    async def needs_reembedding(
        self,
        source_type: str,
        source_id: str,
        content_hash: str,
        embedding_model: str | None = None,
    ) -> bool:
        """
        Check if content needs re-embedding by comparing content hashes.

        The natural key includes ``chunk_index`` + ``embedding_model``, so a
        bare ``(source_table, source_id)`` lookup can match several rows.
        Before #626 the bare ``fetchrow`` compared against a
        non-deterministic row's hash. We now ``ORDER BY chunk_index,
        embedding_model LIMIT 1`` for a stable comparison and accept an
        optional ``embedding_model`` filter.

        Args:
            source_type: Type of content.
            source_id: Source content identifier.
            content_hash: SHA-256 hash of the current content.
            embedding_model: Optional model filter. ``None`` = any model.

        Returns:
            True if content needs re-embedding (no existing embedding or hash differs).
        """
        try:
            async with self.pool.acquire() as conn:
                if embedding_model is not None:
                    row = await conn.fetchrow(
                        """
                        SELECT content_hash FROM embeddings
                        WHERE source_table = $1 AND source_id = $2
                          AND embedding_model = $3
                        ORDER BY chunk_index, embedding_model
                        LIMIT 1
                        """,
                        source_type,
                        source_id,
                        embedding_model,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT content_hash FROM embeddings
                        WHERE source_table = $1 AND source_id = $2
                        ORDER BY chunk_index, embedding_model
                        LIMIT 1
                        """,
                        source_type,
                        source_id,
                    )
            if row is None:
                return True
            return row["content_hash"] != content_hash
        except Exception as e:
            logger.error(
                "[needs_reembedding] Failed to check embedding status: %s",
                e,
                exc_info=True,
            )
            # If we can't check, assume re-embedding is needed
            return True
