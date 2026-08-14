"""Two-axis title originality — web AND our own corpus (poindexter#1044).

``check_title_originality`` read like an internal-diversity gate and was not:
it SequenceMatchered the candidate against DuckDuckGo results only. Nothing
compared our own titles to each other, so "The Shift to Native Telemetry" and
"The Shift to a Native UI" — 0.755 similar — both shipped.

These pin the two axes as INDEPENDENT: separate switches, separate thresholds,
and an ``is_original`` that is false when either trips.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.title_generation import check_title_originality, originality_rank


class _StubSiteConfig:
    def __init__(self, values: dict | None = None):
        self._values = values or {}

    def get(self, key, default=""):
        return self._values.get(key, default)

    def get_int(self, key, default=0):
        return int(self._values.get(key, default))

    def get_float(self, key, default=0.0):
        return float(self._values.get(key, default))

    def get_bool(self, key, default=False):
        val = self._values.get(key, default)
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("true", "1", "yes", "on")


class _FakeConn:
    def __init__(self, titles):
        self._titles = titles

    async def fetch(self, _sql, *_args):
        return [{"title": t} for t in self._titles]


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, titles):
        self._conn = _FakeConn(titles)

    def acquire(self):
        return _FakeAcquire(self._conn)


def _no_web():
    """Patch the external axis to find nothing, isolating the internal one."""
    researcher = AsyncMock()
    researcher.search_simple = AsyncMock(return_value=[])
    ext = AsyncMock()
    ext.check_external_title_duplicates = AsyncMock(
        return_value=type(
            "_Ext", (), {
                "verbatim_match": False, "near_match": False, "penalty": 0,
                "matches": [], "fail_open": False,
            },
        )()
    )
    return (
        patch("services.web_research.WebResearcher", return_value=researcher),
        patch(
            "services.title_originality_external.TitleOriginalityExternalChecker",
            return_value=ext,
        ),
    )


@pytest.mark.asyncio
class TestInternalAxis:
    async def test_internal_duplicate_flips_is_original(self):
        """This is what routes a collision into the regeneration loop."""
        web, ext = _no_web()
        with web, ext:
            result = await check_title_originality(
                "The Shift to Native Telemetry",
                site_config=_StubSiteConfig(),
                pool=_FakePool(["The Shift to a Native UI"]),
            )
        assert result["internal_duplicate"] is True
        assert result["is_original"] is False
        assert "The Shift to a Native UI" in result["similar_titles"]

    async def test_distinct_title_stays_original(self):
        web, ext = _no_web()
        with web, ext:
            result = await check_title_originality(
                "Sourdough Starter Notes",
                site_config=_StubSiteConfig(),
                pool=_FakePool(["VRAM Poisoning and the P4 Architect"]),
            )
        assert result["internal_duplicate"] is False
        assert result["is_original"] is True

    async def test_without_a_pool_the_internal_axis_is_skipped(self):
        """Backcompat: existing callers that pass no pool keep working."""
        web, ext = _no_web()
        with web, ext:
            result = await check_title_originality(
                "The Shift to Native Telemetry", site_config=_StubSiteConfig(),
            )
        assert result["internal_duplicate"] is False
        assert result["internal_similarity"] == 0.0

    async def test_internal_runs_even_when_external_check_is_disabled(self):
        """The axes own separate switches.

        Folding the internal check behind ``qa_title_originality_enabled``
        would mean turning off the WEB check silently stops detecting
        duplicates of our own posts — two unrelated behaviours on one flag.
        """
        web, ext = _no_web()
        with web, ext:
            result = await check_title_originality(
                "The Shift to Native Telemetry",
                site_config=_StubSiteConfig({"qa_title_originality_enabled": False}),
                pool=_FakePool(["The Shift to a Native UI"]),
            )
        assert result["internal_duplicate"] is True
        assert result["is_original"] is False

    async def test_internal_switch_off_leaves_title_original(self):
        web, ext = _no_web()
        with web, ext:
            result = await check_title_originality(
                "The Shift to Native Telemetry",
                site_config=_StubSiteConfig(
                    {"title_internal_similarity_enabled": False}
                ),
                pool=_FakePool(["The Shift to a Native UI"]),
            )
        assert result["internal_duplicate"] is False
        assert result["is_original"] is True

    async def test_unreadable_corpus_reports_fail_open_not_clean(self):
        class _BoomPool:
            def acquire(self):
                raise RuntimeError("pool down")

        web, ext = _no_web()
        with web, ext:
            result = await check_title_originality(
                "Any Title", site_config=_StubSiteConfig(), pool=_BoomPool(),
            )
        assert result["internal_fail_open"] is True
        assert result["internal_duplicate"] is False

    async def test_threshold_is_surfaced_for_the_finding(self):
        web, ext = _no_web()
        with web, ext:
            result = await check_title_originality(
                "Anything", site_config=_StubSiteConfig(), pool=_FakePool([]),
            )
        assert result["internal_threshold"] > 0.0


class TestOriginalityRank:
    """Lower rank is better. Clearing an axis outranks any similarity delta."""

    def test_clearing_the_internal_axis_wins(self):
        """The regression this replaced: ranking on max_similarity alone.

        max_similarity is EXTERNAL-only, so a v2 that fixed an internal
        duplicate while scoring identically against the web was discarded as
        "not more unique" — silently defeating the whole gate.
        """
        v1 = {"internal_duplicate": True, "internal_similarity": 0.8,
              "external_duplicate": False, "max_similarity": 0.4}
        v2 = {"internal_duplicate": False, "internal_similarity": 0.2,
              "external_duplicate": False, "max_similarity": 0.4}
        assert originality_rank(v2) < originality_rank(v1)

    def test_clearing_the_external_axis_wins(self):
        v1 = {"internal_duplicate": False, "internal_similarity": 0.1,
              "external_duplicate": True, "max_similarity": 0.9}
        v2 = {"internal_duplicate": False, "internal_similarity": 0.1,
              "external_duplicate": False, "max_similarity": 0.3}
        assert originality_rank(v2) < originality_rank(v1)

    def test_external_verbatim_counts_as_a_colliding_axis(self):
        v1 = {"external_verbatim_match": True, "max_similarity": 0.5}
        v2 = {"external_verbatim_match": False, "max_similarity": 0.5}
        assert originality_rank(v2) < originality_rank(v1)

    def test_similarity_only_breaks_ties(self):
        v1 = {"internal_duplicate": False, "internal_similarity": 0.5,
              "external_duplicate": False, "max_similarity": 0.1}
        v2 = {"internal_duplicate": False, "internal_similarity": 0.2,
              "external_duplicate": False, "max_similarity": 0.1}
        assert originality_rank(v2) < originality_rank(v1)

    def test_fewer_axes_beats_a_better_similarity_score(self):
        """One clean axis is worth more than a prettier number on two dirty ones."""
        still_colliding = {"internal_duplicate": True, "internal_similarity": 0.59,
                           "external_duplicate": False, "max_similarity": 0.0}
        clean = {"internal_duplicate": False, "internal_similarity": 0.57,
                 "external_duplicate": False, "max_similarity": 0.0}
        assert originality_rank(clean) < originality_rank(still_colliding)

    def test_worst_axis_drives_the_tiebreak(self):
        a = {"max_similarity": 0.9, "internal_similarity": 0.1}
        b = {"max_similarity": 0.1, "internal_similarity": 0.2}
        assert originality_rank(b) < originality_rank(a)

    def test_missing_keys_are_tolerated(self):
        """Reports from older callers must not raise."""
        assert originality_rank({}) == (0, 0.0)
