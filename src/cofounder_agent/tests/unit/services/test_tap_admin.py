"""services/tap_admin.py — add a topic source to a niche without SQL (poindexter#1128).

Everything the tap runner will need is checked before a row is written: a
registered handler, a registered topic source for the topic-source handler,
a known niche, a new name.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.services import tap_admin as ta

pytestmark = pytest.mark.asyncio


@pytest.fixture
def registries(monkeypatch):
    monkeypatch.setattr(ta, "registered_tap_handlers", lambda: {"builtin_topic_source", "singer_subprocess"})
    monkeypatch.setattr(ta, "known_topic_sources", lambda: {"rss", "hackernews", "internal_rag"})


@pytest.fixture
def store(monkeypatch):
    calls: dict = {"upserts": []}

    async def _get_row(pool, surface, name):
        assert surface == "taps"
        return calls.get("existing")

    async def _upsert(pool, surface, payload):
        calls["upserts"].append(payload)
        return {**payload, "id": "row-1"}

    monkeypatch.setattr(ta.dcs, "get_row", _get_row)
    monkeypatch.setattr(ta.dcs, "upsert_row", _upsert)
    return calls


def _niche(slug="glad-labs", niche_id="niche-uuid"):
    service = SimpleNamespace(get_by_slug=AsyncMock(
        return_value=SimpleNamespace(id=niche_id) if slug else None,
    ))
    return patch("poindexter.services.niche_service.NicheService", return_value=service)


async def test_an_rss_feed_for_a_niche_is_written_with_the_niche_id(registries, store):
    with _niche():
        row = await ta.create_tap(
            object(), name="glad-labs_rss_example", tap_type="rss", niche_slug="glad-labs",
            config={"feed_url": "https://example.com/feed"}, schedule=" every 6 hours ",
        )
    (payload,) = store["upserts"]
    assert payload == {
        "name": "glad-labs_rss_example", "handler_name": "builtin_topic_source",
        "tap_type": "rss", "config": {"feed_url": "https://example.com/feed"},
        "enabled": True, "schedule": "every 6 hours", "niche_id": "niche-uuid",
    }
    assert row["id"] == "row-1"


@pytest.mark.parametrize("kwargs, why", [
    ({"tap_type": "rsss"}, "no topic source 'rsss'"),
    ({"handler": "nope"}, "no tap handler 'nope'"),
    ({"niche_slug": ""}, "pass --niche SLUG"),
    ({"name": "bad name!"}, "must start with a letter or digit"),
    ({"tap_type": ""}, "--type is required"),
    ({"config": ["not", "a", "dict"]}, "must be a JSON object"),
])
async def test_what_the_runner_would_trip_on_is_refused_before_writing(registries, store, kwargs, why):
    args = {"name": "t1", "tap_type": "rss", "niche_slug": "glad-labs", **kwargs}
    with _niche(), pytest.raises(ta.TapAdminError, match=why):
        await ta.create_tap(object(), **args)
    assert store["upserts"] == []


async def test_an_unknown_niche_names_the_known_ones(registries, store):
    with _niche(slug=""), patch(
        "poindexter.services.niche_service.get_known_niche_slugs",
        AsyncMock(return_value={"glad-labs", "dev_diary"}),
    ), pytest.raises(ta.TapAdminError, match="known niches: dev_diary, glad-labs"):
        await ta.create_tap(object(), name="t1", tap_type="rss", niche_slug="glad-lab")
    assert store["upserts"] == []


async def test_an_existing_name_is_never_overwritten(registries, store):
    store["existing"] = {"name": "t1"}
    with _niche(), pytest.raises(ta.TapAdminError, match="already exists"):
        await ta.create_tap(object(), name="t1", tap_type="rss", niche_slug="glad-labs")
    assert store["upserts"] == []


async def test_a_non_topic_source_handler_needs_no_niche_and_any_type(registries, store):
    await ta.create_tap(object(), name="ga4", tap_type="tap-google-analytics", handler="singer_subprocess", enabled=False)
    (payload,) = store["upserts"]
    assert payload["handler_name"] == "singer_subprocess" and "niche_id" not in payload
    assert payload["enabled"] is False


async def test_the_real_registries_know_the_sources_prod_runs():
    """Not a stub: every tap_type the operator install runs must validate."""
    sources = ta.known_topic_sources()
    for name in ("rss", "search_autocomplete", "gsc_query_gap", "benchmark_findings",
                 "hackernews", "devto", "web_search", "knowledge", "internal_rag"):
        assert name in sources, name
    assert ta.TOPIC_SOURCE_HANDLER in ta.registered_tap_handlers()
