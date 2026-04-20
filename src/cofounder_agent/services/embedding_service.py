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
``embed()`` calls.
"""

import hashlib
from typing import Any

from plugins.llm_provider import LLMProvider
from services.logger_config import get_logger

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

    async def embed_post(self, post_dict: dict[str, Any]) -> str | None:
        """
        Embed a blog post by combining title, excerpt, and truncated content.

        Skips re-embedding if the content hash has not changed.

        Historically this method wrote to source_table='post' (singular),
        but every reader in the codebase queries source_table='posts'
        (plural — see MemoryClient.find_similar_posts, topic_executor
        semantic dedup, pgvector ideation). That schema mismatch meant
        every published post after auto-embed stopped running was
        invisible to RAG. Unified on plural now (#198 follow-up).

        Args:
            post_dict: Dict with keys 'id', 'title', 'excerpt', 'content'.

        Returns:
            Embedding row ID if stored, None if skipped (unchanged).
        """
        post_id = str(post_dict.get("id", ""))
        title = post_dict.get("title", "")
        excerpt = post_dict.get("excerpt", "")
        content = post_dict.get("content", "")

        # Combine fields for embedding — truncate content to 2000 chars
        combined = f"{title}\n{excerpt}\n{content[:2000]}"
        content_hash = self._content_hash(combined)

        try:
            if not await self.db.needs_reembedding("posts", post_id, content_hash):
                logger.info("Skipping post embedding (unchanged)", post_id=post_id)
                return None

            embedding = await self._embed_one(combined)
            embedding_id = await self.db.store_embedding(
                source_type="posts",
                source_id=post_id,
                content_hash=content_hash,
                embedding=embedding,
                metadata={"title": title},
                text_preview=combined[:500],
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
                metadata={"entity": entity, "attribute": attribute},
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

        # First pass: determine which posts need embedding
        to_embed: list[dict[str, Any]] = []
        combined_texts: list[str] = []
        content_hashes: list[str] = []

        for post in posts:
            post_id = str(post.get("id", ""))
            title = post.get("title", "")
            excerpt = post.get("excerpt", "")
            content = post.get("content", "")

            combined = f"{title}\n{excerpt}\n{content[:2000]}"
            content_hash = self._content_hash(combined)

            try:
                if not await self.db.needs_reembedding("posts", post_id, content_hash):
                    skipped += 1
                    continue
            except Exception:
                logger.debug("[EMBED] needs_reembedding check failed for post %s", post_id, exc_info=True)
                skipped += 1
                continue

            to_embed.append(post)
            combined_texts.append(combined)
            content_hashes.append(content_hash)

        if not to_embed:
            logger.info(
                "All posts already embedded",
                total=len(posts),
                skipped=skipped,
            )
            return {"embedded": 0, "skipped": skipped, "failed": 0}

        # Batch embed all texts at once (falls back to a loop inside
        # _embed_many when the provider doesn't expose embed_batch).
        try:
            embeddings = await self._embed_many(combined_texts)
        except Exception as e:
            logger.error(
                "[embed_all_posts] Batch embedding failed: %s", e, exc_info=True
            )
            return {"embedded": 0, "skipped": skipped, "failed": len(to_embed)}

        # Store each embedding
        for i, post in enumerate(to_embed):
            post_id = str(post.get("id", ""))
            title = post.get("title", "")
            try:
                await self.db.store_embedding(
                    source_type="posts",
                    source_id=post_id,
                    content_hash=content_hashes[i],
                    embedding=embeddings[i],
                    metadata={"title": title},
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
