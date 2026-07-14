"""Unit tests for GscQueryGapSource -- fake pool, no real DB."""

from __future__ import annotations

from typing import Any

import pytest

from services.topic_sources.gsc_query_gap import GscQueryGapSource


class _FakeConn:
    def __init__(self, setting_value: str | None, gap_rows: list[dict[str, Any]]):
        self._setting_value = setting_value
        self._gap_rows = gap_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query, *args):
        assert "app_settings" in query
        return self._setting_value

    async def fetch(self, query, *args):
        assert "external_metrics" in query
        return self._gap_rows


class _FakePool:
    def __init__(self, setting_value: str | None = "true", gap_rows: list[dict[str, Any]] | None = None):
        self._setting_value = setting_value
        self._gap_rows = gap_rows or []

    def acquire(self):
        return _FakeConn(self._setting_value, self._gap_rows)


def _gap_row(**overrides) -> dict[str, Any]:
    base = {
        "query": "docker compose tutorial",
        "impressions": 500.0,
        "avg_position": 22.5,
    }
    base.update(overrides)
    return base


class TestGscQueryGapSource:
    name = "gsc_query_gap"

    @pytest.mark.asyncio
    async def test_returns_empty_when_ingestion_disabled(self):
        source = GscQueryGapSource()
        pool = _FakePool(setting_value="false", gap_rows=[_gap_row()])
        topics = await source.extract(pool, {})
        assert topics == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_setting_missing(self):
        source = GscQueryGapSource()
        pool = _FakePool(setting_value=None, gap_rows=[_gap_row()])
        topics = await source.extract(pool, {})
        assert topics == []

    @pytest.mark.asyncio
    async def test_yields_one_topic_per_gap_row(self):
        source = GscQueryGapSource()
        pool = _FakePool(
            setting_value="true",
            gap_rows=[
                _gap_row(query="docker compose tutorial", impressions=500.0, avg_position=22.5),
                _gap_row(query="best gpu 2026", impressions=1200.0, avg_position=18.0),
            ],
        )
        topics = await source.extract(pool, {})
        assert len(topics) == 2
        titles = {t.title for t in topics}
        assert "Docker Compose Tutorial" in titles
        assert "Best Gpu 2026" in titles
        for t in topics:
            assert t.source == "gsc_query_gap"
            assert t.keywords
            assert t.relevance_score > 0
            assert t.source_url.startswith("https://www.google.com/search?q=")

    @pytest.mark.asyncio
    async def test_relevance_score_scales_with_impressions(self):
        source = GscQueryGapSource()
        pool = _FakePool(
            setting_value="true",
            gap_rows=[
                _gap_row(query="low impressions query", impressions=10.0, avg_position=20.0),
                _gap_row(query="high impressions query", impressions=2000.0, avg_position=20.0),
            ],
        )
        topics = await source.extract(pool, {})
        by_query = {t.keywords[0]: t for t in topics}
        assert (
            by_query["high impressions query"].relevance_score
            > by_query["low impressions query"].relevance_score
        )

    @pytest.mark.asyncio
    async def test_config_overrides_thresholds(self):
        # Just confirm the config dict is read without raising -- the actual
        # threshold filtering happens in SQL (faked here), so this test only
        # locks in that extract() accepts the standard config keys.
        source = GscQueryGapSource()
        pool = _FakePool(setting_value="true", gap_rows=[])
        topics = await source.extract(
            pool,
            {"min_impressions": 200, "min_position": 10, "window_days": 14, "max_topics": 5},
        )
        assert topics == []
