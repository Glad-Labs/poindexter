"""Unit tests for SearchAutocompleteSource — fake HTTP + fake pool, no network.

This is the only topic source that measures search DEMAND (every other one
measures publication or conversation), so the things worth pinning are: that it
never fabricates a volume number, that it can't be switched on by accident, and
that junk suggestions don't become article topics.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from services.topic_sources import search_autocomplete as mod
from services.topic_sources.search_autocomplete import SearchAutocompleteSource


class _Cfg:
    def __init__(self, **v):
        self._v = v

    def get(self, key, default=None):
        return self._v.get(key, default)


BRAND = _Cfg(site_name="Glad Labs", company_name="Glad Labs", site_domain="gladlabs.io")


def _install_fake_http(monkeypatch, suggestions_by_probe: dict[str, list[str]],
                       *, fail: set[str] | None = None):
    """Route the autocomplete endpoint through a transport, no sockets."""
    fail = fail or set()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        seen.append(q)
        if q in fail:
            return httpx.Response(429, text="slow down")
        items = suggestions_by_probe.get(q, [])
        # Real endpoint answers text/javascript with a JSON body.
        return httpx.Response(
            200, text=json.dumps([q, items, [], {}]),
            headers={"content-type": "text/javascript; charset=UTF-8"},
        )

    real_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _factory)
    return seen


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def fetch(self, q, *args):
        return self._rows


class _FakePool:
    def __init__(self, rows=None, raises=False):
        self._rows = rows or []
        self._raises = raises

    def acquire(self):
        if self._raises:
            raise RuntimeError("no such table: external_metrics")
        return _FakeConn(self._rows)


class TestSeeding:
    @pytest.mark.asyncio
    async def test_no_seeds_returns_nothing_and_warns(self, monkeypatch, caplog):
        """Enabled with no seeds must say so — an operator seeing zero topics
        needs to know it is waiting on config, not broken."""
        _install_fake_http(monkeypatch, {})
        src = SearchAutocompleteSource()
        with caplog.at_level("WARNING"):
            topics = await src.extract(None, {"seed_from_gsc_clicks": False})
        assert topics == []
        assert "no seeds" in caplog.text

    @pytest.mark.asyncio
    async def test_expands_configured_seeds(self, monkeypatch):
        seen = _install_fake_http(monkeypatch, {
            "local llm vram": ["local llm vram calculator", "local llm vram requirements"],
        })
        src = SearchAutocompleteSource()
        topics = await src.extract(None, {
            "seeds": ["local llm vram"], "modifiers": [], "seed_from_gsc_clicks": False,
        })
        assert [t.keywords[0] for t in topics] == [
            "local llm vram calculator", "local llm vram requirements",
        ]
        assert seen == ["local llm vram"]

    @pytest.mark.asyncio
    async def test_modifiers_produce_extra_probes(self, monkeypatch):
        seen = _install_fake_http(monkeypatch, {})
        src = SearchAutocompleteSource()
        await src.extract(None, {
            "seeds": ["kv cache"], "modifiers": ["how", "vs"],
            "seed_from_gsc_clicks": False,
        })
        assert set(seen) == {"kv cache", "kv cache how", "kv cache vs"}

    @pytest.mark.asyncio
    async def test_seeds_from_click_earning_gsc_queries(self, monkeypatch):
        """Expanding around what already converts is the point — an operator
        shouldn't have to remember which queries earn clicks."""
        seen = _install_fake_http(monkeypatch, {})
        pool = _FakePool(rows=[{"query": "5090 local llm", "clicks": 1.0}])
        src = SearchAutocompleteSource()
        await src.extract(pool, {"modifiers": []})
        assert seen == ["5090 local llm"]

    @pytest.mark.asyncio
    async def test_gsc_seeding_failure_is_not_fatal(self, monkeypatch):
        """A missing/1broken GSC tap must not take the source down."""
        seen = _install_fake_http(monkeypatch, {})
        src = SearchAutocompleteSource()
        topics = await src.extract(_FakePool(raises=True), {"seeds": ["vram"], "modifiers": []})
        assert topics == []          # no suggestions from the fake endpoint
        assert seen == ["vram"]      # but the configured seed still ran

    @pytest.mark.asyncio
    async def test_gsc_seeds_are_junk_filtered(self, monkeypatch):
        seen = _install_fake_http(monkeypatch, {})
        pool = _FakePool(rows=[{"query": "site:www.gladlabs.io", "clicks": 3.0}])
        src = SearchAutocompleteSource()
        await src.extract(pool, {"modifiers": []})
        assert seen == []


class TestSuggestionFiltering:
    @pytest.mark.asyncio
    async def test_junk_suggestions_are_dropped(self, monkeypatch):
        _install_fake_http(monkeypatch, {"vram": [
            "vram calculator for local llm",     # keep
            "site:gladlabs.io vram",             # operator
            "glad labs vram guide",              # brand navigation
            "https://example.com/vram",          # url
        ]})
        src = SearchAutocompleteSource()
        topics = await src.extract(None, {
            "seeds": ["vram"], "modifiers": [], "seed_from_gsc_clicks": False,
            "_site_config": BRAND,
        })
        assert [t.keywords[0] for t in topics] == ["vram calculator for local llm"]

    @pytest.mark.asyncio
    async def test_short_suggestions_are_dropped(self, monkeypatch):
        """One- and two-word suggestions are navigational or too broad."""
        _install_fake_http(monkeypatch, {"llm": ["llm", "llm vram", "llm vram calculator"]})
        src = SearchAutocompleteSource()
        topics = await src.extract(None, {
            "seeds": ["llm"], "modifiers": [], "seed_from_gsc_clicks": False,
        })
        assert [t.keywords[0] for t in topics] == ["llm vram calculator"]

    @pytest.mark.asyncio
    async def test_duplicate_suggestions_collapse_to_best_rank(self, monkeypatch):
        """The same phrase from two probes keeps its best rank, so a top
        suggestion isn't buried by a worse rank under a modifier probe."""
        # Every suggestion must retain the seed token "vram" or the drift guard
        # removes it before ranking is even considered.
        _install_fake_http(monkeypatch, {
            "vram": ["vram unrelated filler", "vram shared phrase here"],  # rank 1
            "vram how": ["vram shared phrase here"],                       # rank 0
        })
        src = SearchAutocompleteSource()
        topics = await src.extract(None, {
            "seeds": ["vram"], "modifiers": ["how"], "seed_from_gsc_clicks": False,
        })
        by_kw = {t.keywords[0]: t for t in topics}
        assert "vram shared phrase here" in by_kw, f"got {list(by_kw)}"
        assert by_kw["vram shared phrase here"].relevance_score == mod._score_for_rank(0)

    @pytest.mark.asyncio
    async def test_max_topics_is_honoured(self, monkeypatch):
        _install_fake_http(monkeypatch, {
            "vram": [f"vram suggestion number {i}" for i in range(20)],
        })
        src = SearchAutocompleteSource()
        topics = await src.extract(None, {
            "seeds": ["vram"], "modifiers": [], "seed_from_gsc_clicks": False,
            "max_topics": 4,
        })
        assert len(topics) == 4


class TestAutocompleteDriftGuard:
    """Autocomplete wanders. Seeding the brand-adjacent GSC query "glad ai"
    returned "glade air freshener" on a live run — a real query, and utterly
    unrelated. Requiring a shared token with the seed kills that."""

    def test_glad_ai_does_not_retain_glade_air(self):
        assert mod._retains_seed_token("glad ai", "glade air freshener") is False

    @pytest.mark.parametrize("suggestion", [
        "local llm vram calculator", "how much vram for local llm", "vram local llm 16gb",
    ])
    def test_genuine_expansions_are_kept(self, suggestion):
        assert mod._retains_seed_token("local llm vram", suggestion) is True

    def test_weak_tokens_do_not_anchor(self):
        """Matching on 'how'/'best'/'for' would let anything through."""
        assert mod._retains_seed_token("best llm", "best air fryer") is False

    def test_seed_of_only_weak_tokens_fails_open(self):
        """Nothing to anchor on — dropping everything would silently zero the
        run, which is worse than a few off-topic candidates an operator can see."""
        assert mod._retains_seed_token("how to", "how to do anything") is True

    @pytest.mark.asyncio
    async def test_drifted_suggestions_never_become_topics(self, monkeypatch):
        _install_fake_http(monkeypatch, {"glad ai": [
            "glade air freshener", "glade plugins", "glad ai content pipeline",
        ]})
        src = SearchAutocompleteSource()
        topics = await src.extract(None, {
            "seeds": ["glad ai"], "modifiers": [], "seed_from_gsc_clicks": False,
        })
        assert [t.keywords[0] for t in topics] == ["glad ai content pipeline"]


class TestScoringIsOrdinalNotVolume:
    def test_earlier_suggestions_score_higher(self):
        assert mod._score_for_rank(0) > mod._score_for_rank(3) > mod._score_for_rank(9)

    def test_score_is_bounded_below(self):
        assert mod._score_for_rank(500) == 0.5

    def test_ceiling_stays_below_the_measured_demand_source(self):
        """gsc_query_gap can reach 5.0 from real impression counts. Ordinal
        popularity is weaker evidence and must lose a tie in cross-source
        ranking — otherwise inferred demand outranks measured demand."""
        assert mod._SCORE_CEILING < 5.0
        assert mod._score_for_rank(0) <= mod._SCORE_CEILING

    @pytest.mark.asyncio
    async def test_description_does_not_claim_a_volume(self, monkeypatch):
        """feedback_no_dummy_data: autocomplete reports no counts, so the
        description must not imply one."""
        _install_fake_http(monkeypatch, {"vram": ["vram calculator for llm"]})
        src = SearchAutocompleteSource()
        topics = await src.extract(None, {
            "seeds": ["vram"], "modifiers": [], "seed_from_gsc_clicks": False,
        })
        desc = topics[0].description.lower()
        assert "rank" in desc
        assert "no volume" in desc
        for fabricated in ("impression", "searches per", "/mo", "volume:"):
            assert fabricated not in desc.replace("no volume", "")


class TestResilience:
    @pytest.mark.asyncio
    async def test_one_throttled_probe_does_not_sink_the_run(self, monkeypatch, caplog):
        _install_fake_http(
            monkeypatch,
            {"vram": ["vram calculator for llm"], "vram how": ["how much vram for llm"]},
            fail={"vram how"},
        )
        src = SearchAutocompleteSource()
        with caplog.at_level("WARNING"):
            topics = await src.extract(None, {
                "seeds": ["vram"], "modifiers": ["how"], "seed_from_gsc_clicks": False,
            })
        assert [t.keywords[0] for t in topics] == ["vram calculator for llm"]
        assert "probes failed" in caplog.text

    @pytest.mark.asyncio
    async def test_malformed_payload_is_ignored(self, monkeypatch):
        def handler(request):
            return httpx.Response(200, text=json.dumps({"not": "a list"}))

        real = httpx.AsyncClient
        monkeypatch.setattr(
            mod.httpx, "AsyncClient",
            lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}),
        )
        src = SearchAutocompleteSource()
        assert await src.extract(None, {
            "seeds": ["vram"], "modifiers": [], "seed_from_gsc_clicks": False,
        }) == []


class TestShipsInert:
    def test_seeded_default_disables_the_source(self):
        """The runner defaults a MISSING plugin row to enabled=True, so the row
        has to exist with enabled=false or this source switches itself on."""
        from services.settings_defaults import DEFAULTS

        raw = DEFAULTS["plugin.topic_source.search_autocomplete"]
        cfg = json.loads(raw)
        assert cfg["enabled"] is False
        assert cfg["config"]["seeds"] == []

    def test_extract_does_not_gate_on_enabled_itself(self):
        """extract() receives only the INNER config, which never carries
        `enabled`; checking it there would make the source unrunnable."""
        import inspect

        body = inspect.getsource(SearchAutocompleteSource.extract)
        assert 'config.get("enabled"' not in body

    def test_registered_in_the_plugin_registry(self):
        """Registered through the same public seam the runner reads, so a source
        that exists on disk but was never wired fails here."""
        from plugins.registry import get_core_samples

        names = {
            getattr(s, "name", None) for s in get_core_samples().get("topic_sources", [])
        }
        assert "search_autocomplete" in names
