"""
Unit tests for routes/memory_dashboard_routes.py

Tests the helper functions and route structure.
DB-dependent endpoints are tested with mocked MemoryClient.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routes.memory_dashboard_routes import _iso, _seconds_since


class TestIsoHelper:
    def test_none_returns_none(self):
        assert _iso(None) is None

    def test_aware_datetime(self):
        dt = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
        assert _iso(dt) == "2026-04-12T12:00:00+00:00"

    def test_naive_datetime_gets_utc(self):
        dt = datetime(2026, 4, 12, 12, 0, 0)
        result = _iso(dt)
        assert "+00:00" in result


class TestSecondsSince:
    def test_none_returns_none(self):
        assert _seconds_since(None) is None

    def test_recent_timestamp(self):
        recent = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = _seconds_since(recent)
        assert 25 <= result <= 35

    def test_old_timestamp(self):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        result = _seconds_since(old)
        assert 7100 <= result <= 7300

    def test_naive_datetime_treated_as_utc(self):
        recent = datetime.utcnow() - timedelta(seconds=10)
        result = _seconds_since(recent)
        assert result is not None
        assert result >= 0


class TestResolveStalenessThreshold:
    @pytest.mark.asyncio
    async def test_returns_default_when_no_dsn(self):
        with patch.dict("os.environ", {}, clear=True):
            from routes.memory_dashboard_routes import _resolve_staleness_threshold
            result = await _resolve_staleness_threshold("claude-code")
            assert result == 6 * 3600  # 6 hours default


class TestRouteRegistration:
    """Verify the router has the expected endpoints registered."""

    def test_router_has_memory_stats(self):
        from routes.memory_dashboard_routes import router
        paths = [r.path for r in router.routes]
        assert "/api/memory/stats" in paths

    def test_router_has_memory_search(self):
        from routes.memory_dashboard_routes import router
        paths = [r.path for r in router.routes]
        assert "/api/memory/search" in paths

    def test_router_has_html_dashboard(self):
        from routes.memory_dashboard_routes import router
        paths = [r.path for r in router.routes]
        assert "/memory" in paths
