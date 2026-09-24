"""Unit tests for ``services/jobs/probe_fanout_dataset_health.py``.

The featured fan-out exists to collect the Phase-2 router's training data, and
the 2026-08-27 audit (poindexter#1032) found the dataset did not measure what
the router needs: 27% of candidate scores lost to judge truncation, losing
renders discarded to the worker's /tmp, and only 42% of recorded wins actually
comparative judge preferences. Both defects are fixed — this probe exists
because nothing watched either, and a total judge failure still looks identical
to a healthy run in the row.

Pool mocked. ``emit_finding`` patched so routing intent is asserted without
touching audit_log.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.jobs.probe_fanout_dataset_health import (
    ProbeFanoutDatasetHealthJob,
    classify_row,
    summarize,
)
from poindexter.services.site_config import SiteConfig

pytestmark = pytest.mark.unit

_MODULE = "poindexter.services.jobs.probe_fanout_dataset_health"


def _make_pool(rows: list[dict] | None = None) -> Any:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _row(scores: list[float], *, n: int | None = None, urls: int | None = None,
         judge_ran: bool = True) -> dict:
    n = len(scores) if n is None else n
    return {
        "judge_ran": judge_ran,
        "n_candidates": n,
        "scores": scores,
        "candidates_with_url": n if urls is None else urls,
    }


class TestClassifyRow:
    """Only ``judge_win`` is a comparative preference. Everything else records
    a winner chosen by image_fanout_priority order or by there being nothing to
    compare — and they enter the router dataset looking identical."""

    def test_distinct_top_score_is_a_judge_win(self):
        assert classify_row(True, [95.0, 40.0]) == "judge_win"

    def test_equal_top_scores_fall_back_to_priority_order(self):
        assert classify_row(True, [95.0, 95.0]) == "tie_broken_by_priority"

    def test_three_way_tie_at_the_top_is_still_priority(self):
        assert classify_row(True, [90.0, 90.0, 20.0]) == "tie_broken_by_priority"

    def test_single_scored_candidate_reflects_judge_availability(self):
        """13 of the audit's 48 rows were this — siblings rendered fine but
        went unscored, so the 'win' measured whether the judge answered."""
        assert classify_row(True, [88.0]) == "only_scored_candidate"

    def test_no_scores_at_all(self):
        assert classify_row(True, []) == "no_scores"

    def test_judge_down_is_distinct_from_judge_silent(self):
        assert classify_row(False, []) == "judge_down"


class TestSummarize:
    def test_healthy_window(self):
        s = summarize([_row([95.0, 40.0]), _row([88.0, 20.0, 10.0])])
        assert s["unscored_pct"] == 0.0
        assert s["url_coverage_pct"] == 100.0
        assert s["judge_win_pct"] == 100.0

    def test_unscored_candidates_are_counted_not_treated_as_zero(self):
        """A NULL score is the failure being measured — scoring it 0 would make
        a truncated judge look like a judge that hated the image."""
        s = summarize([_row([95.0], n=4)])
        assert s["candidates"] == 4
        assert s["unscored_candidates"] == 3
        assert s["unscored_pct"] == 75.0

    def test_url_coverage_tracks_retrievability(self):
        s = summarize([_row([95.0, 40.0], urls=1)])
        assert s["url_coverage_pct"] == 50.0

    def test_reproduces_the_issue_window_shape(self):
        """Sanity against the real audit: a window dominated by single-scored
        rows reports a low comparative-preference rate."""
        rows = [_row([90.0], n=3) for _ in range(3)] + [_row([95.0, 40.0])]
        s = summarize(rows)
        assert s["judge_win_pct"] == 25.0
        assert s["provenance"]["only_scored_candidate"] == 3

    def test_empty_window_does_not_divide_by_zero(self):
        s = summarize([])
        assert s["candidates"] == 0
        assert s["unscored_pct"] == 0.0
        assert s["judge_win_pct"] == 0.0


class TestProbeRun:
    async def test_healthy_window_is_quiet(self):
        """The live 7-day shape as measured on prod: 0% unscored, 100% urls."""
        pool = _make_pool([_row([95.0, 40.0, 20.0]) for _ in range(9)])
        with patch(f"{_MODULE}.emit_finding") as emit:
            result = await ProbeFanoutDatasetHealthJob().run(
                pool, {"_site_config": SiteConfig()},
            )
        assert result.ok and emit.call_count == 0
        assert result.metrics["unscored_pct"] == 0.0
        assert result.metrics["url_coverage_pct"] == 100.0

    async def test_unscored_spike_emits(self):
        """The regression that already happened — 27% of scores lost to the
        vision judge's think-trace eating its token budget."""
        pool = _make_pool([_row([95.0], n=3) for _ in range(9)])
        with patch(f"{_MODULE}.emit_finding") as emit:
            result = await ProbeFanoutDatasetHealthJob().run(
                pool, {"_site_config": SiteConfig()},
            )
        assert result.ok and emit.call_count == 1
        kw = emit.call_args.kwargs
        assert kw["kind"] == "image_fanout_dataset_degraded"
        assert kw["severity"] == "warn"
        assert "unparseable" in kw["body"]

    async def test_candidates_losing_their_url_emits(self):
        """Defect 1's regression guard: losing renders that are no longer
        retrievable make every score in the dataset unauditable."""
        pool = _make_pool([_row([95.0, 40.0, 20.0], urls=1) for _ in range(9)])
        with patch(f"{_MODULE}.emit_finding") as emit:
            await ProbeFanoutDatasetHealthJob().run(
                pool, {"_site_config": SiteConfig()},
            )
        assert emit.call_count == 1
        assert "auditable" in emit.call_args.kwargs["body"]

    async def test_low_judge_win_rate_reports_but_never_pages(self):
        """A window of pure ties is a MISCALIBRATED judge, not a broken one —
        that is a prompt decision an operator makes, so it is a metric, not an
        alert. The live 7-day window sits at 38.5% for exactly this reason."""
        pool = _make_pool([_row([95.0, 95.0]) for _ in range(12)])
        with patch(f"{_MODULE}.emit_finding") as emit:
            result = await ProbeFanoutDatasetHealthJob().run(
                pool, {"_site_config": SiteConfig()},
            )
        assert emit.call_count == 0, "calibration must not page"
        assert result.metrics["judge_win_pct"] == 0.0
        assert result.metrics["rows_tie_broken_by_priority"] == 12

    async def test_below_sample_floor_gives_no_verdict(self):
        """The fan-out makes ~3 rows/day, so a quiet week must report rather
        than page at 0/2."""
        pool = _make_pool([_row([95.0], n=2)])
        with patch(f"{_MODULE}.emit_finding") as emit:
            result = await ProbeFanoutDatasetHealthJob().run(
                pool, {"_site_config": SiteConfig()},
            )
        assert result.ok and emit.call_count == 0
        assert result.metrics["below_sample_floor"] is True

    async def test_query_failure_is_reported_not_raised(self):
        pool = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("pg down"))
        pool.acquire = MagicMock(return_value=ctx)
        result = await ProbeFanoutDatasetHealthJob().run(
            pool, {"_site_config": SiteConfig()},
        )
        assert result.ok is False and "query failed" in result.detail

    async def test_disabled_switch_is_silent(self):
        pool = _make_pool([_row([95.0], n=3) for _ in range(9)])
        sc = SiteConfig(initial_config={"image_fanout_probe_enabled": "false"})
        with patch(f"{_MODULE}.emit_finding") as emit:
            result = await ProbeFanoutDatasetHealthJob().run(pool, {"_site_config": sc})
        assert result.ok and emit.call_count == 0
        assert "disabled" in result.detail

    async def test_no_pool_is_reported(self):
        result = await ProbeFanoutDatasetHealthJob().run(None, {})
        assert result.ok is False


# ---------------------------------------------------------------------------
# Text-scan signal (2026-09-23) — every fan-out candidate is now OCR-scanned
# before judging. The scan fails OPEN (an unavailable scanner must not become
# "no hero image"), so a dark scanner and a clean dataset read identically in
# the row counts. This probe is the watcher: a check that scanned nothing has
# not passed.
# ---------------------------------------------------------------------------


def _scanned_row(scores: list[float], *, unverified: int = 0, excluded: int = 0,
                 disabled: int = 0) -> dict:
    row = _row(scores)
    total = len(scores) + excluded
    row.update({
        "text_scan_era": True,
        "n_excluded": excluded,
        "text_scan_expected": total - disabled,
        "text_scan_unverified": unverified,
    })
    return row


class TestTextScanSignal:
    def test_pre_scan_rows_are_not_held_to_the_scan(self):
        """Rows written before the scan existed carry no `excluded` array —
        counting them as unverified would page for a week after deploy."""
        s = summarize([_row([95.0, 40.0]) for _ in range(5)])
        assert s["text_scan_expected"] == 0
        assert s["text_scan_unverified_pct"] == 0.0

    def test_unverified_share_is_counted(self):
        s = summarize([_scanned_row([95.0, 40.0], unverified=1)])
        assert s["text_scan_expected"] == 2
        assert s["text_scan_unverified_pct"] == 50.0

    def test_all_excluded_row_is_no_contest_not_judge_down(self):
        row = _scanned_row([], excluded=4)
        row["judge_ran"] = False
        s = summarize([row])
        assert s["provenance"] == {"no_contest": 1}
        assert s["text_scan_excluded"] == 4

    async def test_dark_scanner_emits(self):
        rows = [_scanned_row([95.0, 40.0, 20.0], unverified=3) for _ in range(9)]
        with patch(f"{_MODULE}.emit_finding") as emit:
            result = await ProbeFanoutDatasetHealthJob().run(
                _make_pool(rows), {"_site_config": SiteConfig()},
            )
        assert result.ok and emit.call_count == 1
        body = emit.call_args.kwargs["body"]
        assert "without a verified text scan" in body
        assert "rebuild" in body

    async def test_healthy_scan_is_quiet(self):
        rows = [_scanned_row([95.0, 40.0, 20.0], excluded=1) for _ in range(9)]
        with patch(f"{_MODULE}.emit_finding") as emit:
            result = await ProbeFanoutDatasetHealthJob().run(
                _make_pool(rows), {"_site_config": SiteConfig()},
            )
        assert result.ok and emit.call_count == 0
        assert result.metrics["text_scan_unverified_pct"] == 0.0
        assert result.metrics["text_scan_excluded"] == 9

    async def test_small_scanned_sample_gives_no_scan_verdict(self):
        """During rollout the window mixes old and new rows; two unverified
        scans must not page at 2/2."""
        rows = [_row([95.0, 40.0, 20.0]) for _ in range(9)] + [
            _scanned_row([95.0, 40.0], unverified=2),
        ]
        with patch(f"{_MODULE}.emit_finding") as emit:
            await ProbeFanoutDatasetHealthJob().run(
                _make_pool(rows), {"_site_config": SiteConfig()},
            )
        assert emit.call_count == 0

    async def test_threshold_is_db_tunable(self):
        rows = [_scanned_row([95.0, 40.0, 20.0], unverified=1) for _ in range(9)]
        sc = SiteConfig(initial_config={
            "image_fanout_probe_max_text_scan_unavailable_pct": "50",
        })
        with patch(f"{_MODULE}.emit_finding") as emit:
            await ProbeFanoutDatasetHealthJob().run(_make_pool(rows), {"_site_config": sc})
        assert emit.call_count == 0
