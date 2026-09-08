"""services/entity_demand — Wikipedia pageviews as a topic-demand signal."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import entity_demand as ed

# ---------------------------------------------------------------------------
# Pure math + guards
# ---------------------------------------------------------------------------


def test_demand_factor_is_one_below_floor_and_for_unknown():
    assert ed.demand_factor(None, min_views=1000, max_factor=2.0) == 1.0
    assert ed.demand_factor(0, min_views=1000, max_factor=2.0) == 1.0
    assert ed.demand_factor(999, min_views=1000, max_factor=2.0) == 1.0
    assert ed.demand_factor(1000, min_views=1000, max_factor=2.0) == 1.0


def test_demand_factor_rises_log_linearly_and_clamps():
    assert ed.demand_factor(10_000, min_views=1000, max_factor=2.0) == 1.5
    assert ed.demand_factor(100_000, min_views=1000, max_factor=2.0) == 2.0
    assert ed.demand_factor(10_000_000, min_views=1000, max_factor=2.0) == 2.0
    # max_factor <= 1 disables the nudge entirely.
    assert ed.demand_factor(10_000_000, min_views=1000, max_factor=1.0) == 1.0


def test_august_2026_calibration_points():
    # Real Wikimedia numbers for August 2026 (the probe that motivated this).
    s = ed.DemandSettings()
    assert ed.demand_factor(33_958, min_views=s.min_views, max_factor=s.max_factor) > 1.7  # RAG
    assert abs(ed.demand_factor(10_031, min_views=s.min_views, max_factor=s.max_factor) - 1.5) < 0.01  # llama.cpp
    assert ed.demand_factor(3_754, min_views=s.min_views, max_factor=s.max_factor) < 1.3  # GGUF


def test_combined_factor_dual_signal_only_for_google_sources_with_demand():
    s = ed.DemandSettings()
    f, b = ed.combined_factor(10_000, source_name="search_autocomplete", settings=s)
    assert f == 1.875 and b["_dual_signal"] is True and b["_wiki_views"] == 10_000
    f, b = ed.combined_factor(10_000, source_name="hackernews", settings=s)
    assert f == 1.5 and b["_dual_signal"] is False
    # Google source but no Wikipedia demand → no dual bonus, factor 1.0.
    f, b = ed.combined_factor(200, source_name="search_autocomplete", settings=s)
    assert f == 1.0 and b["_dual_signal"] is False
    # Unknown views: factor 1.0, breakdown says None — never 0.
    f, b = ed.combined_factor(None, source_name="search_autocomplete", settings=s)
    assert f == 1.0 and b["_wiki_views"] is None and b["_dual_signal"] is False


def test_article_match_requires_a_shared_content_token():
    assert ed.article_matches_query("rtx 5090 local llm performance", "GeForce RTX 50 series")
    assert ed.article_matches_query("gguf quantization types", "GGUF")
    assert ed.article_matches_query("llama.cpp vs vllm vs sglang", "Llama.cpp")
    # Fuzzy search returns *something* for a mood; a shared content token is
    # the proof — and number words / "days" are stoplisted so a film cannot
    # lend its pageviews to "the five days nobody was watching".
    assert not ed.article_matches_query("the five days nobody was watching", "Five Days (film)")
    assert not ed.article_matches_query("the gap nobody names", "Gap Inc.")
    assert not ed.article_matches_query("why builders ignore money", "Ignorance")
    # Acronyms are distinctive even at three letters; plain 3-letter words are not.
    assert ed.article_matches_query("rtx 5090 vs 4090", "RTX")
    assert not ed.article_matches_query("the gap year", "Gap")
    assert ed.distinctive_tokens("Llama.cpp") >= {"llama.cpp", "llama"}


@pytest.mark.asyncio
async def test_no_client_outside_lifespan_is_unknown_not_egress(monkeypatch):
    from services import http_client as hc

    monkeypatch.setattr(hc, "http_client", None)
    assert ed.WikipediaDemandScorer.shared_client_available() is False
    scorer = ed.WikipediaDemandScorer(settings=ed.DemandSettings(), pool=None)
    d = await scorer.demand_for("gguf quantization types")
    assert d.monthly_views is None and d.wiki_title is None


def test_resolution_candidates_are_entity_shaped_first():
    c = ed.resolution_candidates("Rtx 5090 Local Llm Performance")
    assert c[:3] == ["rtx 5090 llm performance", "rtx 5090", "5090"]
    assert "performance" not in c  # a plain single word is never searched alone
    c = ed.resolution_candidates("Llama.Cpp Vs Vllm Vs Sglang")
    assert c[:2] == ["llama.cpp vllm sglang", "llama.cpp"]
    c = ed.resolution_candidates("FastAPI best practices")
    assert c[:2] == ["fastapi practices", "fastapi"]  # mixed-case token outranks windows
    c = ed.resolution_candidates("Why Cosine Similarity Quietly Throws Away Information")
    assert c[1] == "cosine similarity" and "information" not in c  # 2-grams right after the phrase
    assert ed.resolution_candidates("The Stuck Task") == ["stuck task"]
    assert ed.resolution_candidates("Information") == []
    assert ed.resolution_candidates("") == []


def test_accept_hit_rules():
    assert ed.accept_hit("rtx 5090", "GeForce RTX 50 series")   # digit phrase: family-first names allowed
    assert ed.accept_hit("llama.cpp", "Llama.cpp")
    assert ed.accept_hit("fastapi", "FastAPI")
    assert ed.accept_hit("vllm", "VLLM")
    assert ed.accept_hit("cosine similarity", "Cosine similarity")
    assert ed.accept_hit("spring boot", "Spring Boot")
    assert ed.accept_hit("retrieval augmented generation", "Retrieval-augmented generation")
    assert ed.accept_hit("gguf quantization types", "GGUF")
    assert ed.accept_hit("stuck", "Stuck (2017 film)")  # parenthetical stripped; known residual
    # Merely mentioned, not what the article is about.
    assert not ed.accept_hit("minus signs", "Plus and minus signs")
    assert not ed.accept_hit("locally mac", "MAC address")
    assert not ed.accept_hit("agent memory", "AI agent")
    assert not ed.accept_hit("vram", "Video random-access memory")
    # Only a generic concept word in common.
    assert not ed.accept_hit("performance memory", "Memory")
    assert not ed.accept_hit("llm performance", "Performance")
    assert not ed.accept_hit("gap names", "Gap Inc.")
    assert not ed.accept_hit("five days watching", "Five Days (film)")


def test_years_are_dates_not_entities():
    c = ed.resolution_candidates("Spring Boot admiration score Java 2026")
    assert "java 2026" not in c and "2026" not in c
    assert "spring boot" in c


def test_content_tokens_keep_digits_and_drop_stopwords():
    toks = ed.content_tokens("Best local LLM for 16GB VRAM in 2026, five days")
    assert "16gb" in toks and "2026" in toks and "vram" in toks and "llm" in toks
    assert "for" not in toks and "best" not in toks and "local" not in toks
    assert "five" not in toks and "days" not in toks


def test_pageviews_window_is_last_30_complete_days():
    start, end = ed.pageviews_window(datetime(2026, 9, 8, 12, 0, tzinfo=UTC))
    assert (start, end) == ("20260809", "20260907")


def test_settings_from_site_config_reads_every_key_and_tolerates_stubs():
    sc = MagicMock()
    values = {
        ed.ENABLED_KEY: True, ed.MIN_VIEWS_KEY: 500, ed.MAX_FACTOR_KEY: 3.0,
        ed.LANG_KEY: "de", ed.CACHE_DAYS_KEY: 2, ed.TIMEOUT_KEY: 2.5,
        ed.DUAL_FACTOR_KEY: 1.1, ed.GOOGLE_SOURCES_KEY: "a, b", ed.CONCURRENCY_KEY: 2,
    }
    sc.get_bool.side_effect = lambda k, d=False: values.get(k, d)
    sc.get_int.side_effect = lambda k, d=0: values.get(k, d)
    sc.get_float.side_effect = lambda k, d=0.0: values.get(k, d)
    sc.get.side_effect = lambda k, d="": values.get(k, d)
    s = ed.DemandSettings.from_site_config(sc)
    assert (s.min_views, s.max_factor, s.lang, s.cache_days, s.timeout_s) == (500, 3.0, "de", 2, 2.5)
    assert s.google_sources == frozenset({"a", "b"}) and s.concurrency == 2
    assert ed.DemandSettings.from_site_config(None) == ed.DemandSettings()


def test_defaults_are_seeded_and_categorised():
    from services.settings_categories import resolve_category
    from services.settings_defaults import DEFAULTS, METADATA

    for k in (ed.ENABLED_KEY, ed.MIN_VIEWS_KEY, ed.MAX_FACTOR_KEY, ed.LANG_KEY,
              ed.CACHE_DAYS_KEY, ed.TIMEOUT_KEY, ed.DUAL_FACTOR_KEY,
              ed.GOOGLE_SOURCES_KEY, ed.CONCURRENCY_KEY, ed.USER_AGENT_KEY):
        assert k in DEFAULTS and k in METADATA, k
        assert resolve_category(k) != "general", k
    assert DEFAULTS[ed.ENABLED_KEY] == "true"


# ---------------------------------------------------------------------------
# Scorer with a fake transport + fake pool
# ---------------------------------------------------------------------------


class _FakeClient:
    """Answers the two Wikimedia endpoints; records calls."""

    def __init__(self, *, search_title: str | None = "Llama.cpp", views: list[int] | None = None,
                 raise_on: str | None = None):
        self.search_title = search_title
        self.views = views if views is not None else [300] * 30
        self.raise_on = raise_on
        self.calls: list[str] = []

    async def get(self, url: str, params: Any = None, headers: Any = None, timeout: Any = None):
        self.calls.append(url)
        assert headers and "User-Agent" in headers and "topic-demand-scorer" in headers["User-Agent"]
        if self.raise_on and self.raise_on in url:
            raise ConnectionError("boom")
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "w/api.php" in url:
            resp.json = MagicMock(return_value={"query": {"search": ([{"title": self.search_title}] if self.search_title else [])}})
        else:
            resp.json = MagicMock(return_value={"items": [{"views": v} for v in self.views]})
        return resp


class _FakePool:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.fetchrow = AsyncMock(side_effect=self._fetchrow)
        self.execute = AsyncMock(side_effect=self._execute)

    async def _fetchrow(self, sql, key, days):
        return self.rows.get(key)

    async def _execute(self, sql, key, lang, title, views):
        self.rows[key] = {"query_key": key, "wiki_title": title, "monthly_views": views, "fetched_at": None}


@pytest.mark.asyncio
async def test_scorer_resolves_search_then_pageviews_and_caches():
    client = _FakeClient(search_title="Llama.cpp", views=[334] * 30)
    pool = _FakePool()
    scorer = ed.WikipediaDemandScorer(settings=ed.DemandSettings(), pool=pool, http_client=client)
    d = await scorer.demand_for("llama.cpp vs vllm vs sglang")
    assert d.wiki_title == "Llama.cpp" and d.monthly_views == 334 * 30 and d.cached is False
    assert len(client.calls) == 2 and "per-article/en.wikipedia" in client.calls[1]
    assert "Llama.cpp" in client.calls[1]
    # Second call is served from the cache — no HTTP.
    d2 = await scorer.demand_for("Llama.cpp VS vLLM vs SGLang")
    assert d2.cached is True and d2.monthly_views == 334 * 30
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_no_matching_article_is_unknown_and_cached():
    client = _FakeClient(search_title="Gap Inc.")
    pool = _FakePool()
    scorer = ed.WikipediaDemandScorer(settings=ed.DemandSettings(), pool=pool, http_client=client)
    d = await scorer.demand_for("the gap nobody names")
    assert d.wiki_title is None and d.monthly_views is None
    assert not any("per-article" in u for u in client.calls)  # no pageviews call for rejected hits
    assert pool.rows[ed.cache_key("the gap nobody names", "en")]["monthly_views"] is None
    n = len(client.calls)
    d2 = await scorer.demand_for("the gap nobody names")
    assert d2.cached is True and len(client.calls) == n


@pytest.mark.asyncio
async def test_transport_failure_is_unknown_and_not_cached():
    client = _FakeClient(raise_on="w/api.php")
    pool = _FakePool()
    scorer = ed.WikipediaDemandScorer(settings=ed.DemandSettings(), pool=pool, http_client=client)
    d = await scorer.demand_for("gguf quantization types")
    assert d.monthly_views is None and d.cached is False
    assert pool.rows == {}  # retry next sweep


@pytest.mark.asyncio
async def test_demand_for_many_dedups_and_bounds_concurrency():
    client = _FakeClient(search_title="GGUF", views=[125] * 30)
    scorer = ed.WikipediaDemandScorer(settings=ed.DemandSettings(concurrency=2), pool=None, http_client=client)
    out = await scorer.demand_for_many(["gguf quantization types", "GGUF  quantization types", "", "gguf vs awq"])
    assert set(out) == {"gguf quantization types", "GGUF quantization types", "gguf vs awq"}
    assert all(v.monthly_views == 3750 for v in out.values())


@pytest.mark.asyncio
async def test_cache_disabled_when_days_is_zero():
    client = _FakeClient()
    pool = _FakePool()
    scorer = ed.WikipediaDemandScorer(settings=ed.DemandSettings(cache_days=0), pool=pool, http_client=client)
    await scorer.demand_for("llama.cpp")
    assert pool.rows == {} and not pool.fetchrow.await_count
