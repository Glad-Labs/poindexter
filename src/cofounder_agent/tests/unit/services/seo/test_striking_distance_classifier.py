"""Unit tests for the pure striking-distance classifier (no DB)."""

from __future__ import annotations

from services.seo.striking_distance import (
    DEFAULT_THRESHOLDS,
    classify_opportunity,
    compute_gap_score,
)


def _metrics(impressions: int, clicks: int, position: float) -> dict:
    ctr = (clicks / impressions) if impressions else 0.0
    return {"impressions": impressions, "clicks": clicks, "ctr": ctr, "position": position}


def test_page1_push_takes_priority_over_striking():
    # pos 6, 500 impressions → qualifies for BOTH push (3-10) and striking
    # (5-20); push wins (highest priority).
    opp = classify_opportunity(_metrics(500, 4, 6.0), DEFAULT_THRESHOLDS)
    assert opp is not None and opp.tier == "page1_push"


def test_striking_distance_when_below_push_band():
    # pos 15 → striking (5-20) but not push (<=10).
    opp = classify_opportunity(_metrics(300, 1, 15.0), DEFAULT_THRESHOLDS)
    assert opp is not None and opp.tier == "striking_distance"


def test_low_ctr_when_ranking_outside_bands_with_no_clicks():
    # pos 25 (outside push+striking), 2000 impressions, 0 clicks → low_ctr.
    opp = classify_opportunity(_metrics(2000, 0, 25.0), DEFAULT_THRESHOLDS)
    assert opp is not None and opp.tier == "low_ctr"


def test_no_opportunity_when_winning_already():
    # pos 1.5, healthy CTR → nothing to harvest.
    assert classify_opportunity(_metrics(1000, 300, 1.5), DEFAULT_THRESHOLDS) is None


def test_no_opportunity_when_below_volume_floor():
    # pos 25 but only 10 impressions and a decent ctr → not worth flagging.
    assert classify_opportunity(_metrics(10, 1, 25.0), DEFAULT_THRESHOLDS) is None


def test_push_requires_min_impressions():
    # pos 6 but only 5 impressions → below push floor; falls through to
    # striking (5-20 covers pos 6), which is the correct softer classification.
    opp = classify_opportunity(_metrics(5, 0, 6.0), DEFAULT_THRESHOLDS)
    assert opp is not None and opp.tier == "striking_distance"


def test_none_position_is_not_an_opportunity():
    assert classify_opportunity(_metrics(500, 0, None), DEFAULT_THRESHOLDS) is None  # type: ignore[arg-type]


def test_gap_score_rewards_more_impressions():
    low = compute_gap_score(_metrics(100, 1, 8.0), DEFAULT_THRESHOLDS)
    high = compute_gap_score(_metrics(5000, 5, 8.0), DEFAULT_THRESHOLDS)
    assert high > low > 0


def test_gap_score_zero_when_ctr_already_above_target():
    # ctr 0.10 > target 0.05 → no gap.
    assert compute_gap_score(_metrics(1000, 100, 2.0), DEFAULT_THRESHOLDS) == 0.0


def test_thresholds_are_tunable():
    strict = {**DEFAULT_THRESHOLDS, "striking_position_max": 10.0}
    # pos 15 no longer striking under the tighter band; only 50 impressions so
    # not low_ctr either → no opportunity.
    assert classify_opportunity(_metrics(50, 0, 15.0), strict) is None
