"""What each distribution surface cost us in placements, and what came back.

Poindexter publishes to seven-ish surfaces and, until the attribution tag
existed, could not answer the only question that matters about any of them:
*did anyone arrive through it?* Referrer alone cannot answer it — X collapses
to ``t.co``, Bluesky to ``go.bsky.app``, and Dev.to sends nothing at all — so
a surface that delivers and a surface that does not looked identical.

This module is the read side of :mod:`services.distribution_ref`. For a window
it reports, per surface:

``placements``
    outbound links we put on that surface — a posted social promo, a Dev.to
    crosspost, a YouTube upload.
``tagged_views``
    views that arrived carrying that surface's tag. The trustworthy signal,
    and the one that only counts from the day tagging shipped.
``referrer_views``
    views whose ``referrer`` host maps to that surface. The legacy signal,
    kept alongside rather than replaced: it is the only evidence that exists
    for the period before tagging, and the gap between the two columns is
    itself the measurement — it shows how much attribution the referrer alone
    was losing.

Deliberately NOT a ranking or a recommendation. A surface with zero yield may
be badly used rather than worthless, and the numbers are here so that call is
made from evidence instead of from how a platform feels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.distribution_ref import SURFACE_MEDIUM

# Referrer host → surface, for the pre-tag signal. Matched against the host
# with any leading ``www.`` removed. Suffix-matched, so ``go.bsky.app`` and
# ``bsky.app`` both resolve without listing every subdomain a platform invents.
#
# Mastodon is absent on purpose: the fediverse has no canonical host, so a
# host map cannot see it. That is precisely the blind spot the tag closes, and
# leaving a wrong guess out of this map keeps the two columns honest.
_REFERRER_HOSTS: dict[str, str] = {
    "t.co": "twitter",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "bsky.app": "bluesky",
    "dev.to": "devto",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "linkedin.com": "linkedin",
    "lnkd.in": "linkedin",
    "reddit.com": "reddit",
    "news.ycombinator.com": "hn",
    "hashnode.dev": "hashnode",
    "open.spotify.com": "spotify",
}


@dataclass(frozen=True)
class SurfaceYield:
    """One surface's placements and returns over the window."""

    surface: str
    medium: str
    placements: int
    tagged_views: int
    referrer_views: int

    @property
    def views_per_placement(self) -> float | None:
        """Tagged views per outbound placement, or ``None`` when nothing went out.

        ``None`` rather than ``0.0`` — "we posted nothing there" and "we posted
        and nobody came" are different findings, and collapsing them into one
        zero is how a dormant surface gets mistaken for a dead one.
        """
        if self.placements <= 0:
            return None
        return self.tagged_views / self.placements


# Outbound placements, unioned across the three systems that record them.
# Each branch is dated by when the placement actually went out, not by when the
# post was written — a surface's yield window has to line up with the window
# the clicks are counted in.
_PLACEMENTS_SQL = """
    -- Social promos that Postiz accepted.
    SELECT platform AS surface, count(*)::bigint AS placements
      FROM social_post_drafts
     WHERE status = 'posted'
       AND posted_at >= $1
     GROUP BY platform

    UNION ALL

    -- Dev.to crossposts. There is no per-crosspost timestamp, so the post's
    -- own publish date stands in: the job crossposts within hours of publish,
    -- which is inside any window worth reporting on.
    SELECT 'devto' AS surface, count(*)::bigint
      FROM posts
     WHERE metadata->>'devto_status' = 'posted'
       AND published_at >= $1

    UNION ALL

    -- Video uploads that returned a platform handle.
    SELECT target AS surface, count(*)::bigint
      FROM pipeline_distributions
     WHERE status = 'published'
       AND target <> 'gladlabs.io'
       AND created_at >= $1
     GROUP BY target
"""

# Views that arrived carrying a tag. page_views_human excludes flagged bots, so
# this counts readers rather than crawlers following the same tagged link.
_TAGGED_VIEWS_SQL = """
    SELECT ref_source AS surface, count(*)::bigint AS views
      FROM page_views_human
     WHERE created_at >= $1
       AND ref_source IS NOT NULL
       AND ref_source <> ''
     GROUP BY ref_source
"""

# Pre-tag signal: referrer host, normalised. Host extraction is done in SQL so
# the whole window never has to be pulled into Python.
_REFERRER_VIEWS_SQL = """
    SELECT lower(
               regexp_replace(
                   regexp_replace(referrer, '^https?://', ''),
                   '^www\\.', ''
               )
           ) AS raw_host,
           count(*)::bigint AS views
      FROM page_views_human
     WHERE created_at >= $1
       AND referrer IS NOT NULL
       AND referrer <> ''
     GROUP BY raw_host
"""


def _surface_for_referrer(raw_host: str) -> str | None:
    """Map a normalised referrer host to a surface, or ``None`` if it is neither.

    Suffix match on a dot boundary so ``go.bsky.app`` resolves to bluesky while
    a lookalike such as ``notbsky.app`` does not.
    """
    host = (raw_host or "").split("/", 1)[0].split(":", 1)[0]
    if not host:
        return None
    for known, surface in _REFERRER_HOSTS.items():
        if host == known or host.endswith("." + known):
            return surface
    return None


async def surface_yield(pool, *, days: int = 30) -> list[SurfaceYield]:
    """Placements and returns per surface over the last ``days``.

    Sorted by tagged views descending, then placements descending, so the
    surfaces that actually delivered lead and the ones we are spending
    placements on for nothing sit right beneath them.
    """
    if days <= 0:
        raise ValueError(f"distribution_yield: days must be positive — got {days}")

    # Bound as a real datetime, never interpolated: an interval built by string
    # substitution is the shape every scanner flags and the shape that goes
    # wrong the first time this grows a caller that does not sanitise its
    # input. asyncpg binds an aware datetime to timestamptz directly (a STRING
    # bind is the one that misbehaves).
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with pool.acquire() as conn:
        placement_rows = await conn.fetch(_PLACEMENTS_SQL, since)
        tagged_rows = await conn.fetch(_TAGGED_VIEWS_SQL, since)
        referrer_rows = await conn.fetch(_REFERRER_VIEWS_SQL, since)

    placements: dict[str, int] = {}
    for row in placement_rows:
        surface = row["surface"] or ""
        if surface:
            placements[surface] = placements.get(surface, 0) + int(row["placements"])

    tagged: dict[str, int] = {
        (row["surface"] or ""): int(row["views"]) for row in tagged_rows if row["surface"]
    }

    referrer: dict[str, int] = {}
    for row in referrer_rows:
        surface = _surface_for_referrer(row["raw_host"] or "")
        if surface:
            referrer[surface] = referrer.get(surface, 0) + int(row["views"])

    surfaces = sorted(set(placements) | set(tagged) | set(referrer))
    results = [
        SurfaceYield(
            surface=surface,
            medium=SURFACE_MEDIUM.get(surface, ""),
            placements=placements.get(surface, 0),
            tagged_views=tagged.get(surface, 0),
            referrer_views=referrer.get(surface, 0),
        )
        for surface in surfaces
    ]
    results.sort(key=lambda r: (-r.tagged_views, -r.placements, r.surface))
    return results


__all__ = ["SurfaceYield", "surface_yield"]
