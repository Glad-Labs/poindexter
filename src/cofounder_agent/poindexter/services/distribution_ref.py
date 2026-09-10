"""Tag outbound distribution links so a click can be traced back to its surface.

Every link Poindexter places on another platform — the YouTube description's
"Read the full post", a social promo's URL, the Dev.to back-link — pointed at a
bare ``{site_url}/posts/{slug}``. Nothing downstream could tell a Bluesky click
from a Dev.to click from someone typing the address, because the only
attribution signal was ``document.referrer``, which social apps and in-app
browsers routinely suppress (X collapses everything to ``t.co``, Dev.to sends
nothing at all).

That measurement hole is not academic. Over the 90 days to 2026-08-31 the whole
outbound estate — 77 posted social promos across X + Bluesky and 146 Dev.to
crossposts — accounted for **three** identifiable referrals in
``page_views_human``, and there was no way to tell "this surface delivers
nothing" apart from "this surface delivers invisibly". Adding a seventh surface
on top of that is a guess, not a strategy.

This module is the *tagging* half of the fix: one helper that every outbound
link composer routes through, so the surface travels inside the URL itself. The
*reading* half is the beacon chain — ``ViewTracker`` lifts the tag out of
``window.location.search``, the page-views Worker carries it as a blob, and
``SyncCloudflareAnalyticsJob`` lands it in ``page_views.ref_source``.

**Why UTM and not a bespoke ``ref``.** The default parameter vocabulary is
``utm_source`` / ``utm_medium`` because Google Analytics already parses it. One
tag, two independent consumers — our own beacon and GA4's acquisition reports —
for no extra work. Operators who prefer a shorter parameter set
``distribution_ref_source_param``; ``ViewTracker`` reads a small allow-list of
names, so both spellings arrive.

**SEO safety.** Every post page emits ``<link rel="canonical">`` at its
untagged URL, so a tagged variant consolidates back to the canonical and does
not fragment ranking authority. The one place a tag must NEVER go is a
``canonical_url`` field we hand to another platform (Dev.to's, for instance) —
that value IS the canonicalisation signal. Tag the visible back-link beside it
instead; :func:`tag_url` has no way to know which is which, so that call is the
caller's responsibility.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# A surface token ends up in a public URL and in a GROUP BY, so it is kept to a
# short, lowercase, stable slug. Anything else is a programmer error and raises
# — a silently-untagged link is the failure mode this module exists to end.
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Surface → UTM medium. The medium is the *class* of surface, so several
# surfaces share one: it is what makes "how much does social deliver in total"
# a one-line query without re-listing every platform we have ever posted to.
#
# A surface absent from this map still tags its source — only the medium is
# omitted. That is deliberate: a new platform must not be blocked from being
# attributed just because nobody classified it yet.
SURFACE_MEDIUM: dict[str, str] = {
    # Social broadcast (Postiz lane).
    "x": "social",
    "twitter": "social",
    "bluesky": "social",
    "mastodon": "social",
    "linkedin": "social",
    "reddit": "social",
    "instagram": "social",
    # social_drafts' platform key for Reels — the map key must match what the
    # composer passes, and approve_draft passes the DRAFT's platform verbatim.
    "instagram_reels": "social",
    "tiktok": "social",
    # Full-content syndication with a canonical back-link.
    "devto": "syndication",
    "hashnode": "syndication",
    "medium": "syndication",
    # Media surfaces.
    "youtube": "video",
    "podcast": "audio",
    "spotify": "audio",
    # Owned channels.
    "newsletter": "email",
    "rss": "feed",
}


@dataclass(frozen=True)
class RefConfig:
    """Resolved tagging config — the DB values, read once per composer call."""

    enabled: bool = True
    source_param: str = "utm_source"
    medium_param: str = "utm_medium"


def _as_bool(value: Any, default: bool) -> bool:
    """Parse a settings string to bool without inventing a third state."""
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def resolve_ref_config(site_config: Any) -> RefConfig:
    """Read the tagging config off ``site_config``.

    ``site_config`` may be ``None`` (bootstrap paths, unit tests that only care
    about URL shaping) — the dataclass defaults then apply, which are the same
    values ``settings_defaults`` seeds, so the two can't drift into disagreeing
    about what "no config" means.
    """
    if site_config is None:
        return RefConfig()
    try:
        return RefConfig(
            enabled=_as_bool(site_config.get("distribution_ref_enabled", "true"), True),
            source_param=(
                str(site_config.get("distribution_ref_source_param", "") or "").strip()
                or "utm_source"
            ),
            # Empty is a real choice here: it means "source only, no medium".
            medium_param=str(
                site_config.get("distribution_ref_medium_param", "utm_medium") or ""
            ).strip(),
        )
    except Exception as exc:  # noqa: BLE001 — a config read must not break a publish
        logger.warning(
            "[DISTRIBUTION_REF] settings read failed, using defaults: %s", exc
        )
        return RefConfig()


def tag_url(
    url: str,
    *,
    surface: str,
    config: RefConfig | None = None,
    medium: str | None = None,
) -> str:
    """Return ``url`` with the surface tag appended, or ``url`` unchanged.

    Unchanged when: tagging is disabled, ``url`` is empty or not ``http(s)``,
    or the source parameter is already present. That last case makes the
    function idempotent AND keeps it from clobbering a tag an operator placed
    by hand — a link that already says where it came from is already doing the
    job.

    Raises ``ValueError`` for a malformed ``surface``. This is the one loud
    failure: the whole point is that the surface reaches the URL, so a typo'd
    token must surface in tests rather than quietly publish an untagged link
    that reads as "(direct)" forever.

    ``medium`` overrides the :data:`SURFACE_MEDIUM` lookup for callers that
    know better (the same platform used two ways). Passing a medium for a
    surface that has none is how a new platform gets classified before it
    earns a map entry.
    """
    if not _TOKEN_RE.match(surface or ""):
        raise ValueError(
            f"distribution_ref: surface must match {_TOKEN_RE.pattern!r} — got {surface!r}"
        )

    cfg = config or RefConfig()
    if not cfg.enabled or not url:
        return url

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        # Relative or non-web URL — a query tag on it would be meaningless and
        # could corrupt a path the caller intends to resolve later.
        return url

    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(key == cfg.source_param for key, _ in query):
        return url

    query.append((cfg.source_param, surface))
    resolved_medium = medium if medium is not None else SURFACE_MEDIUM.get(surface)
    if cfg.medium_param and resolved_medium:
        if not any(key == cfg.medium_param for key, _ in query):
            query.append((cfg.medium_param, resolved_medium))

    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def tag_for(site_config: Any, url: str, *, surface: str, medium: str | None = None) -> str:
    """``resolve_ref_config`` + :func:`tag_url` in one call.

    The shape most composers want: they hold a ``site_config`` and a URL they
    just built, and have no reason to keep a :class:`RefConfig` around.
    """
    return tag_url(
        url, surface=surface, config=resolve_ref_config(site_config), medium=medium
    )


__all__ = ["RefConfig", "SURFACE_MEDIUM", "resolve_ref_config", "tag_for", "tag_url"]
