"""Unit tests for ``brain/health_probes.py:probe_gpu_temperature``.

Closes #236 — the ``gpu_temperature_high_threshold_c`` app_setting was
seeded by the 2026-02-07 baseline but had no consumer until this probe
landed. Tests pin the threshold-reading + alert semantics so future
edits to the row name or default don't silently break the alert.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from brain.health_probes import probe_gpu_temperature


def _make_pool(*, gpu_row, threshold_row):
    """asyncpg pool stub. ``fetchrow`` is sequenced — the probe calls
    it for gpu_metrics first, threshold second."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=[gpu_row, threshold_row])
    return pool


@pytest.mark.unit
@pytest.mark.asyncio
async def test_temperature_below_threshold_is_ok():
    pool = _make_pool(
        gpu_row={"temperature": 72, "timestamp": "2026-05-27T13:00:00Z"},
        threshold_row={"value": "85"},
    )
    result = await probe_gpu_temperature(pool)
    assert result["ok"] is True
    assert result["temperature_c"] == 72
    assert result["threshold_c"] == 85
    assert "72C" in result["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_temperature_above_threshold_alerts():
    """The original ask in #236 — the probe must fail-loud when GPU
    crosses the operator-tuned ceiling. 88 > 85 default → ok=False."""
    pool = _make_pool(
        gpu_row={"temperature": 88, "timestamp": "now"},
        threshold_row={"value": "85"},
    )
    result = await probe_gpu_temperature(pool)
    assert result["ok"] is False
    assert result["temperature_c"] == 88
    assert result["threshold_c"] == 85
    assert "88C" in result["detail"]
    assert "85C" in result["detail"]
    # Mention the throttle context so the on-call operator has signal
    assert "throttle" in result["detail"].lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_temperature_equal_to_threshold_is_ok():
    """Threshold semantics: STRICTLY greater than triggers, equality is OK.
    Matches NVIDIA spec where most cards throttle near (not at) the rated
    ceiling — gives operators headroom without flapping at the limit."""
    pool = _make_pool(
        gpu_row={"temperature": 85, "timestamp": "now"},
        threshold_row={"value": "85"},
    )
    result = await probe_gpu_temperature(pool)
    assert result["ok"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_custom_threshold_overrides_default():
    """An operator pushing thermals (overclocked card with strong cooling)
    might raise the threshold to 90 — the probe must honor the row."""
    pool = _make_pool(
        gpu_row={"temperature": 88, "timestamp": "now"},
        threshold_row={"value": "90"},
    )
    result = await probe_gpu_temperature(pool)
    assert result["ok"] is True
    assert result["threshold_c"] == 90


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_threshold_row_falls_back_to_85():
    """Per the baseline seed comment: 85C is the safe default for
    consumer-grade NVIDIA hardware. If the row was hand-deleted, the
    probe must still alert at the documented ceiling rather than
    silently disabling itself."""
    pool = _make_pool(
        gpu_row={"temperature": 90, "timestamp": "now"},
        threshold_row=None,
    )
    result = await probe_gpu_temperature(pool)
    assert result["ok"] is False
    assert result["threshold_c"] == 85


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_gpu_metrics_returns_ok_with_explanatory_detail():
    """Fresh install without nvidia_smi_exporter wired returns no rows.
    The probe must NOT alert — that's a config gap, not an emergency.
    Returns ``ok=True`` with a detail line that helps the operator debug."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=[None])
    result = await probe_gpu_temperature(pool)
    assert result["ok"] is True
    assert "no gpu_metrics rows" in result["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_null_temperature_value_returns_ok_with_detail():
    """gpu_metrics row exists but the temperature column is NULL
    (exporter wrote the row but couldn't reach the GPU). Treat
    same as no rows — config issue, not pager-worthy."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=[
        {"temperature": None, "timestamp": "now"},
    ])
    result = await probe_gpu_temperature(pool)
    assert result["ok"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unparseable_threshold_falls_back_to_default():
    """Operator typo'd '85C' instead of '85' in the row. Probe must
    parse-or-default rather than crash + lose alerting entirely."""
    pool = _make_pool(
        gpu_row={"temperature": 90, "timestamp": "now"},
        threshold_row={"value": "eighty-five"},
    )
    result = await probe_gpu_temperature(pool)
    # Should default to 85 → 90 > 85 → alert
    assert result["ok"] is False
    assert result["threshold_c"] == 85


@pytest.mark.unit
@pytest.mark.asyncio
async def test_db_failure_returns_ok_false_with_detail():
    """Pool exhaustion / DB outage means we can't read the threshold.
    Return ok=False so the brain dispatcher can log + audit the
    probe-itself-broke condition; the detail names the failure path."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("pool exhausted"))
    result = await probe_gpu_temperature(pool)
    assert result["ok"] is False
    assert "gpu_temperature probe failed" in result["detail"]
    assert "pool exhausted" in result["detail"]
