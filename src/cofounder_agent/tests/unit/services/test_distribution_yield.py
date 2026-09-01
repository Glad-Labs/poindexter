"""Tests for ``services.distribution_yield`` — placements vs returns per surface.

The numbers in the fixtures are the real 90-day shape as of 2026-08-31
(bluesky 40 promos, twitter 38, devto 107 crossposts, youtube 12 uploads,
against 5 referrer-identifiable clicks), because the thing being tested is
whether the report tells the truth about a distribution estate that is mostly
broadcasting into a void.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime

import pytest

from services.distribution_yield import (
    _PLACEMENTS_SQL,
    SurfaceYield,
    _surface_for_referrer,
    surface_yield,
)
from services.pipeline_db import LEGACY_SITE_TARGETS, SITE_TARGET


class _FakeConn:
    def __init__(self, responses: list[list[dict]]) -> None:
        self._responses = list(responses)
        self.bound_args: list[tuple] = []

    async def fetch(self, _sql: str, *args):
        self.bound_args.append(args)
        return self._responses.pop(0)


class _FakePool:
    """Hands out one connection returning the three queries' rows in order."""

    def __init__(self, placements, tagged, referrer) -> None:
        self.conn = _FakeConn([placements, tagged, referrer])

    @asynccontextmanager
    async def _acquire(self):
        yield self.conn

    def acquire(self):
        return self._acquire()


# ---------------------------------------------------------------------------
# referrer host mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("t.co", "twitter"),
        ("t.co/", "twitter"),
        ("x.com/gladlabs", "twitter"),
        ("go.bsky.app", "bluesky"),
        ("bsky.app", "bluesky"),
        ("dev.to/mattg", "devto"),
        ("youtube.com/watch", "youtube"),
        ("youtu.be/abc", "youtube"),
        ("news.ycombinator.com/", "hn"),
        ("lnkd.in/xyz", "linkedin"),
    ],
)
def test_known_referrer_hosts_map_to_surfaces(host, expected):
    assert _surface_for_referrer(host) == expected


@pytest.mark.parametrize(
    "host", ["google.com/", "gladlabs.io/posts/x", "claude.ai/", "", "duckduckgo.com"]
)
def test_unrelated_hosts_map_to_nothing(host):
    assert _surface_for_referrer(host) is None


def test_lookalike_host_does_not_match():
    """Suffix matching is on a dot boundary, so a domain that merely ENDS in a
    known name is not credited to it."""
    assert _surface_for_referrer("notbsky.app") is None
    assert _surface_for_referrer("evil-dev.to") is None


def test_port_and_path_are_stripped_before_matching():
    assert _surface_for_referrer("bsky.app:443/profile/gladlabs.io") == "bluesky"


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_joins_the_three_signals_per_surface():
    pool = _FakePool(
        placements=[
            {"surface": "bluesky", "placements": 40},
            {"surface": "twitter", "placements": 38},
            {"surface": "devto", "placements": 107},
            {"surface": "youtube", "placements": 12},
        ],
        tagged=[{"surface": "devto", "views": 9}, {"surface": "bluesky", "views": 2}],
        referrer=[
            {"raw_host": "t.co/", "views": 2},
            {"raw_host": "go.bsky.app", "views": 1},
            {"raw_host": "youtube.com/", "views": 2},
            {"raw_host": "google.com/", "views": 396},
        ],
    )
    rows = await surface_yield(pool, days=90)
    by_surface = {r.surface: r for r in rows}

    assert by_surface["devto"] == SurfaceYield(
        surface="devto",
        medium="syndication",
        placements=107,
        tagged_views=9,
        referrer_views=0,
    )
    assert by_surface["bluesky"].referrer_views == 1
    assert by_surface["twitter"].referrer_views == 2
    # Search traffic is not a distribution surface we place links on, so it
    # must not appear at all — the report is about what WE sent.
    assert "google" not in by_surface


@pytest.mark.asyncio
async def test_sorted_by_what_actually_delivered():
    pool = _FakePool(
        placements=[
            {"surface": "devto", "placements": 107},
            {"surface": "bluesky", "placements": 40},
        ],
        tagged=[{"surface": "bluesky", "views": 5}],
        referrer=[],
    )
    rows = await surface_yield(pool, days=30)
    # Bluesky leads on 5 tagged views despite a third of Dev.to's placements —
    # the whole point is that volume is not the ranking.
    assert [r.surface for r in rows] == ["bluesky", "devto"]


@pytest.mark.asyncio
async def test_surface_with_returns_but_no_placements_still_appears():
    """Hacker News delivered more clicks than X and Bluesky combined and we
    place nothing there — the report has to be able to say so."""
    pool = _FakePool(
        placements=[],
        tagged=[],
        referrer=[{"raw_host": "news.ycombinator.com/", "views": 5}],
    )
    rows = await surface_yield(pool, days=90)
    assert [(r.surface, r.placements, r.referrer_views) for r in rows] == [("hn", 0, 5)]


@pytest.mark.asyncio
async def test_views_per_placement_is_none_when_nothing_went_out():
    """`we posted nothing there` and `we posted and nobody came` are different
    findings; collapsing both to 0.0 is how a dormant surface gets killed."""
    pool = _FakePool(
        placements=[{"surface": "devto", "placements": 100}],
        tagged=[{"surface": "devto", "views": 0}, {"surface": "hn", "views": 4}],
        referrer=[],
    )
    rows = {r.surface: r for r in await surface_yield(pool, days=30)}
    assert rows["devto"].views_per_placement == 0.0
    assert rows["hn"].views_per_placement is None


@pytest.mark.asyncio
async def test_window_is_bound_not_interpolated():
    pool = _FakePool(placements=[], tagged=[], referrer=[])
    await surface_yield(pool, days=7)
    # Nothing is spliced into the SQL text: the cutoff is bound on all three
    # queries, and the placements query binds the own-site target list too.
    assert len(pool.conn.bound_args) == 3
    assert [len(args) for args in pool.conn.bound_args] == [2, 1, 1]
    assert all(isinstance(args[0], datetime) for args in pool.conn.bound_args)


@pytest.mark.asyncio
async def test_own_site_is_excluded_by_sentinel_and_its_legacy_spelling():
    """The site itself is not an outbound placement, and the row saying so used
    to be stamped with the source operator's domain (poindexter#1038).

    Both spellings have to be excluded: a fork running the sentinel would
    otherwise report a surface literally named ``gladlabs.io`` the moment it
    restored a pre-cutover row.
    """
    pool = _FakePool(placements=[], tagged=[], referrer=[])
    await surface_yield(pool, days=30)

    excluded = pool.conn.bound_args[0][1]
    assert SITE_TARGET in excluded
    assert set(LEGACY_SITE_TARGETS) <= set(excluded)
    # Bound as a list, not interpolated — the SQL names a parameter, not a value.
    assert "gladlabs.io" not in _PLACEMENTS_SQL
    assert "$2" in _PLACEMENTS_SQL


@pytest.mark.asyncio
async def test_rejects_a_nonsense_window():
    pool = _FakePool(placements=[], tagged=[], referrer=[])
    with pytest.raises(ValueError):
        await surface_yield(pool, days=0)
