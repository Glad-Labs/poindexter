"""BenchmarkFindingsSource — topics from our own instrumentation.

Two behaviours carry the value and are pinned hardest: the measurements must
travel with the topic (``description`` becomes ``topic_pool.summary`` and then
the task's ``research_context``), and the source must stand down rather than
guess when it cannot read its own freshness state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.topic_sources.benchmark_findings import BenchmarkFindingsSource

pytestmark = pytest.mark.unit

_CFG = {
    "window_days": 30, "min_calls": 30, "min_models": 3, "min_spread_pct": 25,
    "cooldown_days": 30, "new_model_days": 30, "max_topics": 5,
}

_MEASURE_ROWS = [
    {"model": "phi4:14b", "calls": 243, "first_seen": "2026-08-26",
     "last_seen": "2026-09-01", "decode_tps": 124.7, "wall_tps": 25.3, "overhead_ms": 8872},
    {"model": "qwen2.5:7b", "calls": 90, "first_seen": "2026-08-27",
     "last_seen": "2026-09-01", "decode_tps": 235.1, "wall_tps": 64.2, "overhead_ms": 2756},
    {"model": "glm-4.7-5090:latest", "calls": 34, "first_seen": "2026-08-28",
     "last_seen": "2026-08-30", "decode_tps": 177.0, "wall_tps": 172.2, "overhead_ms": 591},
]


def _pool(measure_rows=None, recent_titles=(), new_models=(), fail_second=False):
    calls = {"n": 0}

    async def _fetch(query, *args):
        calls["n"] += 1
        if "PERCENTILE_CONT" in query:
            return list(_MEASURE_ROWS if measure_rows is None else measure_rows)
        if fail_second:
            raise RuntimeError("cooldown lookup exploded")
        if "topic_pool" in query:
            return [{"title": t} for t in recent_titles]
        return [{"model": m, "first_call": datetime.now(timezone.utc)} for m in new_models]

    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=_fetch)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


class TestMeasurementsTravelWithTheTopic:
    async def test_description_carries_the_fact_block(self):
        """This is the whole mechanism: description -> topic_pool.summary ->
        task metadata.research_context -> the writer's grounding AND the
        corpus qa.numeric_fidelity checks the draft against."""
        topics = await BenchmarkFindingsSource().extract(_pool(), _CFG)
        assert topics, "a fleet with a 77-point spread must produce a topic"
        block = topics[0].description
        assert "cost_logs" in block
        for token in ("124.7", "25.3", "phi4:14b"):
            assert token in block

    async def test_topic_is_labelled_with_this_source(self):
        t = (await BenchmarkFindingsSource().extract(_pool(), _CFG))[0]
        assert t.source == "benchmark_findings"
        assert t.source_url == ""  # our own telemetry has no external URL
        assert t.keywords


class TestBars:
    async def test_no_qualifying_models_proposes_nothing(self):
        assert await BenchmarkFindingsSource().extract(_pool([]), _CFG) == []

    async def test_flat_fleet_proposes_nothing(self):
        flat = [
            {**r, "decode_tps": 100.0, "wall_tps": 90.0} for r in _MEASURE_ROWS
        ]
        assert await BenchmarkFindingsSource().extract(_pool(flat), _CFG) == []

    async def test_max_topics_caps_output(self):
        pool = _pool(new_models=["phi4:14b", "qwen2.5:7b"])
        out = await BenchmarkFindingsSource().extract(pool, {**_CFG, "max_topics": 1})
        assert len(out) == 1

    async def test_a_new_model_becomes_its_own_topic(self):
        out = await BenchmarkFindingsSource().extract(
            _pool(new_models=["qwen2.5:7b"]), _CFG,
        )
        assert any("qwen2.5:7b" in t.title for t in out)


class TestFreshness:
    async def test_a_finding_inside_its_cooldown_is_skipped(self):
        title = "What local models actually deliver versus their benchmark decode speed"
        out = await BenchmarkFindingsSource().extract(
            _pool(recent_titles=[title]), _CFG,
        )
        assert all(t.title != title for t in out)

    async def test_it_stands_down_when_freshness_state_is_unreadable(self):
        """Without the cooldown read it cannot tell a fresh finding from one
        proposed yesterday. Proposing anyway would spam the pool."""
        out = await BenchmarkFindingsSource().extract(_pool(fail_second=True), _CFG)
        assert out == []


class TestFailureIsolation:
    async def test_no_pool_returns_empty(self):
        assert await BenchmarkFindingsSource().extract(None, _CFG) == []

    async def test_a_measurement_query_failure_never_raises(self):
        """One source crashing must not kill the whole discovery run."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=RuntimeError("db gone"))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        assert await BenchmarkFindingsSource().extract(pool, _CFG) == []

    async def test_bad_config_values_fall_back_to_defaults(self):
        out = await BenchmarkFindingsSource().extract(
            _pool(), {**_CFG, "min_calls": "not-a-number"},
        )
        assert out, "a malformed knob must not silently disable the source"


class TestNewnessSemantics:
    def test_new_model_query_is_not_filtered_on_decode_capture(self):
        """decode_duration_ms only exists from 2026-08-26, so filtering on it
        makes every model look brand new — qwen3-vl:30b, running for months,
        was proposed as a new-model finding for exactly that reason."""
        from services.topic_sources.benchmark_findings import _FIRST_SEEN_SQL

        assert "decode_duration_ms" not in _FIRST_SEEN_SQL
