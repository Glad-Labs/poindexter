"""Unit tests for ``services/watermark_cursor.py`` (stack#3523).

The rule is shared by both CF Analytics Engine ingests, so it is tested once,
here, rather than twice against whichever job happens to call it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.watermark_cursor import (
    DEFAULT_INGESTION_LAG_SECONDS,
    is_future_cursor,
    next_high_water,
)


def _at(hhmmss: str) -> datetime:
    """2026-08-31 <hh:mm:ss> UTC — the day the loss was caught in the wild."""
    return datetime.strptime(
        f"2026-08-31 {hhmmss}", "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)


@pytest.mark.unit
class TestNextHighWater:
    NOW = _at("19:33:09")

    def test_empty_response_stops_short_of_now(self):
        """The bug in one assertion: an empty poll used to jump to now(),
        stepping over every row still inside its ingestion delay."""
        assert next_high_water(
            since=_at("19:28:00"),
            observed_max=None,
            now=self.NOW,
            lag_margin_seconds=300,
        ) == _at("19:28:09")

    def test_observed_max_is_capped_at_the_horizon(self):
        """A row that ingested fast does NOT license a jump to its timestamp —
        visibility is not strictly ordered, so an older row can still be in
        flight behind it."""
        assert next_high_water(
            since=_at("19:20:00"),
            observed_max=_at("19:32:46"),
            now=self.NOW,
            lag_margin_seconds=300,
        ) == _at("19:28:09")

    def test_observed_max_below_horizon_is_used_as_is(self):
        assert next_high_water(
            since=_at("19:00:00"),
            observed_max=_at("19:10:00"),
            now=self.NOW,
            lag_margin_seconds=300,
        ) == _at("19:10:00")

    def test_never_moves_backwards(self):
        """A cursor already ahead of the horizon — written by the pre-fix code,
        or after the margin was widened — must STALL, never rewind into an
        ever-widening re-scan."""
        assert next_high_water(
            since=_at("19:31:00"),
            observed_max=None,
            now=self.NOW,
            lag_margin_seconds=300,
        ) == _at("19:31:00")

    def test_stale_observed_max_does_not_rewind_the_cursor(self):
        """A batch whose newest row predates the cursor leaves it alone."""
        assert next_high_water(
            since=_at("19:31:00"),
            observed_max=_at("18:00:00"),
            now=self.NOW,
            lag_margin_seconds=300,
        ) == _at("19:31:00")

    def test_zero_margin_restores_pre_fix_behaviour(self):
        """The escape hatch: margin 0 collapses the horizon onto now()."""
        assert next_high_water(
            since=_at("19:28:00"),
            observed_max=None,
            now=self.NOW,
            lag_margin_seconds=0,
        ) == self.NOW

    def test_negative_margin_is_clamped_not_inverted(self):
        assert next_high_water(
            since=_at("19:28:00"),
            observed_max=None,
            now=self.NOW,
            lag_margin_seconds=-600,
        ) == self.NOW

    def test_default_margin_is_one_poll_interval(self):
        """Both callers run every 5 minutes; the default margin matches, so the
        re-pull overlap is exactly one cycle."""
        assert DEFAULT_INGESTION_LAG_SECONDS == 300


@pytest.mark.unit
class TestIsFutureCursor:
    """Monotonicity means a corrupt future cursor can never self-heal, so it
    has to be REJECTED at read time instead (the pre-fix code repaired one by
    accident, by overwriting it with `now()` on the next empty poll)."""

    NOW = _at("19:33:09")

    def test_future_cursor_is_rejected(self):
        assert is_future_cursor(_at("19:40:00"), self.NOW) is True

    def test_past_cursor_is_fine(self):
        assert is_future_cursor(_at("19:30:00"), self.NOW) is False

    def test_now_is_not_future(self):
        assert is_future_cursor(self.NOW, self.NOW) is False


@pytest.mark.unit
class TestBothIngestsShareTheRule:
    """Neither CF ingest may grow its own copy of the rule.

    The two jobs already share their transient-network posture for the same
    reason (stack#3161): a fault, or a fix, that reaches only half the ingest
    surface is worse than one that reaches neither, because the half that
    still works makes the gap invisible.
    """

    @pytest.mark.parametrize(
        "job_module",
        ["sync_cloudflare_analytics", "sync_affiliate_clicks"],
    )
    def test_job_binds_the_shared_helper(self, job_module: str):
        import importlib

        mod = importlib.import_module(f"services.jobs.{job_module}")
        src = Path(mod.__file__).read_text(encoding="utf-8")

        assert "from services.watermark_cursor import" in src
        # A future cursor must be rejected at read time — monotonicity means
        # it can never repair itself.
        assert "is_future_cursor(" in src
        # Both watermark writes must go through the rule — an ``isoformat()``
        # on a bare ``datetime.now()`` or a raw ``max_ts`` is the bug itself.
        assert "next_high_water(" in src
        assert "datetime.now(timezone.utc).isoformat()" not in src
        assert "max_ts.isoformat()" not in src
