"""``topic_source_rank_weights`` parsing + lookup (services/topic_ranking)."""

from __future__ import annotations

from services.topic_ranking import parse_rank_weights, source_rank_weight


def test_parses_csv_pairs():
    w = parse_rank_weights("search_autocomplete=1.5, gsc_query_gap=1.5,hackernews=0.9")
    assert w == {"search_autocomplete": 1.5, "gsc_query_gap": 1.5, "hackernews": 0.9}


def test_empty_and_none_give_no_weights():
    assert parse_rank_weights("") == {}
    assert parse_rank_weights(None) == {}
    assert parse_rank_weights(" , ,") == {}


def test_malformed_pairs_are_skipped_not_fatal():
    w = parse_rank_weights("search_autocomplete=1.5,broken,=2,devto=abc,rss=0,x=-1")
    assert w == {"search_autocomplete": 1.5}


def test_unlisted_and_missing_source_default_to_one():
    w = {"search_autocomplete": 1.5}
    assert source_rank_weight("hackernews", w) == 1.0
    assert source_rank_weight(None, w) == 1.0
    assert source_rank_weight("", w) == 1.0
    assert source_rank_weight("search_autocomplete", w) == 1.5


def test_default_setting_boosts_the_two_demand_sources():
    from services.settings_categories import resolve_category
    from services.settings_defaults import DEFAULTS, METADATA

    w = parse_rank_weights(DEFAULTS["topic_source_rank_weights"])
    assert w["search_autocomplete"] > 1.0 and w["gsc_query_gap"] > 1.0
    assert "hackernews" not in w  # a nudge toward demand, not a penalty on discussion
    assert "topic_source_rank_weights" in METADATA
    assert resolve_category("topic_source_rank_weights") != "general"
