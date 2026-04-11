"""
Unit tests for services/settings_service.py.

Covers get/set/delete, caching, secret masking, env-var fallback,
and category filtering.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.settings_service import _SECRET_MASK, SettingsService


def _make_pool(rows=None):
    """Build a mock asyncpg pool that returns *rows* on fetch."""
    if rows is None:
        rows = []

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(return_value=rows)
    mock_conn.execute = AsyncMock(return_value="DELETE 1")
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=mock_conn)
    return pool


def _row(key, value, category="general", description=None, is_secret=False, updated_at=None):
    """Create a dict mimicking an asyncpg Record."""
    return {
        "key": key,
        "value": value,
        "category": category,
        "description": description,
        "is_secret": is_secret,
        "updated_at": updated_at,
    }


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGet:
    def test_returns_value_from_cache(self):
        pool = _make_pool([_row("site_name", "Glad Labs")])
        svc = SettingsService(pool)
        result = _run(svc.get("site_name"))
        assert result == "Glad Labs"

    def test_returns_default_when_missing(self):
        pool = _make_pool([])
        svc = SettingsService(pool)
        result = _run(svc.get("nonexistent", default="fallback"))
        assert result == "fallback"

    def test_falls_back_to_env_var(self):
        pool = _make_pool([_row("api_key", "")])  # empty DB value
        svc = SettingsService(pool)
        with patch.dict("os.environ", {"API_KEY": "from-env"}):
            result = _run(svc.get("api_key"))
        assert result == "from-env"

    def test_db_value_takes_priority_over_env(self):
        pool = _make_pool([_row("api_key", "from-db")])
        svc = SettingsService(pool)
        with patch.dict("os.environ", {"API_KEY": "from-env"}):
            result = _run(svc.get("api_key"))
        assert result == "from-db"


# ---------------------------------------------------------------------------
# get_by_category
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetByCategory:
    def test_filters_by_category(self):
        pool = _make_pool([
            _row("a", "1", category="social"),
            _row("b", "2", category="social"),
            _row("c", "3", category="general"),
        ])
        svc = SettingsService(pool)
        result = _run(svc.get_by_category("social"))
        assert result == {"a": "1", "b": "2"}

    def test_returns_empty_for_missing_category(self):
        pool = _make_pool([_row("a", "1", category="general")])
        svc = SettingsService(pool)
        result = _run(svc.get_by_category("nonexistent"))
        assert result == {}


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSet:
    def test_set_calls_execute(self):
        pool = _make_pool()
        svc = SettingsService(pool)
        _run(svc.set("new_key", "new_value", category="test"))
        conn = pool.acquire().__aenter__.return_value
        # Verify execute was called (the upsert SQL)
        assert conn.execute.called

    def test_set_invalidates_cache(self):
        pool = _make_pool([_row("x", "old")])
        svc = SettingsService(pool)
        _run(svc.get("x"))  # populate cache
        assert svc._last_refresh > 0
        _run(svc.set("x", "new"))
        assert svc._last_refresh == 0  # cache invalidated


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelete:
    def test_delete_invalidates_cache(self):
        pool = _make_pool([_row("x", "val")])
        svc = SettingsService(pool)
        _run(svc.get("x"))  # populate cache
        _run(svc.delete("x"))
        assert svc._last_refresh == 0


# ---------------------------------------------------------------------------
# get_all + secret masking
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAll:
    def test_masks_secrets_by_default(self):
        pool = _make_pool([
            _row("public_key", "visible", is_secret=False),
            _row("secret_key", "hidden", is_secret=True),
        ])
        svc = SettingsService(pool)
        result = _run(svc.get_all(include_secrets=False))
        values = {r["key"]: r["value"] for r in result}
        assert values["public_key"] == "visible"
        assert values["secret_key"] == _SECRET_MASK

    def test_reveals_secrets_when_requested(self):
        pool = _make_pool([
            _row("secret_key", "hidden", is_secret=True),
        ])
        svc = SettingsService(pool)
        result = _run(svc.get_all(include_secrets=True))
        assert result[0]["value"] == "hidden"

    def test_returns_sorted_by_key(self):
        pool = _make_pool([
            _row("z_key", "z"),
            _row("a_key", "a"),
            _row("m_key", "m"),
        ])
        svc = SettingsService(pool)
        result = _run(svc.get_all())
        keys = [r["key"] for r in result]
        assert keys == ["a_key", "m_key", "z_key"]


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCacheBehavior:
    def test_does_not_refetch_within_ttl(self):
        pool = _make_pool([_row("x", "val")])
        svc = SettingsService(pool)
        svc._cache_ttl = 300

        _run(svc.get("x"))  # first call loads cache
        fetch_count_1 = pool.acquire().__aenter__.return_value.fetch.call_count

        _run(svc.get("x"))  # second call should use cache
        fetch_count_2 = pool.acquire().__aenter__.return_value.fetch.call_count

        # fetch should not have been called again
        assert fetch_count_2 == fetch_count_1
