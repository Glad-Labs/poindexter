"""Unit tests for GscQueryGapSource -- fake pool, no real DB."""

from __future__ import annotations

from typing import Any

import pytest

from poindexter.services.topic_sources.gsc_query_gap import GscQueryGapSource


class _FakeConn:
    def __init__(self, setting_value: str | None, gap_rows: list[dict[str, Any]]):
        self._setting_value = setting_value
        self._gap_rows = gap_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query, *args):
        assert "app_settings" in query
        return self._setting_value

    async def fetch(self, query, *args):
        assert "external_metrics" in query
        return self._gap_rows


class _FakePool:
    def __init__(self, setting_value: str | None = "true", gap_rows: list[dict[str, Any]] | None = None):
        self._setting_value = setting_value
        self._gap_rows = gap_rows or []

    def acquire(self):
        return _FakeConn(self._setting_value, self._gap_rows)


def _gap_row(**overrides) -> dict[str, Any]:
    base = {
        "query": "docker compose tutorial",
        "impressions": 500.0,
        "avg_position": 22.5,
    }
    base.update(overrides)
    return base


class TestGscQueryGapSource:
    name = "gsc_query_gap"

    @pytest.mark.asyncio
    async def test_returns_empty_when_ingestion_disabled(self):
        source = GscQueryGapSource()
        pool = _FakePool(setting_value="false", gap_rows=[_gap_row()])
        topics = await source.extract(pool, {})
        assert topics == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_setting_missing(self):
        source = GscQueryGapSource()
        pool = _FakePool(setting_value=None, gap_rows=[_gap_row()])
        topics = await source.extract(pool, {})
        assert topics == []

    @pytest.mark.asyncio
    async def test_yields_one_topic_per_gap_row(self):
        source = GscQueryGapSource()
        pool = _FakePool(
            setting_value="true",
            gap_rows=[
                _gap_row(query="docker compose tutorial", impressions=500.0, avg_position=22.5),
                _gap_row(query="best gpu 2026", impressions=1200.0, avg_position=18.0),
            ],
        )
        topics = await source.extract(pool, {})
        assert len(topics) == 2
        titles = {t.title for t in topics}
        assert "Docker Compose Tutorial" in titles
        assert "Best Gpu 2026" in titles
        for t in topics:
            assert t.source == "gsc_query_gap"
            assert t.keywords
            assert t.relevance_score > 0
            assert t.source_url.startswith("https://www.google.com/search?q=")

    @pytest.mark.asyncio
    async def test_relevance_score_scales_with_impressions(self):
        source = GscQueryGapSource()
        pool = _FakePool(
            setting_value="true",
            gap_rows=[
                _gap_row(query="low impressions query", impressions=10.0, avg_position=20.0),
                _gap_row(query="high impressions query", impressions=2000.0, avg_position=20.0),
            ],
        )
        topics = await source.extract(pool, {})
        by_query = {t.keywords[0]: t for t in topics}
        assert (
            by_query["high impressions query"].relevance_score
            > by_query["low impressions query"].relevance_score
        )

    @pytest.mark.asyncio
    async def test_config_overrides_thresholds(self):
        # Just confirm the config dict is read without raising -- the actual
        # threshold filtering happens in SQL (faked here), so this test only
        # locks in that extract() accepts the standard config keys.
        source = GscQueryGapSource()
        pool = _FakePool(setting_value="true", gap_rows=[])
        topics = await source.extract(
            pool,
            {"min_impressions": 200, "min_position": 10, "window_days": 14, "max_topics": 5},
        )
        assert topics == []


class _Cfg:
    """Minimal stand-in for SiteConfig's read seam."""

    def __init__(self, **values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


BRAND = _Cfg(site_name="Glad Labs", company_name="Glad Labs", site_domain="gladlabs.io")


class TestJunkQueriesNeverBecomeTopics:
    """An impression is only demand if a person made it.

    2026-08-09 GSC audit: this source was LIVE (`seo.query_ingestion.enabled`
    = true) with no junk filter, and `site:www.gladlabs.io` cleared its
    thresholds — it would have produced an article titled "Site:Www.Gladlabs.Io".
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("junk", [
        "site:www.gladlabs.io",                 # operator: index audit
        "inurl:posts",
        "zhanymkanov/fastapi-best-practices",   # repo path: navigation
        "https://www.gladlabs.io/about",
        "www.gladlabs.io",
        "8000-6400",                            # letterless fragment
        "glad labs",                            # brand navigation
        "gladlabs.io",
        "gladlabs",
    ])
    async def test_junk_query_is_dropped(self, junk):
        source = GscQueryGapSource()
        pool = _FakePool(gap_rows=[_gap_row(query=junk)])
        assert await source.extract(pool, {"_site_config": BRAND}) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("real", [
        "5090 local llm",              # 3 words, 7.1% CTR in prod — must survive
        "fast api best practices",     # 20% CTR in prod
        "dot product vs cosine similarity",
        "socket.io tutorial",          # a bare-domain rule would have killed this
        "next.js routing",
        "content pipeline automation",
    ])
    async def test_real_query_survives(self, real):
        source = GscQueryGapSource()
        pool = _FakePool(gap_rows=[_gap_row(query=real)])
        topics = await source.extract(pool, {"_site_config": BRAND})
        assert [t.keywords[0] for t in topics] == [real]

    @pytest.mark.asyncio
    async def test_no_site_config_still_filters_operators(self):
        """Brand filtering needs config; operator/URL filtering must not."""
        source = GscQueryGapSource()
        pool = _FakePool(gap_rows=[_gap_row(query="site:example.com")])
        assert await source.extract(pool, {}) == []


class TestPermutationClustersAreDropped:
    """103 reorderings of one phrase produced 3,696 impressions and ZERO clicks.

    A per-query rule cannot catch this — every variant looks like a plausible
    query on its own. Grouping on the sorted token bag catches the whole cluster.
    """

    def _permutations(self, n: int) -> list[dict[str, Any]]:
        words = ["cadquery", "official", "documentation", "parametric", "cad", "python"]
        out = []
        for i in range(n):
            rotated = words[i % len(words):] + words[: i % len(words)]
            # vary two positions so each string is distinct but the bag is equal
            if i >= len(words):
                rotated = rotated[:2][::-1] + rotated[2:]
            out.append(_gap_row(query=" ".join(rotated) + ("" if i < len(words) else "")))
        # de-dup identical strings so the count reflects distinct orderings
        seen, uniq = set(), []
        for r in out:
            if r["query"] not in seen:
                seen.add(r["query"])
                uniq.append(r)
        return uniq

    @pytest.mark.asyncio
    async def test_cluster_is_dropped_whole(self):
        source = GscQueryGapSource()
        rows = self._permutations(6)
        assert len(rows) >= 5, "need >= min_variants distinct orderings"
        pool = _FakePool(gap_rows=rows)
        assert await source.extract(pool, {"_site_config": BRAND}) == []

    @pytest.mark.asyncio
    async def test_two_phrasings_are_not_a_cluster(self):
        """Rewording once is normal human behaviour, not machine generation."""
        source = GscQueryGapSource()
        pool = _FakePool(gap_rows=[
            _gap_row(query="local llm vram requirements"),
            _gap_row(query="vram requirements local llm"),
        ])
        topics = await source.extract(pool, {"_site_config": BRAND})
        assert len(topics) == 2

    @pytest.mark.asyncio
    async def test_threshold_is_configurable(self):
        source = GscQueryGapSource()
        pool = _FakePool(gap_rows=[
            _gap_row(query="local llm vram requirements"),
            _gap_row(query="vram requirements local llm"),
        ])
        topics = await source.extract(
            pool, {"_site_config": BRAND, "permutation_min_variants": 2},
        )
        assert topics == []

    @pytest.mark.asyncio
    async def test_a_cluster_cannot_starve_the_run(self):
        """The SQL over-fetches, so a cluster filling the first max_topics slots
        still leaves room for the real gaps behind it."""
        source = GscQueryGapSource()
        rows = self._permutations(6) + [
            _gap_row(query="dot product vs cosine similarity"),
            _gap_row(query="speculative decoding throughput"),
        ]
        pool = _FakePool(gap_rows=rows)
        topics = await source.extract(
            pool, {"_site_config": BRAND, "max_topics": 2},
        )
        assert [t.keywords[0] for t in topics] == [
            "dot product vs cosine similarity", "speculative decoding throughput",
        ]

    @pytest.mark.asyncio
    async def test_max_topics_is_still_honoured_after_filtering(self):
        source = GscQueryGapSource()
        pool = _FakePool(gap_rows=[
            _gap_row(query=f"distinct technical query number {i}") for i in range(9)
        ])
        topics = await source.extract(pool, {"_site_config": BRAND, "max_topics": 3})
        assert len(topics) == 3


class _ArgCapturingConn(_FakeConn):
    """Records the bind args, since the thresholds live in SQL not Python."""

    def __init__(self, setting_value, gap_rows, sink):
        super().__init__(setting_value, gap_rows)
        self._sink = sink

    async def fetch(self, query, *args):
        self._sink.append(args)
        return await super().fetch(query, *args)


class _ArgCapturingPool(_FakePool):
    def __init__(self, sink, **kw):
        super().__init__(**kw)
        self._sink = sink

    def acquire(self):
        return _ArgCapturingConn(self._setting_value, self._gap_rows, self._sink)


class TestDefaultThresholdsAreReachable:
    """The shipped defaults must be able to match something on a real site.

    Calibrated 2026-09-15. The original 28d + 50-impression pair needed one
    poorly-ranked query to draw ~1.8 impressions/day. Measured on this install:
    over 28 days the whole corpus was 103 queries with a median of 2
    impressions, and the only two clearing 50 ranked at positions 3.8 and 5.4 --
    ranking well, so they failed ``min_position`` as well. The two conditions
    could not both be satisfied, and the source returned 0 rows on all 187 runs
    while reporting ``success`` every time.

    A threshold nothing can reach is indistinguishable from a broken tap, and
    that is what this pins.
    """

    @pytest.mark.asyncio
    async def test_default_window_is_ninety_days(self):
        sink: list[tuple] = []
        source = GscQueryGapSource()
        await source.extract(_ArgCapturingPool(sink, setting_value="true", gap_rows=[]), {})
        assert sink, "the gap query never ran"
        window_days = sink[0][0]
        assert window_days == 90, (
            "28 days is too short to accumulate impressions on a small site; "
            "the 90d corpus is where the real gaps show up"
        )

    @pytest.mark.asyncio
    async def test_default_impression_floor_is_reachable(self):
        sink: list[tuple] = []
        source = GscQueryGapSource()
        await source.extract(_ArgCapturingPool(sink, setting_value="true", gap_rows=[]), {})
        min_impressions = sink[0][1]
        assert min_impressions == 20.0, (
            "50 was unreachable: no poorly-ranked query on this site had ever "
            "cleared it in a 28d window"
        )

    @pytest.mark.asyncio
    async def test_explicit_config_still_overrides_the_defaults(self):
        sink: list[tuple] = []
        source = GscQueryGapSource()
        await source.extract(
            _ArgCapturingPool(sink, setting_value="true", gap_rows=[]),
            {"window_days": 14, "min_impressions": 200},
        )
        assert sink[0][0] == 14
        assert sink[0][1] == 200.0
