"""Unit tests for CodebaseSource.

Mocks httpx.AsyncClient (Ollama embed calls) and the asyncpg pool. No
real network, no real pgvector.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.topic_source import TopicSource
from services.topic_sources.codebase import (
    CodebaseSource,
    _extract_topic_from_row,
)


# ---------------------------------------------------------------------------
# _extract_topic_from_row
# ---------------------------------------------------------------------------


class TestExtractTopicFromRow:
    def test_too_short_returns_none(self):
        assert _extract_topic_from_row("short", "posts", 80) is None

    def test_empty_returns_none(self):
        assert _extract_topic_from_row("", "posts", 80) is None
        assert _extract_topic_from_row(None, "posts", 80) is None  # type: ignore[arg-type]

    def test_skip_issues_memory_audit(self):
        long_text = "A reasonably long piece of text that would otherwise extract fine"
        assert _extract_topic_from_row(long_text, "issues", 80) is None
        assert _extract_topic_from_row(long_text, "memory", 80) is None
        assert _extract_topic_from_row(long_text, "audit", 80) is None

    def test_posts_extracts_first_long_line(self):
        text = "# A clear informative title for a blog post about something\nshort body"
        result = _extract_topic_from_row(text, "posts", 80)
        assert result == "A clear informative title for a blog post about something"

    def test_posts_respects_max_chars(self):
        long_title = "A" * 150
        text = long_title + "\nmore"
        result = _extract_topic_from_row(text, "posts", 50)
        assert result is not None
        assert len(result) == 50

    def test_posts_skips_short_lines(self):
        text = "short\ntiny\nAnother clear long title describing technical content in depth"
        result = _extract_topic_from_row(text, "posts", 80)
        assert result == "Another clear long title describing technical content in depth"

    def test_unknown_source_returns_none(self):
        long_text = "A plausible long text that might otherwise be a topic from somewhere"
        assert _extract_topic_from_row(long_text, "unknown_source", 80) is None


# ---------------------------------------------------------------------------
# CodebaseSource.extract
# ---------------------------------------------------------------------------


def _make_embed_client(embedding: list[float] | None):
    """Fake Ollama client returning the given embedding (or None for failure)."""
    resp = MagicMock()
    resp.status_code = 200 if embedding is not None else 500
    resp.json = MagicMock(return_value={"embedding": embedding or []})

    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


def _make_pool(rows_per_call: list[list[dict]] | None = None):
    """Mock asyncpg pool whose .fetch() returns the next canned row list.

    ``rows_per_call`` is a list of row-lists; each call consumes the next
    entry, so test fixtures can script a different result per seed query.
    """
    pool = AsyncMock()
    calls = {"n": 0}

    async def fetch(sql: str, *args: Any):
        if rows_per_call is None:
            return []
        idx = calls["n"]
        calls["n"] += 1
        if idx < len(rows_per_call):
            return rows_per_call[idx]
        return []

    pool.fetch = AsyncMock(side_effect=fetch)
    return pool


class TestCodebaseSourceExtract:
    @pytest.mark.asyncio
    async def test_no_pool_returns_empty(self):
        source = CodebaseSource()
        assert await source.extract(pool=None, config={}) == []

    @pytest.mark.asyncio
    async def test_yields_topic_from_posts_row(self):
        ctx, client = _make_embed_client([0.1] * 768)
        pool = _make_pool([
            [
                {
                    "source_table": "posts",
                    "source_id": "post-123",
                    "text_preview": "An illuminating blog post title about Rust async runtime deep dive\nand more body text",
                    "similarity": 0.7,
                },
            ],
        ])

        source = CodebaseSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(
                pool=pool,
                config={"seed_queries": ["rust async"]},
            )
        assert len(topics) == 1
        assert topics[0].source == "embeddings:posts"
        assert "Rust async runtime" in topics[0].title
        # Score = min(0.9, 0.7 + 0.3) = 0.9
        assert topics[0].relevance_score == 0.9

    @pytest.mark.asyncio
    async def test_similarity_threshold_filters_low_sim(self):
        ctx, _ = _make_embed_client([0.1] * 768)
        pool = _make_pool([
            [
                {
                    "source_table": "posts",
                    "source_id": "post-low",
                    "text_preview": "A real long title line about technical content for fun",
                    "similarity": 0.2,  # Below default 0.4 threshold
                },
            ],
        ])
        source = CodebaseSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(
                pool=pool, config={"seed_queries": ["x"]},
            )
        assert topics == []

    @pytest.mark.asyncio
    async def test_dedup_by_source_id(self):
        # Same source_id appears in two different seed queries' results.
        ctx, _ = _make_embed_client([0.1] * 768)
        dup_row = {
            "source_table": "posts",
            "source_id": "post-A",
            "text_preview": "An informative title line for a technical deep dive into systems",
            "similarity": 0.8,
        }
        pool = _make_pool([[dup_row], [dup_row]])

        source = CodebaseSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(
                pool=pool,
                config={"seed_queries": ["q1", "q2"]},
            )
        # Dedup should collapse the two identical source_ids to one topic.
        assert len(topics) == 1

    @pytest.mark.asyncio
    async def test_non_posts_source_skipped(self):
        ctx, _ = _make_embed_client([0.1] * 768)
        pool = _make_pool([
            [
                {
                    "source_table": "audit",
                    "source_id": "audit-x",
                    "text_preview": "A plausibly long audit event text that would otherwise extract",
                    "similarity": 0.9,
                },
            ],
        ])
        source = CodebaseSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(
                pool=pool, config={"seed_queries": ["x"]},
            )
        assert topics == []

    @pytest.mark.asyncio
    async def test_embed_http_failure_skipped_not_aborted(self):
        """Failing embed for one seed shouldn't abort the whole pass."""
        # First call fails (None embedding → status 500), second succeeds.
        resp_fail = MagicMock()
        resp_fail.status_code = 500

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json = MagicMock(return_value={"embedding": [0.1] * 768})

        client = AsyncMock()
        client.post = AsyncMock(side_effect=[resp_fail, resp_ok])
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        pool = _make_pool([
            [
                {
                    "source_table": "posts",
                    "source_id": "post-X",
                    "text_preview": "A clearly readable example post title about async primitives",
                    "similarity": 0.8,
                },
            ],
        ])
        source = CodebaseSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(
                pool=pool,
                config={"seed_queries": ["q1", "q2"]},
            )
        # Second seed returned a topic; first seed's failure was isolated.
        assert len(topics) == 1

    @pytest.mark.asyncio
    async def test_empty_embedding_response_skipped(self):
        ctx, _ = _make_embed_client([])
        pool = _make_pool([])
        source = CodebaseSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(
                pool=pool, config={"seed_queries": ["x"]},
            )
        assert topics == []

    @pytest.mark.asyncio
    async def test_custom_lookback_threaded_into_sql(self):
        ctx, _ = _make_embed_client([0.1] * 768)
        pool = _make_pool([])
        source = CodebaseSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            await source.extract(
                pool=pool,
                config={"seed_queries": ["x"], "lookback_days": 7},
            )
        # The SQL should have 'INTERVAL '7 days' interpolated.
        sqls = [str(c.args[0]) for c in pool.fetch.await_args_list]
        assert any("INTERVAL '7 days'" in s for s in sqls)


class TestContract:
    def test_conforms_to_topic_source_protocol(self):
        source = CodebaseSource()
        assert isinstance(source, TopicSource)
        assert source.name == "codebase"

    def test_extract_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(CodebaseSource.extract)
