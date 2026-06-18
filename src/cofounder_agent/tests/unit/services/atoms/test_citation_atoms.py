"""Atom-level tests for the citation reconciliation pair (poindexter#765).

- content.reconcile_citations (repair): links corpus-matched attribution
  subjects, honors the enable flag, no-ops without a corpus.
- qa.unlinked_attribution (advisory rail): flags the residual unmatched
  attributions, emits a scored review, no-ops without a corpus.
"""

from __future__ import annotations

from services.site_config import SiteConfig

_CORPUS = (
    "RECENT WEB SOURCES (cite if relevant):\n"
    "- [Maxim AI Blog](https://getmaxim.ai/blog/drift): context drift writeup\n"
    "- [DEV Community](https://dev.to/authora/why-x): agent memory\n"
)


# --- content.reconcile_citations -------------------------------------------

async def test_reconcile_links_corpus_matched_attribution():
    from modules.content.atoms.content_reconcile_citations import run

    state = {
        "content": "GetMaxim points out that drift hurts coherence.",
        "research_context": _CORPUS,
        "site_config": SiteConfig(initial_config={}),
    }
    out = await run(state)
    assert "[GetMaxim](https://getmaxim.ai/blog/drift)" in out["content"]


async def test_reconcile_leaves_unmatched_attribution_untouched():
    from modules.content.atoms.content_reconcile_citations import run

    state = {
        "content": "But as noted by M. Huzaifa Rizwan, tokens are not the fix.",
        "research_context": _CORPUS,
        "site_config": SiteConfig(initial_config={}),
    }
    out = await run(state)
    # No confident corpus match → no edit → atom returns nothing (channel keeps value).
    assert out == {}


async def test_reconcile_noop_when_disabled():
    from modules.content.atoms.content_reconcile_citations import run

    state = {
        "content": "GetMaxim points out that drift hurts coherence.",
        "research_context": _CORPUS,
        "site_config": SiteConfig(initial_config={"citation_reconcile_enabled": "false"}),
    }
    assert await run(state) == {}


async def test_reconcile_noop_without_corpus():
    from modules.content.atoms.content_reconcile_citations import run

    state = {
        "content": "GetMaxim points out that drift hurts coherence.",
        "research_context": "",
        "site_config": SiteConfig(initial_config={}),
    }
    assert await run(state) == {}


# --- content.reconcile_citations — re-point pass (#765 follow-up) -----------

_REPOINT_CORPUS = (
    "RECENT WEB SOURCES (cite if relevant):\n"
    "- [Maxim AI Blog](https://getmaxim.ai/blog/drift): context drift writeup\n"
)


async def test_reconcile_repoints_already_linked_fabricated_brand_url():
    # The writer linked [GetMaxim] to a fabricated path on getmaxim.ai (a 404
    # the trusted-host scrub keeps); the corpus holds the real getmaxim.ai URL.
    # The atom re-points it BEFORE qa.citations would flag it dead.
    from modules.content.atoms.content_reconcile_citations import run

    state = {
        "content": "Insight from [GetMaxim](https://getmaxim.ai/blog/wrong-slug) on drift.",
        "research_context": _REPOINT_CORPUS,
        "site_config": SiteConfig(initial_config={}),
    }
    out = await run(state)
    assert "[GetMaxim](https://getmaxim.ai/blog/drift)" in out["content"]


async def test_reconcile_repoint_skips_multitenant_host():
    # dev.to is multi-tenant: a different article is a different source. Even
    # though the link text matches the corpus host, the atom must NOT re-point.
    from modules.content.atoms.content_reconcile_citations import run

    corpus = (
        "RECENT WEB SOURCES (cite if relevant):\n"
        "- [DEV post](https://dev.to/authora/why-x): agent memory\n"
    )
    state = {
        "content": "As [dev.to](https://dev.to/someoneelse/fake-post) covers it.",
        "research_context": corpus,
        "site_config": SiteConfig(initial_config={}),
    }
    assert await run(state) == {}


async def test_reconcile_repoint_noop_when_disabled():
    from modules.content.atoms.content_reconcile_citations import run

    state = {
        "content": "Insight from [GetMaxim](https://getmaxim.ai/blog/wrong-slug) on drift.",
        "research_context": _REPOINT_CORPUS,
        "site_config": SiteConfig(initial_config={"citation_repoint_enabled": "false"}),
    }
    assert await run(state) == {}


# --- qa.unlinked_attribution -----------------------------------------------

async def test_unlinked_attribution_flags_residual():
    from modules.content.atoms.qa_unlinked_attribution import run

    content = (
        "GetMaxim points out that drift hurts coherence. "
        "But as noted by M. Huzaifa Rizwan, tokens are not the fix. "
        "AI companions suffer erasure (Ai Insights)."
    )
    state = {
        "content": content,
        "research_context": _CORPUS,
        "site_config": SiteConfig(initial_config={}),
        "database_service": None,
    }
    out = await run(state)
    reviews = out["qa_rail_reviews"]
    assert len(reviews) == 1
    rev = reviews[0]
    assert rev["reviewer"] == "unlinked_attribution"
    # GetMaxim matched the corpus → not flagged; the author + unknown brand are.
    assert "M. Huzaifa Rizwan" in rev["feedback"]
    assert "Ai Insights" in rev["feedback"]
    assert "GetMaxim" not in rev["feedback"]
    assert rev["score"] < 100
    assert rev["approved"] is False


async def test_unlinked_attribution_clean_pass():
    from modules.content.atoms.qa_unlinked_attribution import run

    state = {
        "content": "As discussed in [DEV Community](https://dev.to/authora/why-x), memory differs.",
        "research_context": _CORPUS,
        "site_config": SiteConfig(initial_config={}),
        "database_service": None,
    }
    out = await run(state)
    rev = out["qa_rail_reviews"][0]
    assert rev["score"] == 100.0
    assert rev["approved"] is True


async def test_unlinked_attribution_noop_without_corpus():
    from modules.content.atoms.qa_unlinked_attribution import run

    state = {
        "content": "As noted by Someone Important, this matters.",
        "research_context": "",
        "site_config": SiteConfig(initial_config={}),
        "database_service": None,
    }
    assert await run(state) == {}


async def test_unlinked_attribution_noop_when_disabled():
    from modules.content.atoms.qa_unlinked_attribution import run

    state = {
        "content": "But as noted by M. Huzaifa Rizwan, tokens fail.",
        "research_context": _CORPUS,
        "site_config": SiteConfig(initial_config={"unlinked_attribution_enabled": "false"}),
        "database_service": None,
    }
    assert await run(state) == {}
