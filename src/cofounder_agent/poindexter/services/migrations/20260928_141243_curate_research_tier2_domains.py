"""Migration 20260928_141243: curate ``research_tier2_domains``.

ISSUE: none filed. Operator decision on 2026-09-28, following the
ResearchQualityService wiring (glad-labs-stack#4140).

``research_tier2_domains`` lists the hosts ResearchQualityService scores 0.85
("credible") when it ranks the research web tier; an unlisted host scores a
neutral 0.65. It shipped with three platforms that host anyone's writing:
github.com, medium.com and dev.to. In the evidence gathered while wiring the
service, their promotions were a coin flip. dev.to outranked bun.com's own
site, and a GitHub page mirroring Hacker News edged past the Authors Guild's own
post about its lawsuit. Meanwhile authoritative ``.org`` sources (python.org,
owasp.org, the ACM Digital Library, MDN) scored neutral, because ``org`` had
just left tier 1: open registration is no credibility signal. Of the 54
distinct ``.org`` hosts in the stored research corpora, most were content farms
and small blogs.

The new default drops the three platforms and adds 29 authoritative ``.org``
hosts. That means official docs, standards and security bodies, scholarly
indexes, economic data and nonprofit newsrooms, drawn from the ``.org`` hosts
that actually appear in our research plus the core bodies tech topics cite.
Re-running the stored and live research tiers with it, 34 source appearances
fell to neutral and 13 rose. Duplicate collapsing was unchanged, and every
top-slot change left was a ``.edu`` page passing a Medium post.

Only a row whose entries are exactly the previous shipped default (seeded by
glad-labs-stack#4088) is rewritten. Any other list is an operator's choice and
is left alone. ``seed_all_defaults`` is ``ON CONFLICT DO NOTHING``, so without
this migration the new default would never reach an install that already has
the row.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEY = "research_tier2_domains"
_OLD_DEFAULT = frozenset({
    "medium.com", "dev.to", "github.com", "stackoverflow.com", "wikipedia.org",
    "arxiv.org", "research.google.com", "aws.amazon.com", "cloud.google.com",
    "microsoft.com", "apple.com",
})
_NEW_DEFAULT = (
    "stackoverflow.com,wikipedia.org,arxiv.org,research.google.com,"
    "aws.amazon.com,cloud.google.com,microsoft.com,apple.com,"
    "python.org,pypi.org,postgresql.org,mozilla.org,rust-lang.org,pytorch.org,"
    "nodejs.org,apache.org,r-project.org,"
    "w3.org,ietf.org,rfc-editor.org,owasp.org,opensource.org,"
    "linuxfoundation.org,eff.org,"
    "acm.org,aclanthology.org,dblp.org,semanticscholar.org,jstor.org,nber.org,"
    "mlcommons.org,"
    "imf.org,oecd.org,worldbank.org,"
    "hbr.org,npr.org,propublica.org"
)


def _entries(csv: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in csv.split(",") if e.strip())


async def up(pool) -> None:
    """Rewrite the row if it still holds the previous default. Idempotent."""
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", _KEY)
        if not isinstance(value, str) or _entries(value) != _OLD_DEFAULT:
            logger.info("Migration %s: %s left as is", __name__, _KEY)
            return
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() WHERE key = $2",
            _NEW_DEFAULT,
            _KEY,
        )
        logger.info("Migration %s: %s rewritten to the curated default", __name__, _KEY)


async def down(pool) -> None:
    """One-way — no rollback.

    Restoring the old list would put three user-content platforms back in the
    credible tier. An operator who wants any of them can add it to the setting.
    """
    return
