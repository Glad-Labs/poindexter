"""
Unit tests for services/embedding_service.py

Tests EmbeddingService orchestration: embed_post, embed_brain_knowledge,
embed_all_posts (batch), deduplication via content hashing, and error handling.
All external dependencies (LLMProvider, EmbeddingsDatabase) are mocked.

## v2.2b migration

The ``ollama`` fixture now stands in for an ``LLMProvider`` — mocks
``embed()`` and optionally ``embed_batch()``. EmbeddingService no
longer holds an OllamaClient reference.
"""

import hashlib
from unittest.mock import AsyncMock

import pytest

from services.embedding_service import EmbeddingService
from services.taps.published_posts import build_post_text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_EMBEDDING = [0.1, 0.2, 0.3, 0.4, 0.5]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_post(post_id=1, title="Test Title", excerpt="Test excerpt", content="Test content"):
    return {"id": post_id, "title": title, "excerpt": excerpt, "content": content}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ollama():
    """Mock LLMProvider — named ``ollama`` for historical reasons; in
    v2.2b it's actually a Provider Protocol mock, not an OllamaClient."""
    mock = AsyncMock()
    mock.embed = AsyncMock(return_value=SAMPLE_EMBEDDING)
    # One vector per input text, whatever the batch size — the chunker
    # decides how many chunks a post makes, so a fixed-length return would
    # pin the test to today's chunk boundaries.
    mock.embed_batch = AsyncMock(
        side_effect=lambda texts, **_kw: [SAMPLE_EMBEDDING for _ in texts]
    )
    return mock


@pytest.fixture
def embeddings_db():
    mock = AsyncMock()
    mock.needs_reembedding = AsyncMock(return_value=True)
    mock.store_embedding = AsyncMock(return_value="emb-uuid-123")
    return mock


@pytest.fixture
def service(ollama, embeddings_db):
    return EmbeddingService(provider=ollama, embeddings_db=embeddings_db)


# ---------------------------------------------------------------------------
# _content_hash
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_deterministic(self):
        assert EmbeddingService._content_hash("hello") == _content_hash("hello")

    def test_different_input_different_hash(self):
        assert EmbeddingService._content_hash("a") != EmbeddingService._content_hash("b")


# ---------------------------------------------------------------------------
# embed_post
# ---------------------------------------------------------------------------


class TestEmbedPost:
    @pytest.mark.asyncio
    async def test_embeds_new_post(self, service, ollama, embeddings_db):
        post = _make_post()
        result = await service.embed_post(post)

        assert result == "emb-uuid-123"
        ollama.embed.assert_awaited_once()
        embeddings_db.store_embedding.assert_awaited_once()

        # Verify store_embedding was called with correct source_type.
        # #198: unified on plural 'posts' so RAG can query the same
        # namespace that auto-embed writes to.
        call_kwargs = embeddings_db.store_embedding.call_args
        assert call_kwargs.kwargs["source_type"] == "posts"
        assert call_kwargs.kwargs["source_id"] == "1"
        # Chunk bookkeeping rides alongside the caller's metadata, matching
        # what the tap runner writes for the same source.
        assert call_kwargs.kwargs["metadata"] == {
            "title": "Test Title", "chars": 40, "total_chunks": 1, "chunk_index": 0,
        }
        # text_preview + writer added in the NOT NULL schema fix — verify
        # they're populated rather than dropped.
        assert call_kwargs.kwargs["text_preview"]  # non-empty
        assert call_kwargs.kwargs["writer"] == "worker"
        # chunk_text is the retrieval payload — the whole chunk, not the
        # 500-char display slice. A row written without it serves retrieval
        # a prefix that need not contain what the query matched.
        assert call_kwargs.kwargs["chunk_text"] == build_post_text(
            title="Test Title", excerpt="Test excerpt", content="Test content",
        )
        assert call_kwargs.kwargs["chunk_index"] == 0

    @pytest.mark.asyncio
    async def test_skips_unchanged_post(self, service, ollama, embeddings_db):
        embeddings_db.needs_reembedding.return_value = False

        result = await service.embed_post(_make_post())

        assert result is None
        ollama.embed.assert_not_awaited()
        embeddings_db.store_embedding.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_truncate_content(self, service, ollama, embeddings_db):
        """Long posts are CHUNKED, not truncated.

        This used to embed ``title\nexcerpt\ncontent[:2000]`` as one row at
        chunk 0 — the same natural key ``PostsTap`` writes its real chunk 0
        to, with a hash the tap never produces. The two writers overwrote
        each other on every republish, and for part of every hour chunk 0
        held a 2000-char whole-post truncation instead of a chunk.
        """
        long_content = "x" * 5000
        post = _make_post(content=long_content)

        await service.embed_post(post)

        embedded_text = ollama.embed.call_args[0][0]
        assert embedded_text == build_post_text(
            title="Test Title", excerpt="Test excerpt", content=long_content,
        )
        assert "x" * 5000 in embedded_text

    @pytest.mark.asyncio
    async def test_oversize_post_is_split_into_chunks(
        self, service, ollama, embeddings_db
    ):
        """Past the 6000-char chunk ceiling, one post becomes several rows
        with distinct chunk_index values — same as the tap would write."""
        await service.embed_post(_make_post(content="paragraph text. " * 1500))

        indexes = [
            c.kwargs["chunk_index"]
            for c in embeddings_db.store_embedding.call_args_list
        ]
        assert len(indexes) > 1
        assert indexes == sorted(set(indexes))
        # Every chunk carries its own payload, and chunk 0 keeps the
        # whole-document hash so either writer's dedup check agrees.
        for call in embeddings_db.store_embedding.call_args_list:
            assert call.kwargs["chunk_text"]
        embeddings_db.delete_stale_chunks.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agrees_with_the_tap_on_chunk0_hash(
        self, service, ollama, embeddings_db
    ):
        """The collision guard: ``embed_post`` and ``PostsTap`` write the
        same key, so they must derive the same chunk-0 content_hash from the
        same post or they will clobber each other forever."""
        from services.taps._chunking import content_hash as tap_hash
        from services.taps.published_posts import PostsTap  # noqa: F401

        post = _make_post(content="a real post body " * 50)
        await service.embed_post(post)

        tap_text = build_post_text(
            title=post["title"], excerpt=post["excerpt"], content=post["content"],
        )
        chunk0 = embeddings_db.store_embedding.call_args_list[0].kwargs
        assert chunk0["content_hash"] == tap_hash(tap_text)

    @pytest.mark.asyncio
    async def test_embed_post_raises_on_ollama_error(self, service, ollama, embeddings_db):
        ollama.embed.side_effect = RuntimeError("Ollama down")

        with pytest.raises(RuntimeError, match="Ollama down"):
            await service.embed_post(_make_post())

    @pytest.mark.asyncio
    async def test_embed_post_raises_on_db_store_error(self, service, ollama, embeddings_db):
        embeddings_db.store_embedding.side_effect = Exception("DB write failed")

        with pytest.raises(Exception, match="DB write failed"):
            await service.embed_post(_make_post())

    @pytest.mark.asyncio
    async def test_content_hash_passed_to_db(self, service, ollama, embeddings_db):
        post = _make_post()
        combined = build_post_text(
            title=post["title"], excerpt=post["excerpt"], content=post["content"],
        )
        expected_hash = _content_hash(combined)

        await service.embed_post(post)

        call_kwargs = embeddings_db.store_embedding.call_args.kwargs
        assert call_kwargs["content_hash"] == expected_hash


# ---------------------------------------------------------------------------
# embed_brain_knowledge
# ---------------------------------------------------------------------------


class TestEmbedBrainKnowledge:
    @pytest.mark.asyncio
    async def test_embeds_new_knowledge(self, service, ollama, embeddings_db):
        result = await service.embed_brain_knowledge("Glad Labs", "mission", "democratize AI")

        assert result == "emb-uuid-123"
        ollama.embed.assert_awaited_once()

        call_kwargs = embeddings_db.store_embedding.call_args.kwargs
        assert call_kwargs["source_type"] == "brain_knowledge"
        assert call_kwargs["source_id"] == "Glad Labs::mission"
        assert call_kwargs["metadata"] == {
            "entity": "Glad Labs", "attribute": "mission",
        }
        # Short rows carry the payload too — "it fits in the preview anyway"
        # is how half a table ends up with a NULL retrieval column.
        assert call_kwargs["chunk_text"] == "Glad Labs mission: democratize AI"

    @pytest.mark.asyncio
    async def test_skips_unchanged_knowledge(self, service, ollama, embeddings_db):
        embeddings_db.needs_reembedding.return_value = False

        result = await service.embed_brain_knowledge("Glad Labs", "mission", "democratize AI")

        assert result is None
        ollama.embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_combined_text_format(self, service, ollama, embeddings_db):
        await service.embed_brain_knowledge("Entity", "attr", "val")

        embedded_text = ollama.embed.call_args[0][0]
        assert embedded_text == "Entity attr: val"

    @pytest.mark.asyncio
    async def test_raises_on_ollama_error(self, service, ollama, embeddings_db):
        ollama.embed.side_effect = RuntimeError("GPU OOM")

        with pytest.raises(RuntimeError, match="GPU OOM"):
            await service.embed_brain_knowledge("E", "A", "V")

    @pytest.mark.asyncio
    async def test_raises_on_db_error(self, service, ollama, embeddings_db):
        embeddings_db.store_embedding.side_effect = Exception("DB fail")

        with pytest.raises(Exception, match="DB fail"):
            await service.embed_brain_knowledge("E", "A", "V")


# ---------------------------------------------------------------------------
# embed_all_posts (batch)
# ---------------------------------------------------------------------------


class TestEmbedAllPosts:
    @pytest.mark.asyncio
    async def test_embeds_all_new_posts(self, service, ollama, embeddings_db):
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 2, "skipped": 0, "failed": 0}
        ollama.embed_batch.assert_awaited_once()
        assert embeddings_db.store_embedding.await_count == 2

    @pytest.mark.asyncio
    async def test_skips_unchanged_posts(self, service, ollama, embeddings_db):
        embeddings_db.needs_reembedding.return_value = False
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 0, "skipped": 2, "failed": 0}
        ollama.embed_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_new_and_unchanged(self, service, ollama, embeddings_db):
        # First post needs re-embedding, second doesn't
        embeddings_db.needs_reembedding.side_effect = [True, False]
        ollama.embed_batch.return_value = [SAMPLE_EMBEDDING]
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 1, "skipped": 1, "failed": 0}
        ollama.embed_batch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_list(self, service, ollama, embeddings_db):
        result = await service.embed_all_posts([])

        assert result == {"embedded": 0, "skipped": 0, "failed": 0}
        ollama.embed_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_embed_failure_returns_all_failed(self, service, ollama, embeddings_db):
        ollama.embed_batch.side_effect = RuntimeError("Batch failed")
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 0, "skipped": 0, "failed": 2}

    @pytest.mark.asyncio
    async def test_individual_store_failure(self, service, ollama, embeddings_db):
        # First store succeeds, second fails
        embeddings_db.store_embedding.side_effect = [
            "emb-1",
            Exception("DB write failed"),
        ]
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 1, "skipped": 0, "failed": 1}

    @pytest.mark.asyncio
    async def test_needs_reembedding_check_failure_skips(self, service, ollama, embeddings_db):
        # needs_reembedding raises for one post, should be counted as skipped
        embeddings_db.needs_reembedding.side_effect = [Exception("DB read fail"), True]
        ollama.embed_batch.return_value = [SAMPLE_EMBEDDING]
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result["skipped"] == 1
        assert result["embedded"] == 1


# ---------------------------------------------------------------------------
# embed_model threading
# ---------------------------------------------------------------------------


class TestEmbedModelPassthrough:
    """The model name must flow from the constructor into every embed call.
    Without this guarantee, changing `embedding_model` in app_settings would
    silently have no effect on the write path while the read (RAG) path honours
    the setting — producing cross-model query/corpus mismatch."""

    @pytest.mark.asyncio
    async def test_custom_model_passed_to_provider_on_single_embed(self, ollama, embeddings_db):
        svc = EmbeddingService(provider=ollama, embeddings_db=embeddings_db, embed_model="mxbai-embed-large")
        await svc.embed_post(_make_post())
        _, call_kwargs = ollama.embed.call_args
        assert call_kwargs.get("model") == "mxbai-embed-large"

    @pytest.mark.asyncio
    async def test_default_model_is_nomic(self, ollama, embeddings_db):
        svc = EmbeddingService(provider=ollama, embeddings_db=embeddings_db)
        await svc.embed_post(_make_post())
        _, call_kwargs = ollama.embed.call_args
        assert call_kwargs.get("model") == "nomic-embed-text"

    @pytest.mark.asyncio
    async def test_custom_model_passed_on_batch_embed(self, ollama, embeddings_db):
        svc = EmbeddingService(provider=ollama, embeddings_db=embeddings_db, embed_model="mxbai-embed-large")
        ollama.embed_batch.return_value = [SAMPLE_EMBEDDING]
        await svc.embed_all_posts([_make_post()])
        _, call_kwargs = ollama.embed_batch.call_args
        assert call_kwargs.get("model") == "mxbai-embed-large"
