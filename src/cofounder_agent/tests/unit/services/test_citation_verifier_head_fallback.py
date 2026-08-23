"""citation_verifier — HEAD-hostile servers and bot walls are not dead links.

2026-08 sample: real drafts were hard-vetoed (citation_verifier is
required_to_pass=true) on links that were ALIVE — nithinanil.com answers
404 to HEAD and 200 to GET; news.ycombinator 405→200; medium.com 403s the
crawler UA entirely (unverifiable, not gone). Policy pinned here:

- ANY failing HEAD gets one GET before the URL is judged.
- 403/418/429/999 after the GET are "blocked" — reported, but excluded
  from the dead ratio (numerator AND denominator).
- Genuine 404-on-both is still dead. 401 is still dead (auth-gated is a
  bad citation for readers even when the page exists).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.citation_verifier import (
    CitationReport,
    _head_one,
    verdict_from_report,
)


def _resp(status):
    r = MagicMock()
    r.status_code = status
    return r


def _client(head_status, get_status=None):
    c = MagicMock()
    c.head = AsyncMock(return_value=_resp(head_status))
    c.get = AsyncMock(return_value=_resp(get_status if get_status is not None else head_status))
    return c


@pytest.mark.unit
@pytest.mark.asyncio
async def test_head_404_get_200_is_alive():
    client = _client(404, 200)
    url, issue = await _head_one(client, "https://x.test/a", 5.0, {})
    assert issue is None
    client.get.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_head_405_get_200_is_alive():
    client = _client(405, 200)
    _, issue = await _head_one(client, "https://x.test/a", 5.0, {})
    assert issue is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_403_on_both_is_blocked_not_dead():
    client = _client(403, 403)
    _, issue = await _head_one(client, "https://medium.test/a", 5.0, {})
    assert issue is not None and issue.reason == "blocked"
    assert "bot wall" in issue.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_404_on_both_is_dead():
    client = _client(404, 404)
    _, issue = await _head_one(client, "https://x.test/gone", 5.0, {})
    assert issue is not None and issue.reason == "bad_status"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_401_is_dead_not_blocked():
    client = _client(401, 401)
    _, issue = await _head_one(client, "https://x.test/paywalled", 5.0, {})
    assert issue is not None and issue.reason == "bad_status"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocked_links_do_not_reject_the_draft():
    """1 alive + 1 blocked must NOT trip a 30% dead-ratio veto."""
    from services.citation_verifier import CitationIssue

    report = CitationReport(
        total_urls=2, unique_urls=2,
        alive=["https://a.test/"],
        blocked=[CitationIssue(url="https://m.test/", reason="blocked",
                               detail="HTTP 403 (bot wall — unverifiable via crawler UA)",
                               status_code=403)],
        dead=[], dead_ratio=0.0,
    )
    passed, reason = await verdict_from_report(report, max_dead_ratio=0.3, min_citations=0)
    assert passed, reason
    assert "bot-walled" in reason


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dead_ratio_excludes_blocked_from_denominator():
    """1 dead + 1 blocked = 100% of the VERIFIABLE links dead, not 50%."""
    import services.citation_verifier as cvmod
    from services.citation_verifier import CitationVerifier
    from services.site_config import SiteConfig

    sc = SiteConfig(initial_config={})
    cv = CitationVerifier(site_config=sc)

    async def fake_head_one(client, url, timeout_s, headers):
        from services.citation_verifier import CitationIssue
        if "blockedhost" in url:
            return (url, CitationIssue(url=url, reason="blocked", detail="HTTP 403", status_code=403))
        return (url, CitationIssue(url=url, reason="bad_status", detail="HTTP 404", status_code=404))

    orig = cvmod._head_one
    cvmod._head_one = fake_head_one
    try:
        report = await cv.verify_citations(
            "[a](https://blockedhost.test/x) [b](https://deadhost.test/y)",
            site_url="https://www.gladlabs.io",
        )
    finally:
        cvmod._head_one = orig
    assert len(report.blocked) == 1 and len(report.dead) == 1
    assert report.dead_ratio == 1.0
