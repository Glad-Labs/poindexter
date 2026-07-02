"""Unit tests for ``brain/data_freshness_probe.py`` (2026-07-01 audit).

Pins the generalized data-feed dead-man's switch: per-feed staleness vs
an app_settings-declared threshold, edge-triggered ``data_feed_stale``
finding (warning severity, stable per-feed ``dedup_key``), no re-fire on
a persistent stall, recovery resets the edge, feeds with zero rows are
not assessed, and malformed feed config entries are dropped instead of
interpolated into SQL.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from brain.data_freshness_probe import (
    DEFAULT_FEEDS,
    _parse_feeds,
    run_data_freshness_probe,
)

_FEEDS_JSON = json.dumps([
    {"name": "cost_logs", "table": "cost_logs", "column": "created_at",
     "threshold_minutes": 180},
])


def _pool(*, age_min, prev_state=None, feeds_json=_FEEDS_JSON, enabled="true"):
    """asyncpg pool stub for a single-feed config.

    ``fetchval`` routes the two app_settings reads (enabled flag, feeds
    JSON); ``fetchrow`` routes the feed max-age query and the
    brain_knowledge prev-state lookup by SQL text.
    """
    pool = MagicMock()

    async def _fetchval(query, *args, **kwargs):  # noqa: ANN001, ARG001
        key = args[0] if args else ""
        if key == "data_freshness_probe_enabled":
            return enabled
        if key == "data_freshness_feeds":
            return feeds_json
        return None

    async def _fetchrow(query, *args, **kwargs):  # noqa: ANN001, ARG001
        if "brain_knowledge" in query:
            return {"value": prev_state} if prev_state is not None else None
        return {"age_min": age_min}

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    return pool


def _finding_calls(pool):
    return [
        c.args for c in pool.execute.call_args_list
        if c.args and "audit_log" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_fresh_feed_no_finding():
    pool = _pool(age_min=30.0, prev_state="fresh")
    res = await run_data_freshness_probe(pool)
    assert res["ok"] is True
    assert res["feeds"]["cost_logs"]["state"] == "fresh"
    assert _finding_calls(pool) == []


@pytest.mark.asyncio
async def test_stale_transition_emits_finding_with_shape():
    pool = _pool(age_min=500.0, prev_state="fresh")
    res = await run_data_freshness_probe(pool)
    assert res["ok"] is False
    assert res["feeds"]["cost_logs"]["state"] == "stale"
    calls = _finding_calls(pool)
    assert len(calls) == 1
    details = json.loads(calls[0][1])
    assert details["kind"] == "data_feed_stale"  # dot-free per #756
    assert details["dedup_key"] == "data_feed_stale:cost_logs"
    assert "'warning'" in calls[0][0]


@pytest.mark.asyncio
async def test_persistent_stall_does_not_refire():
    pool = _pool(age_min=500.0, prev_state="stale")
    res = await run_data_freshness_probe(pool)
    assert res["ok"] is False
    assert _finding_calls(pool) == []


@pytest.mark.asyncio
async def test_already_stale_at_boot_emits():
    """prev=None (no brain_knowledge row yet) + stale must still surface."""
    pool = _pool(age_min=500.0, prev_state=None)
    await run_data_freshness_probe(pool)
    assert len(_finding_calls(pool)) == 1


@pytest.mark.asyncio
async def test_zero_row_feed_not_assessed():
    pool = _pool(age_min=None)
    res = await run_data_freshness_probe(pool)
    assert res["ok"] is True
    assert res["feeds"]["cost_logs"]["state"] == "not_assessed"
    assert _finding_calls(pool) == []
    pool.execute.assert_not_called()  # no state write either


@pytest.mark.asyncio
async def test_disabled_probe_is_silent():
    pool = _pool(age_min=999999.0, enabled="false")
    res = await run_data_freshness_probe(pool)
    assert res == {"ok": True, "detail": "disabled", "feeds": {}}
    pool.fetchrow.assert_not_called()


def test_parse_feeds_falls_back_to_defaults_on_garbage():
    fallback = _parse_feeds("")
    assert [f["name"] for f in fallback] == [f["name"] for f in DEFAULT_FEEDS]
    assert _parse_feeds("not json") == fallback
    assert _parse_feeds('{"a": 1}') == fallback


def test_parse_feeds_drops_sql_unsafe_identifiers():
    """Injection-shaped table/column names never reach the query builder."""
    feeds = _parse_feeds(json.dumps([
        {"name": "evil", "table": "cost_logs; DROP TABLE posts",
         "column": "created_at", "threshold_minutes": 60},
        {"name": "evil2", "table": "cost_logs",
         "column": 'created_at" FROM pg_shadow --', "threshold_minutes": 60},
        {"name": "no_threshold", "table": "cost_logs",
         "column": "created_at", "threshold_minutes": 0},
        {"name": "good", "table": "cost_logs", "column": "created_at",
         "threshold_minutes": 60},
    ]))
    assert [f["name"] for f in feeds] == ["good"]


def test_parse_feeds_accepts_filter_column_value():
    feeds = _parse_feeds(json.dumps([
        {"name": "corsair", "table": "sensor_samples", "column": "sampled_at",
         "threshold_minutes": 120, "filter_column": "source",
         "filter_value": "corsair_csv"},
    ]))
    assert feeds[0]["filter_column"] == "source"
    assert feeds[0]["filter_value"] == "corsair_csv"


@pytest.mark.asyncio
async def test_filtered_feed_binds_value_as_parameter():
    feeds_json = json.dumps([
        {"name": "corsair", "table": "sensor_samples", "column": "sampled_at",
         "threshold_minutes": 120, "filter_column": "source",
         "filter_value": "corsair_csv"},
    ])
    pool = _pool(age_min=10.0, prev_state="fresh", feeds_json=feeds_json)
    await run_data_freshness_probe(pool)
    feed_query_call = pool.fetchrow.call_args_list[0]
    assert "WHERE source = $1" in feed_query_call.args[0]
    assert feed_query_call.args[1] == "corsair_csv"


def test_seeded_default_matches_in_code_fallback():
    """The settings_defaults.py seed and DEFAULT_FEEDS must describe the
    same feeds — otherwise a fresh install (seed) and a broken-settings
    fallback (in-code) watch different things."""
    from services.settings_defaults import DEFAULTS

    seeded = _parse_feeds(DEFAULTS["data_freshness_feeds"])
    fallback = _parse_feeds("")
    assert seeded == fallback
