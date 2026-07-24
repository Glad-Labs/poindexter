"""Tests for services/topic_recent_coverage.py — the recent-coverage topic
guard (incident 2026-07-23: internal_rag re-proposed the already-published
Grafana-telemetry theme; the composite title+angle signal separates re-treads
from same-domain neighbours where title-vs-content cosine cannot).

Everything runs on fakes — no DB, no embed model. The fake embedder returns
hand-built vectors so cosine outcomes are exact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from services.topic_recent_coverage import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_THRESHOLD,
    RecentCoverageError,
    RecentCoverageIndex,
    RecentCoverageMatch,
    assert_no_recent_coverage,
    check_recent_coverage,
    compose_text,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _site_config(values: dict | None = None) -> MagicMock:
    sc = MagicMock()
    values = values or {}
    sc.get.side_effect = lambda key, default="": values.get(key, default)
    sc.get_bool.side_effect = lambda key, default: values.get(key, default)
    sc.get_float.side_effect = lambda key, default: values.get(key, default)
    sc.get_int.side_effect = lambda key, default: values.get(key, default)
    return sc


class _FakeConn:
    def __init__(self, published_rows, in_flight_rows):
        self._published = published_rows
        self._in_flight = in_flight_rows
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "FROM posts" in sql:
            return self._published
        return self._in_flight


class _FakePool:
    def __init__(self, published_rows=None, in_flight_rows=None):
        self.conn = _FakeConn(published_rows or [], in_flight_rows or [])

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *_a):
                return False

        return _Ctx()


class _FakeMem:
    """Deterministic embedder: vectors looked up by text; unknown text gets
    an orthogonal unit vector so it matches nothing."""

    def __init__(self, vectors: dict[str, list[float]], raise_on: set | None = None):
        self._vectors = vectors
        self._raise_on = set(raise_on or ())
        self.embedded: list[str] = []

    async def embed(self, text: str) -> list[float]:
        if text in self._raise_on:
            raise RuntimeError("embed down")
        self.embedded.append(text)
        return self._vectors.get(text, [0.0, 0.0, 1.0])


def _row(
    title: str,
    *,
    task_topic: str | None = None,
    angle: str = "",
    niche: str | None = "glad-labs",
    ref_id: str = "post-1",
    published_at: datetime | None = None,
):
    return {
        "ref_id": ref_id,
        "title": title,
        "published_at": published_at,
        "task_topic": task_topic,
        "niche_slug": niche,
        "angle": angle,
    }


# ---------------------------------------------------------------------------
# compose_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestComposeText:
    async def test_joins_title_and_angle(self):
        assert compose_text("A Title", "An angle") == "A Title — An angle"

    async def test_skips_blank_fragments(self):
        assert compose_text("A Title", "", None, "  ") == "A Title"

    async def test_skips_fragments_repeating_the_title(self):
        # Post title == task topic for most posts — composing both would
        # just double-weight the title.
        assert compose_text("Same Text", "same text", "angle") == "Same Text — angle"


# ---------------------------------------------------------------------------
# RecentCoverageIndex.load
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIndexLoad:
    async def test_disabled_returns_none(self):
        sc = _site_config({"topic_recent_coverage_enabled": False})
        index = await RecentCoverageIndex.load(
            _FakePool(), site_config=sc, memory_client=_FakeMem({}),
        )
        assert index is None

    async def test_builds_composite_refs(self):
        pool = _FakePool(
            published_rows=[
                _row(
                    "The Shift to Native Telemetry",
                    task_topic="The Shift to Native Telemetry",
                    angle="Moving from Grafana iframes to a 100% native UI",
                ),
            ],
            in_flight_rows=[
                _row("In-flight topic", angle="its angle", ref_id="task-9"),
            ],
        )
        mem = _FakeMem({})
        index = await RecentCoverageIndex.load(
            pool, site_config=_site_config(), memory_client=mem,
        )
        assert index is not None
        texts = [r.text for r in index.refs]
        # Title + angle composed; the task_topic that repeats the title is
        # dropped rather than double-weighted.
        assert texts == [
            "The Shift to Native Telemetry — Moving from Grafana iframes to a 100% native UI",
            "In-flight topic — its angle",
        ]
        assert [r.kind for r in index.refs] == ["published_post", "in_flight_task"]
        assert mem.embedded == texts

    async def test_lookback_days_passed_to_query(self):
        pool = _FakePool()
        sc = _site_config({"topic_recent_coverage_lookback_days": 30})
        await RecentCoverageIndex.load(
            pool, site_config=sc, memory_client=_FakeMem({}),
        )
        published_call = next(
            c for c in pool.conn.calls if "FROM posts" in c[0]
        )
        assert published_call[1] == (30,)

    async def test_default_lookback_is_90_days(self):
        pool = _FakePool()
        await RecentCoverageIndex.load(
            pool, site_config=_site_config(), memory_client=_FakeMem({}),
        )
        published_call = next(
            c for c in pool.conn.calls if "FROM posts" in c[0]
        )
        assert published_call[1] == (DEFAULT_LOOKBACK_DAYS,)

    async def test_other_niche_refs_excluded_nicheless_kept(self):
        pool = _FakePool(
            published_rows=[
                _row("Glad Labs post", niche="glad-labs", ref_id="p1"),
                _row("Dev diary post", niche="dev_diary", ref_id="p2"),
                _row("Legacy manual post", niche=None, ref_id="p3"),
            ],
        )
        index = await RecentCoverageIndex.load(
            pool,
            site_config=_site_config(),
            memory_client=_FakeMem({}),
            niche_slug="glad-labs",
        )
        assert index is not None
        assert [r.ref_id for r in index.refs] == ["p1", "p3"]

    async def test_no_niche_filter_keeps_everything(self):
        pool = _FakePool(
            published_rows=[
                _row("A", niche="glad-labs", ref_id="p1"),
                _row("B", niche="dev_diary", ref_id="p2"),
            ],
        )
        index = await RecentCoverageIndex.load(
            pool, site_config=_site_config(), memory_client=_FakeMem({}),
        )
        assert index is not None
        assert len(index.refs) == 2

    async def test_db_failure_fails_open(self):
        pool = MagicMock()
        pool.acquire.side_effect = RuntimeError("db down")
        index = await RecentCoverageIndex.load(
            pool, site_config=_site_config(), memory_client=_FakeMem({}),
        )
        assert index is None


# ---------------------------------------------------------------------------
# embed_and_match
# ---------------------------------------------------------------------------


def _index_with_one_ref(
    *, threshold: float = DEFAULT_THRESHOLD, vectors: dict, raise_on=None,
) -> RecentCoverageIndex:
    mem = _FakeMem(vectors, raise_on=raise_on)
    from services.topic_recent_coverage import CoverageRef

    ref = CoverageRef(
        kind="published_post",
        ref_id="post-1",
        title="The Shift to Native Telemetry",
        niche_slug="glad-labs",
        published_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        text="ref text",
        embedding=vectors["ref text"],
    )
    return RecentCoverageIndex([ref], threshold=threshold, embed=mem.embed)


@pytest.mark.unit
class TestEmbedAndMatch:
    async def test_match_at_threshold_returns_named_match(self):
        index = _index_with_one_ref(
            vectors={"ref text": [1.0, 0.0], "candidate": [1.0, 0.0]},
        )
        match = await index.embed_and_match("candidate")
        assert match is not None
        assert match.title == "The Shift to Native Telemetry"
        assert match.similarity == pytest.approx(1.0)
        assert match.kind == "published_post"

    async def test_below_threshold_returns_none(self):
        # cosine([1,1],[1,0]) ≈ 0.707 < 0.80 default.
        index = _index_with_one_ref(
            vectors={"ref text": [1.0, 0.0], "candidate": [1.0, 1.0]},
        )
        assert await index.embed_and_match("candidate") is None

    async def test_threshold_override_catches_borderline(self):
        index = _index_with_one_ref(
            threshold=0.70,
            vectors={"ref text": [1.0, 0.0], "candidate": [1.0, 1.0]},
        )
        match = await index.embed_and_match("candidate")
        assert match is not None
        assert match.similarity == pytest.approx(0.7071, abs=1e-3)

    async def test_empty_text_and_empty_refs_return_none(self):
        index = _index_with_one_ref(
            vectors={"ref text": [1.0, 0.0]},
        )
        assert await index.embed_and_match("") is None
        empty = RecentCoverageIndex([], threshold=0.8, embed=_FakeMem({}).embed)
        assert await empty.embed_and_match("anything") is None

    async def test_embed_failure_fails_open(self):
        index = _index_with_one_ref(
            vectors={"ref text": [1.0, 0.0]}, raise_on={"candidate"},
        )
        assert await index.embed_and_match("candidate") is None


# ---------------------------------------------------------------------------
# check / assert helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckAndAssert:
    def _pool(self):
        return _FakePool(
            published_rows=[
                _row(
                    "The Shift to Native Telemetry",
                    angle="Moving from Grafana iframes to a 100% native UI",
                    published_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
                ),
            ],
        )

    def _mem(self, similar: bool):
        ref_text = (
            "The Shift to Native Telemetry — Moving from Grafana iframes "
            "to a 100% native UI"
        )
        return _FakeMem({
            ref_text: [1.0, 0.0],
            "candidate text": [1.0, 0.0] if similar else [0.0, 1.0],
        })

    async def test_check_returns_match_for_near_duplicate(self):
        match = await check_recent_coverage(
            "candidate text",
            pool=self._pool(),
            site_config=_site_config(),
            memory_client=self._mem(similar=True),
        )
        assert match is not None
        assert match.title == "The Shift to Native Telemetry"

    async def test_check_returns_none_for_distinct(self):
        match = await check_recent_coverage(
            "candidate text",
            pool=self._pool(),
            site_config=_site_config(),
            memory_client=self._mem(similar=False),
        )
        assert match is None

    async def test_check_disabled_short_circuits(self):
        mem = self._mem(similar=True)
        match = await check_recent_coverage(
            "candidate text",
            pool=self._pool(),
            site_config=_site_config({"topic_recent_coverage_enabled": False}),
            memory_client=mem,
        )
        assert match is None
        assert mem.embedded == []

    async def test_assert_raises_with_named_match(self):
        with pytest.raises(RecentCoverageError) as exc_info:
            await assert_no_recent_coverage(
                "candidate text",
                topic="Ditching Grafana for Native Telemetry",
                pool=self._pool(),
                site_config=_site_config(),
                memory_client=self._mem(similar=True),
            )
        message = str(exc_info.value)
        assert "The Shift to Native Telemetry" in message
        assert "Ditching Grafana for Native Telemetry" in message
        assert "2026-07-15" in message

    async def test_assert_passes_for_distinct(self):
        await assert_no_recent_coverage(
            "candidate text",
            topic="Something else",
            pool=self._pool(),
            site_config=_site_config(),
            memory_client=self._mem(similar=False),
        )


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContracts:
    async def test_error_is_value_error_for_route_mapping(self):
        # The resolve HTTP routes map ValueError → 400 and the CLI prints
        # it — same contract TopicSanityError rides on.
        match = RecentCoverageMatch(
            kind="published_post", ref_id="x", title="T",
            similarity=0.9, published_at=None,
        )
        err = RecentCoverageError(topic="t", match=match, threshold=0.8)
        assert isinstance(err, ValueError)

    async def test_defaults_seeded_in_settings(self):
        from services.settings_defaults import DEFAULTS

        assert DEFAULTS["topic_recent_coverage_enabled"] == "true"
        assert float(DEFAULTS["topic_recent_coverage_threshold"]) == DEFAULT_THRESHOLD
        assert int(DEFAULTS["topic_recent_coverage_lookback_days"]) == (
            DEFAULT_LOOKBACK_DAYS
        )
