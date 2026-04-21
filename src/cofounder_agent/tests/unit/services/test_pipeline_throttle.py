"""
Unit tests for services/pipeline_throttle.py

Covers (per GH-89):
- is_queue_full reads max_approval_queue from site_config (DB-first)
- State transitions (inactive → active → inactive) update counters correctly
- get_state exposes active / total_seconds / queue_size / queue_limit
- Metric toggles on/off as queue size crosses the limit
- DB errors don't poison the caller — returns (False, 0, limit)
- reset_for_tests clears module state between tests
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import pipeline_throttle
from services.pipeline_throttle import (
    get_state,
    is_queue_full,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_throttle_state():
    """Every test starts with a fresh module-level state."""
    reset_for_tests()
    yield
    reset_for_tests()


def _make_pool(count: int):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"c": count})
    return pool


def _make_failing_pool():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
    return pool


def _patch_site_config(max_queue: int):
    """Patch site_config.get_int to return a controlled max_approval_queue."""
    mock_cfg = MagicMock()
    mock_cfg.get_int = MagicMock(
        side_effect=lambda key, default=0: max_queue if key == "max_approval_queue" else default
    )
    return patch("services.site_config.site_config", mock_cfg)


@pytest.mark.unit
class TestInitialState:
    def test_default_state_is_inactive(self):
        s = get_state()
        assert s["active"] is False
        assert s["active_seconds"] == 0.0
        assert s["total_seconds"] == 0.0
        assert s["queue_size"] == 0
        assert s["queue_limit"] == 0


@pytest.mark.unit
class TestIsQueueFull:
    @pytest.mark.asyncio
    async def test_queue_below_limit_returns_false(self):
        with _patch_site_config(3):
            full, size, limit = await is_queue_full(_make_pool(2))
        assert full is False
        assert size == 2
        assert limit == 3

    @pytest.mark.asyncio
    async def test_queue_at_limit_returns_true(self):
        with _patch_site_config(3):
            full, size, limit = await is_queue_full(_make_pool(3))
        assert full is True
        assert size == 3
        assert limit == 3

    @pytest.mark.asyncio
    async def test_queue_above_limit_returns_true(self):
        with _patch_site_config(3):
            full, size, limit = await is_queue_full(_make_pool(7))
        assert full is True
        assert size == 7
        assert limit == 3

    @pytest.mark.asyncio
    async def test_no_pool_returns_false(self):
        with _patch_site_config(3):
            full, size, limit = await is_queue_full(None)
        assert full is False
        assert size == 0
        assert limit == 3

    @pytest.mark.asyncio
    async def test_db_error_returns_not_full_silently(self):
        """DB hiccup must NOT mark the pipeline throttled — otherwise a
        flaky connection poisons the whole queue (GH-89 observation (b))."""
        with _patch_site_config(3):
            full, size, limit = await is_queue_full(_make_failing_pool())
        assert full is False
        assert size == 0
        assert limit == 3


@pytest.mark.unit
class TestMetricToggle:
    @pytest.mark.asyncio
    async def test_active_gauge_flips_on_when_queue_fills(self):
        with _patch_site_config(2):
            assert get_state()["active"] is False
            await is_queue_full(_make_pool(2))
            assert get_state()["active"] is True
            # queue_size/limit reported accurately
            state = get_state()
            assert state["queue_size"] == 2
            assert state["queue_limit"] == 2

    @pytest.mark.asyncio
    async def test_active_gauge_flips_off_when_queue_drains(self):
        with _patch_site_config(2):
            await is_queue_full(_make_pool(2))
            assert get_state()["active"] is True
            await is_queue_full(_make_pool(1))
            assert get_state()["active"] is False

    @pytest.mark.asyncio
    async def test_active_state_is_idempotent(self):
        """Two consecutive full-queue checks keep a single active interval."""
        with _patch_site_config(2):
            await is_queue_full(_make_pool(2))
            first_active_since = pipeline_throttle._STATE.active_since_ts
            await is_queue_full(_make_pool(2))
            second_active_since = pipeline_throttle._STATE.active_since_ts
        assert first_active_since is not None
        assert first_active_since == second_active_since


@pytest.mark.unit
class TestCounter:
    @pytest.mark.asyncio
    async def test_total_seconds_accumulates_across_intervals(self):
        """Throttled intervals add to pipeline_throttle_seconds_total."""
        fake_now = [1000.0]

        def _tick():
            return fake_now[0]

        with _patch_site_config(2), patch.object(pipeline_throttle, "_now", side_effect=_tick):
            # Start throttled at t=1000
            await is_queue_full(_make_pool(2))
            # Advance 5s while still throttled
            fake_now[0] = 1005.0
            # Drop below limit — closes the first interval at 5.0s
            await is_queue_full(_make_pool(1))
            state = get_state()
            assert state["active"] is False
            assert abs(state["total_seconds"] - 5.0) < 0.01

            # Throttled again at t=1010, observe total includes the open interval
            fake_now[0] = 1010.0
            await is_queue_full(_make_pool(2))
            fake_now[0] = 1013.0
            state = get_state()
            assert state["active"] is True
            # Previous 5.0s + ongoing 3.0s
            assert abs(state["total_seconds"] - 8.0) < 0.01

    @pytest.mark.asyncio
    async def test_total_seconds_monotonic_non_decreasing(self):
        fake_now = [2000.0]
        with _patch_site_config(2), patch.object(pipeline_throttle, "_now", side_effect=lambda: fake_now[0]):
            await is_queue_full(_make_pool(2))
            fake_now[0] = 2010.0
            await is_queue_full(_make_pool(1))
            total_after_first = get_state()["total_seconds"]
            # A round of non-throttled checks should not decrease the counter
            fake_now[0] = 2020.0
            await is_queue_full(_make_pool(0))
            fake_now[0] = 2030.0
            await is_queue_full(_make_pool(1))
            assert get_state()["total_seconds"] >= total_after_first


@pytest.mark.unit
class TestResetForTests:
    @pytest.mark.asyncio
    async def test_reset_clears_all_state(self):
        with _patch_site_config(2):
            await is_queue_full(_make_pool(5))
        assert get_state()["active"] is True
        reset_for_tests()
        s = get_state()
        assert s["active"] is False
        assert s["total_seconds"] == 0.0
        assert s["queue_size"] == 0
