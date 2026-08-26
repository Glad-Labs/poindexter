"""Roundtrip tests for ``services.llm_throughput.get_llm_throughput_trend``
against the Postgres test DB (``db_pool``). Seeds ``cost_logs`` inference rows,
then asserts the epoch-bucketed per-model series, the non-LLM/failed-row
filters, the ``ollama/`` prefix merge, and the top-N cap. Mirrors
``tests/unit/services/test_qa_trend.py``."""

from __future__ import annotations

import pytest

from services.llm_throughput import _clamp, get_llm_throughput_trend

# db_pool is loop_scope="session"; tests must share that loop.
pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_clamp_bounds_bucket_count():
    rng, step = _clamp(604800, 1)
    assert rng == 604800
    assert step >= 604800 / 1000
    assert _clamp(5, 5) == (60, 15)


async def test_metric_validated():
    with pytest.raises(ValueError):
        await get_llm_throughput_trend(
            None, range_seconds=3600, step_seconds=900, metric="bogus"
        )


async def _seed_call(
    conn,
    *,
    model,
    output_tokens,
    duration_ms,
    cost_type="inference",
    success=True,
):
    await conn.execute(
        "INSERT INTO cost_logs (phase, model, provider, input_tokens, "
        "output_tokens, total_tokens, duration_ms, cost_type, success) "
        "VALUES ('test', $1, 'litellm', 10, $2, $3, $4, $5, $6)",
        model,
        output_tokens,
        10 + output_tokens,
        duration_ms,
        cost_type,
        success,
    )


async def _reset(conn):
    await conn.execute("DELETE FROM cost_logs WHERE phase = 'test'")


async def test_speed_series_merges_ollama_prefix_and_gaps(db_pool):
    async with db_pool.acquire() as conn:
        await _reset(conn)
        # Same engine, two dispatch-path spellings → ONE merged series.
        # 100 tok in 10s + 200 tok in 10s = 300 tok / 20s = 15.0 tok/s.
        await _seed_call(conn, model="phi4:14b", output_tokens=100, duration_ms=10_000)
        await _seed_call(
            conn, model="ollama/phi4:14b", output_tokens=200, duration_ms=10_000
        )
        # Excluded rows: non-LLM (zero OUTPUT tokens — the seed helper still
        # gives it input tokens, proving input-only rows can't leak in),
        # failed call, zero duration.
        await _seed_call(
            conn, model="Systran/faster-whisper-medium", output_tokens=0, duration_ms=41_000
        )
        await _seed_call(
            conn, model="phi4:14b", output_tokens=999, duration_ms=1_000, success=False
        )
        await _seed_call(conn, model="phi4:14b", output_tokens=999, duration_ms=0)
    try:
        out = await get_llm_throughput_trend(
            db_pool, range_seconds=3600, step_seconds=900, metric="speed"
        )
        assert out["metric"] == "speed"
        labels = [s["label"] for s in out["series"]]
        assert labels == ["phi4:14b"]  # merged, excluded rows grew no series
        pts = out["series"][0]["points"]
        vals = [v for _, v in pts if v is not None]
        assert vals == [15.0]
        assert any(v is None for _, v in pts)  # empty buckets → null gap
    finally:
        async with db_pool.acquire() as conn:
            await _reset(conn)


async def test_volume_metric_and_top_n_cap(db_pool):
    async with db_pool.acquire() as conn:
        await _reset(conn)
        # 900 output tokens in one 900s bucket → 60 tok/min.
        await _seed_call(conn, model="big:70b", output_tokens=900, duration_ms=30_000)
        await _seed_call(conn, model="small:7b", output_tokens=90, duration_ms=3_000)
    try:
        out = await get_llm_throughput_trend(
            db_pool, range_seconds=3600, step_seconds=900, metric="volume", max_models=1
        )
        assert out["metric"] == "volume"
        # top-1 by output volume wins the single series slot
        assert [s["label"] for s in out["series"]] == ["big:70b"]
        vals = [v for _, v in out["series"][0]["points"] if v is not None]
        assert vals == [60.0]
    finally:
        async with db_pool.acquire() as conn:
            await _reset(conn)
