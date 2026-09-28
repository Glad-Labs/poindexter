"""Migration 20260928_130429: drop ``org`` from ``research_tier1_domains``.

ISSUE: none filed. The measurements are in the PR that wires
``ResearchQualityService`` into ``research_service.build_context``.

``research_tier1_domains`` gives a credibility score of 0.95 to every host under
one of its suffixes. It shipped as ``edu,gov,ac.uk,org``, and nothing read it
until the service was wired into the live research path on 2026-09-28. ``org``
is open registration, not an institutional suffix. Replaying the 181 web tiers
stored in ``pipeline_versions`` through the service, ranked on its quality
scores alone, a suffix-only ``.org`` took the top slot from DuckDuckGo's first
result in 26 of them. fatsil.org, 2ndbook.org and blazebeaver.org were among
them, with fatsil.org ranked above a textbook author's own page. The code
default and ``settings_defaults`` now read ``edu,gov,ac.uk``. This migration
brings an existing row into line, because ``seed_all_defaults`` is
``ON CONFLICT DO NOTHING`` and never rewrites a row that already exists.

Only a row whose entries are exactly the old shipped default is rewritten. Any
other list is an operator's choice and is left alone. Wikipedia and arXiv keep
their standing through ``research_tier2_domains``, which lists both.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEY = "research_tier1_domains"
_OLD_DEFAULT = frozenset({"edu", "gov", "ac.uk", "org"})
_NEW_DEFAULT = "edu,gov,ac.uk"


def _entries(csv: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in csv.split(",") if e.strip())


async def up(pool) -> None:
    """Rewrite the row if it still holds the old default. Idempotent."""
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", _KEY)
        if not isinstance(value, str) or _entries(value) != _OLD_DEFAULT:
            logger.info("Migration %s: %s = %r left as is", __name__, _KEY, value)
            return
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() WHERE key = $2",
            _NEW_DEFAULT,
            _KEY,
        )
        logger.info("Migration %s: %s %r -> %r", __name__, _KEY, value, _NEW_DEFAULT)


async def down(pool) -> None:
    """One-way — no rollback.

    Restoring ``org`` would bring back the ranking the replay above measured.
    An operator who wants it can add it to the setting.
    """
    return
