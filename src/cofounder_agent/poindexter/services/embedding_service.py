"""
Embedding Service

Orchestrator that coordinates embedding generation (via an LLMProvider) with
storage and deduplication (via EmbeddingsDatabase). Provides high-level
methods for embedding posts and brain knowledge triples.

## Migration note (v2.2b, 2026-04-20)

The ctor used to take ``ollama_client: OllamaClient``. It now takes an
``LLMProvider`` instance instead — callers get one via::

    from plugins.registry import get_llm_providers
    provider = {p.name: p for p in get_llm_providers()}["ollama_native"]

Batch embedding (``embed_all_posts``) uses the provider's
``embed_batch`` method when available (optional, not part of the
Protocol). Providers without it fall through to a per-text loop of
``embed()`` calls. It batches every CHUNK of every post in one call and
hands each post back its own slice, so a multi-chunk post costs one
round-trip rather than one per chunk.
"""

import hashlib
from typing import Any

from plugins.llm_provider import LLMProvider
from services.logger_config import get_logger
from services.taps._chunking import chunk_text as split_into_chunks
from services.taps._chunking import content_hash as chunk_content_hash
from services.taps.published_posts import build_post_text

from .embeddings_db import EmbeddingsDatabase

logger = get_logger(__name__)


class EmbeddingService:
    """
    High-level embedding orchestrator.

    Combines an ``LLMProvider`` (vector generation via the plugin
    Protocol) with ``EmbeddingsDatabase`` (storage and similarity
    search). Uses SHA-256 content hashing to skip re-embedding
    unchanged content.
    """

    def __init__(
        self,
        provider: LLMProvider,
        embeddings_db: EmbeddingsDatabase,
        embed_model: str = "nomic-embed-text",
    ):
        """
        Initialize embedding service.

        Args:
            provider: LLMProvider for generating embeddings (typically
                the ``ollama_native`` provider from the registry).
            embeddings_db: EmbeddingsDatabase for storage and search.
            embed_model: Embedding model name; passed to the provider
                on each embed call.
        """
        self.provider = provider
        self.db = embeddings_db
        self.embed_model = embed_model

    async def _embed_one(self, text: str) -> list[float]:
        """Single-text embed via the provider."""
        return await self.provider.embed(text, model=self.embed_model)

    async def _embed_many(self, texts: list[str]) -> list[list[float]]:
        """Batch embed, falling back to a per-text loop if the provider
        doesn't expose ``embed_batch``."""
        batch_fn = getattr(self.provider, "embed_batch", None)
        if batch_fn:
            return await batch_fn(texts, model=self.embed_model)
        return [await self._embed_one(t) for t in texts]

    @staticmethod
    def _content_hash(text: str) -> str:
        """Generate SHA-256 hash of content for deduplication."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def _store_chunked(
        self,
        *,
        source_type: str,
        source_id: str,
        text: str,
        full_hash: str,
        metadata: dict[str, Any],
        writer: str,
        embeddings: list[list[float]] | None = None,
        chunks: list[str] | None = None,
    ) -> str | None:
        """Chunk ``text``, embed each chunk, and upsert one row per chunk.

        Uses the same rules as the tap runner — ``_chunking.chunk_text`` for
        the split, the full-document hash on chunk 0 (so either writer's
        dedup check sees the same value) and per-chunk hashes after it — then
        prunes chunks left over from a longer previous version.

        Returns the chunk-0 row id.
        """
        chunks = chunks if chunks is not None else split_into_chunks(text)
        if embeddings is None:
            # Single chunk is the common case (a short post, a knowledge
            # triple); keep it on the plain embed() call rather than routing
            # a one-element list through the provider's batch endpoint.
            embeddings = (
                [await self._embed_one(chunks[0])]
                if len(chunks) == 1
                else await self._embed_many(chunks)
            )

        first_id: str | None = None
        for idx, (chunk, vector) in enumerate(zip(chunks, embeddings, strict=True)):
            row_id = await self.db.store_embedding(
                source_type=source_type,
                source_id=source_id,
                content_hash=full_hash if idx == 0 else chunk_content_hash(chunk),
                embedding=vector,
                embedding_model=self.embed_model,
                metadata={
                    **metadata,
                    "chars": len(text),
                    "total_chunks": len(chunks),
                    "chunk_index": idx,
                },
                text_preview=chunk[:500],
                chunk_text=chunk,
                writer=writer,
                chunk_index=idx,
            )
            if idx == 0:
                first_id = row_id

        await self.db.delete_stale_chunks(
            source_type, source_id, self.embed_model, len(chunks)
        )
        return first_id

    async def embed_post(self, post_dict: dict[str, Any]) -> str | None:
        """
        Embed a blog post: title + excerpt + content, chunked like the tap.

        Skips re-embedding if the content hash has not changed.

        Historically this method wrote to source_table='post' (singular),
        but every reader in the codebase queries source_table='posts'
        (plural — see MemoryClient.find_similar_posts, topic_executor
        semantic dedup, pgvector ideation). That schema mismatch meant
        every published post after auto-embed stopped running was
        invisible to RAG. Unified on plural now (#198 follow-up).

        This is the LOW-LATENCY path: ``publish_service`` calls it at publish
        time so a post is searchable immediately instead of waiting up to an
        hour for ``PostsTap``. Both write the same natural key, so both must
        build the same text and chunk it the same way — see
        ``build_post_text``.

        Until this was fixed, they did not. This method wrote a single
        2000-char whole-post truncation at chunk 0 with a hash the tap never
        produced, so the two writers took turns overwriting each other on
        every republish and chunk 0 spent part of every hour holding a
        truncation instead of a chunk. poindexter#1033 made that truncation
        the row's ``chunk_text`` too, which fixed the payload but left the
        flapping — one builder and one chunking rule removes it: the second
        writer to arrive now computes the same hash, dedups, and skips.

        Args:
            post_dict: Dict with keys 'id', 'title', 'excerpt', 'content'.

        Returns:
            Embedding row ID of chunk 0 if stored, None if skipped (unchanged).
        """
        post_id = str(post_dict.get("id", ""))
        title = post_dict.get("title", "")
        excerpt = post_dict.get("excerpt", "")
        content = post_dict.get("content", "")

        combined = build_post_text(title=title, excerpt=excerpt, content=content)
        if not combined.strip():
            logger.info("Skipping post embedding (no text)", post_id=post_id)
            return None
        content_hash = self._content_hash(combined)

        try:
            if not await self.db.needs_reembedding(
                "posts", post_id, content_hash, embedding_model=self.embed_model
            ):
                logger.info("Skipping post embedding (unchanged)", post_id=post_id)
                return None

            embedding_id = await self._store_chunked(
                source_type="posts",
                source_id=post_id,
                text=combined,
                full_hash=content_hash,
                metadata={"title": title},
                writer="worker",
            )

            logger.info("Embedded post", post_id=post_id, title=title)
            return embedding_id

        except Exception as e:
            logger.error(
                "[embed_post] Failed to embed post: %s",
                e,
                exc_info=True,
                post_id=post_id,
            )
            raise

    async def embed_brain_knowledge(
        self, entity: str, attribute: str, value: str
    ) -> str | None:
        """
        Embed a brain knowledge triple (entity, attribute, value).

        Args:
            entity: The entity name (e.g. 'Glad Labs').
            attribute: The attribute (e.g. 'mission').
            value: The value (e.g. 'democratize AI for small businesses').

        Returns:
            Embedding row ID if stored, None if skipped (unchanged).
        """
        source_id = f"{entity}::{attribute}"
        combined = f"{entity} {attribute}: {value}"
        content_hash = self._content_hash(combined)

        try:
            if not await self.db.needs_reembedding("brain_knowledge", source_id, content_hash):
                logger.info(
                    "Skipping brain knowledge embedding (unchanged)",
                    entity=entity,
                    attribute=attribute,
                )
                return None

            embedding = await self._embed_one(combined)
            embedding_id = await self.db.store_embedding(
                source_type="brain_knowledge",
                source_id=source_id,
                content_hash=content_hash,
                embedding=embedding,
                embedding_model=self.embed_model,
                metadata={"entity": entity, "attribute": attribute},
                # A triple is one short line; it never chunks. Passing it
                # anyway keeps the retrieval payload populated for every
                # writer rather than only the ones that happen to be long.
                text_preview=combined[:500],
                chunk_text=combined,
                writer="worker",
            )

            logger.info(
                "Embedded brain knowledge",
                entity=entity,
                attribute=attribute,
            )
            return embedding_id

        except Exception as e:
            logger.error(
                "[embed_brain_knowledge] Failed to embed knowledge: %s",
                e,
                exc_info=True,
                entity=entity,
                attribute=attribute,
            )
            raise

    async def embed_all_posts(self, posts: list[dict[str, Any]]) -> dict[str, int]:
        """
        Batch embed all published posts (for initial migration).

        Uses batch embedding for efficiency, but still checks content hashes
        individually to skip unchanged posts.

        Args:
            posts: List of post dicts with 'id', 'title', 'excerpt', 'content'.

        Returns:
            Dict with counts: {'embedded': N, 'skipped': N, 'failed': N}.
        """
        embedded = 0
        skipped = 0
        failed = 0

        # First pass: determine which posts need embedding. Text + hash come
        # from the same builder ``embed_post`` and ``PostsTap`` use, so all
        # three agree on what chunk 0 of a post contains.
        to_embed: list[dict[str, Any]] = []
        combined_texts: list[str] = []
        post_chunks: list[list[str]] = []
        content_hashes: list[str] = []

        for post in posts:
            post_id = str(post.get("id", ""))
            combined = build_post_text(
                title=post.get("title", ""),
                excerpt=post.get("excerpt", ""),
                content=post.get("content", ""),
            )
            if not combined.strip():
                skipped += 1
                continue
            content_hash = self._content_hash(combined)

            try:
                if not await self.db.needs_reembedding(
                    "posts", post_id, content_hash, embedding_model=self.embed_model
                ):
                    skipped += 1
                    continue
            except Exception:
                logger.debug("[EMBED] needs_reembedding check failed for post %s", post_id, exc_info=True)
                skipped += 1
                continue

            to_embed.append(post)
            combined_texts.append(combined)
            post_chunks.append(split_into_chunks(combined))
            content_hashes.append(content_hash)

        if not to_embed:
            logger.info(
                "All posts already embedded",
                total=len(posts),
                skipped=skipped,
            )
            return {"embedded": 0, "skipped": skipped, "failed": 0}

        # Batch embed every chunk of every post in one call (falls back to a
        # loop inside _embed_many when the provider doesn't expose
        # embed_batch), then hand each post back its own slice.
        flat_chunks = [chunk for chunks in post_chunks for chunk in chunks]
        try:
            flat_vectors = await self._embed_many(flat_chunks)
        except Exception as e:
            logger.error(
                "[embed_all_posts] Batch embedding failed: %s", e, exc_info=True
            )
            return {"embedded": 0, "skipped": skipped, "failed": len(to_embed)}

        # Store each embedding
        cursor = 0
        for i, post in enumerate(to_embed):
            post_id = str(post.get("id", ""))
            title = post.get("title", "")
            chunks = post_chunks[i]
            vectors = flat_vectors[cursor : cursor + len(chunks)]
            cursor += len(chunks)
            try:
                await self._store_chunked(
                    source_type="posts",
                    source_id=post_id,
                    text=combined_texts[i],
                    full_hash=content_hashes[i],
                    metadata={"title": title},
                    writer="worker",
                    chunks=chunks,
                    embeddings=vectors,
                )
                embedded += 1
            except Exception as e:
                logger.error(
                    "[embed_all_posts] Failed to store embedding for post %s: %s",
                    post_id,
                    e,
                    exc_info=True,
                )
                failed += 1

        logger.info(
            "Batch post embedding complete",
            embedded=embedded,
            skipped=skipped,
            failed=failed,
            total=len(posts),
        )
        return {"embedded": embedded, "skipped": skipped, "failed": failed}
