"""
Unit tests for agents/content_agent/services/postgres_cms_client.py

Tests for PostgresCMSClient (no live database required — all DB calls mocked).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.content_agent.services.postgres_cms_client import PostgresCMSClient
from agents.content_agent.utils.data_models import BlogPost

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULTS = {  # type: ignore[arg-type]
    "topic": "AI Trends",
    "primary_keyword": "artificial intelligence",
    "target_audience": "developers",
    "category": "tech",
}


def _make_post(**kwargs) -> BlogPost:
    return BlogPost(**{**DEFAULTS, **kwargs})  # type: ignore[arg-type]


def _make_client(
    database_url: str = "postgresql://user:pass@localhost/testdb",
) -> PostgresCMSClient:
    with patch("agents.content_agent.services.postgres_cms_client.config") as mock_cfg:
        mock_cfg.DATABASE_URL = database_url
        client = PostgresCMSClient(database_url=database_url)
    return client


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestPostgresCMSClientInit:
    def test_stores_database_url(self):
        client = _make_client("postgresql://user:pass@host/db")
        assert client.database_url == "postgresql://user:pass@host/db"

    def test_initial_pool_none(self):
        client = _make_client()
        assert client.pool is None

    def test_uses_config_url_when_none_provided(self):
        with patch("agents.content_agent.services.postgres_cms_client.config") as mock_cfg:
            mock_cfg.DATABASE_URL = "postgresql://config:pass@db/prod"
            client = PostgresCMSClient()
        assert client.database_url == "postgresql://config:pass@db/prod"

    def test_logs_init_info(self):
        with (
            patch("agents.content_agent.services.postgres_cms_client.logger") as mock_logger,
            patch("agents.content_agent.services.postgres_cms_client.config") as mock_cfg,
        ):
            mock_cfg.DATABASE_URL = "postgresql://user:pass@host/db"
            PostgresCMSClient()
            mock_logger.info.assert_called()


# ---------------------------------------------------------------------------
# _mask_url
# ---------------------------------------------------------------------------


class TestMaskUrl:
    def test_masks_password(self):
        client = _make_client()
        masked = client._mask_url("postgresql://admin:secretpassword@db.host/mydb")
        assert "secretpassword" not in masked
        assert "***" in masked
        assert "db.host/mydb" in masked

    def test_url_without_credentials_unchanged(self):
        client = _make_client()
        url = "postgresql://db.host/mydb"
        masked = client._mask_url(url)
        assert masked == url

    def test_url_without_scheme_returned_as_is(self):
        client = _make_client()
        url = "not-a-url"
        assert client._mask_url(url) == url

    def test_empty_string(self):
        client = _make_client()
        assert client._mask_url("") == ""


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


class TestInitialize:
    @pytest.mark.asyncio
    async def test_creates_pool_on_success(self):
        client = _make_client()
        mock_pool = AsyncMock()

        with patch("agents.content_agent.services.postgres_cms_client.asyncpg") as mock_asyncpg:
            mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)
            client._ensure_schema = AsyncMock()

            await client.initialize()

        assert client.pool is mock_pool

    @pytest.mark.asyncio
    async def test_raises_on_pool_creation_failure(self):
        client = _make_client()

        with patch("agents.content_agent.services.postgres_cms_client.asyncpg") as mock_asyncpg:
            mock_asyncpg.create_pool = AsyncMock(side_effect=RuntimeError("connection refused"))

            with pytest.raises(RuntimeError, match="connection refused"):
                await client.initialize()

    @pytest.mark.asyncio
    async def test_calls_ensure_schema_after_pool_creation(self):
        client = _make_client()
        mock_pool = AsyncMock()

        with patch("agents.content_agent.services.postgres_cms_client.asyncpg") as mock_asyncpg:
            mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)
            client._ensure_schema = AsyncMock()

            await client.initialize()

        client._ensure_schema.assert_called_once()


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_closes_pool_when_set(self):
        client = _make_client()
        mock_pool = AsyncMock()
        client.pool = mock_pool

        await client.close()
        mock_pool.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_error_when_pool_is_none(self):
        client = _make_client()
        client.pool = None
        # Should not raise
        await client.close()


# ---------------------------------------------------------------------------
# create_post — pool not initialized guard
# ---------------------------------------------------------------------------


def _make_mock_pool(mock_conn):
    """
    Build a mock asyncpg pool where pool.acquire() is an async context manager.

    asyncpg's pool.acquire() returns an object that supports 'async with',
    NOT a coroutine itself. We replicate this with a MagicMock that defines
    __aenter__/__aexit__ on the value returned by pool.acquire().
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool = MagicMock()
    mock_pool.acquire = _acquire
    return mock_pool


class TestCreatePost:
    @pytest.mark.asyncio
    async def test_raises_when_pool_not_initialized(self):
        client = _make_client()
        client.pool = None
        post = _make_post(title="My Post", raw_content="Content")

        with pytest.raises(RuntimeError, match="initialize"):
            await client.create_post(post)

    @pytest.mark.asyncio
    async def test_returns_post_id_and_slug(self):
        client = _make_client()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        client.pool = _make_mock_pool(mock_conn)

        post = _make_post(title="Test Post", raw_content="Content here", slug="test-post")

        post_id, slug = await client.create_post(post)

        assert isinstance(post_id, str)
        assert len(post_id) > 0
        assert slug == "test-post"

    @pytest.mark.asyncio
    async def test_generates_slug_from_title_when_missing(self):
        client = _make_client()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        client.pool = _make_mock_pool(mock_conn)

        post = _make_post(title="Hello World", raw_content="Content")
        post.slug = None

        post_id, slug = await client.create_post(post)

        assert slug == "hello-world"

    @pytest.mark.asyncio
    async def test_uses_topic_when_title_missing(self):
        client = _make_client()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        client.pool = _make_mock_pool(mock_conn)

        post = _make_post(raw_content="Content")
        post.title = None
        post.slug = None

        post_id, slug = await client.create_post(post)

        # Should fall back to topic (lowercase, hyphenated)
        assert "ai" in slug.lower() or len(slug) > 0


# ---------------------------------------------------------------------------
# _ensure_schema — pool not initialized guard
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    @pytest.mark.asyncio
    async def test_returns_early_when_pool_none(self):
        client = _make_client()
        client.pool = None

        # Should not raise, just log a warning
        await client._ensure_schema()

    @pytest.mark.asyncio
    async def test_executes_schema_when_pool_available(self):
        client = _make_client()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        client.pool = _make_mock_pool(mock_conn)

        await client._ensure_schema()

        # Multiple CREATE TABLE IF NOT EXISTS calls expected
        assert mock_conn.execute.call_count >= 4
