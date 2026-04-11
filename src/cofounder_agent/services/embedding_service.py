"""
Embedding Service

Orchestrator that coordinates embedding generation (via OllamaClient) with
storage and deduplication (via EmbeddingsDatabase). Provides high-level
methods for embedding posts and brain knowledge triples.
"""

import hashlib
from typing import Any, Dict, List, Optional

from services.logger_config import get_logger

from .embeddings_db import EmbeddingsDatabase
from .ollama_client import OllamaClient

logger = get_logger(__name__)


class EmbeddingService:
    """
    High-level embedding orchestrator.

    Combines OllamaClient (vector generation) with EmbeddingsDatabase
    (storage and similarity search). Uses SHA-256 content hashing to
    skip re-embedding unchanged content.
    """

    def __init__(self, ollama_client: OllamaClient, embeddings_db: EmbeddingsDatabase):
        """
        Initialize embedding service.

        Args:
            ollama_client: OllamaClient instance for generating embeddings.
            embeddings_db: EmbeddingsDatabase instance for storage and search.
        """
        self.ollama = ollama_client
        self.db = embeddings_db

    @staticmethod
    def _content_hash(text: str) -> str:
        """Generate SHA-256 hash of content for deduplication."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def embed_post(self, post_dict: dict[str, Any]) -> str | None:
        """
        Embed a blog post by combining title, excerpt, and truncated content.

        Skips re-embedding if the content hash has not changed.

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
            if not await self.db.needs_reembedding("post", post_id, content_hash):
                logger.info("Skipping post embedding (unchanged)", post_id=post_id)
                return None

            embedding = await self.ollama.embed(combined)
            embedding_id = await self.db.store_embedding(
                source_type="post",
                source_id=post_id,
                content_hash=content_hash,
                embedding=embedding,
                metadata={"title": title},
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

            embedding = await self.ollama.embed(combined)
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
                if not await self.db.needs_reembedding("post", post_id, content_hash):
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

        # Batch embed all texts at once
        try:
            embeddings = await self.ollama.embed_batch(combined_texts)
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
                    source_type="post",
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
