"""Unit tests for the citation-matching core (``_citation_match``).

These are the pure, DB-free primitives shared by ``content.reconcile_citations``
(repair) and ``qa.unlinked_attribution`` (advisory). They drive the design:
attribution detection, subject↔corpus matching, link insertion at attribution
sites only, and unmatched-attribution flagging.

Reference case (Glad-Labs/poindexter#765): a real post attributed claims to
"M. Huzaifa Rizwan", "GetMaxim", and "(Ai Insights)" without links, while
"[DEV Community](url)" and "[arXiv](url)" WERE linked. Repair should link the
corpus-matched names and leave the rest for the advisory flag.
"""

from __future__ import annotations

from modules.content.atoms._citation_match import (
    CorpusSource,
    find_attributions,
    find_unmatched_attributions,
    link_matched_attributions,
    match_subject,
    parse_corpus,
)

# --- parse_corpus -----------------------------------------------------------

def test_parse_corpus_extracts_markdown_pairs():
    rc = (
        "RECENT WEB SOURCES (cite if relevant):\n"
        "- [Why Your AI Agent Gets Dumber](https://dev.to/authora/why-x): a snippet here\n"
        "- [Maxim AI Blog](https://getmaxim.ai/blog/drift): context drift writeup\n"
    )
    sources = parse_corpus(rc)
    urls = {s.url for s in sources}
    assert "https://dev.to/authora/why-x" in urls
    assert "https://getmaxim.ai/blog/drift" in urls
    # snippet after the ": " is captured into the source text for handle mining
    maxim = next(s for s in sources if "getmaxim.ai" in s.url)
    assert "drift" in maxim.text.lower()


def test_parse_corpus_captures_bare_urls():
    rc = "Some source: https://arxiv.org/html/2601.11653v1 and more text"
    sources = parse_corpus(rc)
    assert any("arxiv.org" in s.url for s in sources)


def test_parse_corpus_skips_internal_post_links():
    # Internal /posts/ links are not external citations; they must not become
    # corpus sources (no scheme/host to derive a handle from anyway).
    rc = "- [Breaking the Memory Wall](/posts/breaking-the-memory-wall-346f4919)"
    sources = parse_corpus(rc)
    assert sources == []


def test_parse_corpus_empty_string():
    assert parse_corpus("") == []
    assert parse_corpus(None) == []  # type: ignore[arg-type]


# --- find_attributions ------------------------------------------------------

def test_find_attributions_noted_by_form():
    text = "But as noted by M. Huzaifa Rizwan, relying solely on tokens is wrong."
    attrs = find_attributions(text)
    subjects = [a.subject for a in attrs]
    assert "M. Huzaifa Rizwan" in subjects


def test_find_attributions_subject_first_verb_form():
    text = "GetMaxim points out that context drift hurts coherence."
    attrs = find_attributions(text)
    assert any(a.subject == "GetMaxim" for a in attrs)


def test_find_attributions_according_to_form():
    text = "According to Kore.ai, memory must be decoupled from the window."
    attrs = find_attributions(text)
    assert any(a.subject == "Kore.ai" for a in attrs)


def test_find_attributions_parenthetical_form():
    text = "AI companions suffer identity erasure if not reinforced (Ai Insights)."
    attrs = find_attributions(text)
    assert any(a.subject == "Ai Insights" for a in attrs)


def test_find_attributions_skips_markdown_linked_subject():
    # Already-cited: the subject sits inside a markdown link, so it is NOT an
    # unlinked attribution. find_attributions marks it already_linked.
    text = "As discussed in [DEV Community](https://dev.to/x), memory differs."
    attrs = find_attributions(text)
    # Either no attribution is surfaced, or it's flagged already_linked.
    assert all(a.already_linked for a in attrs)


def test_find_attributions_ignores_first_person_and_rhetoric():
    # "according to our analysis" / "as we noted" are rhetoric, not sources.
    assert find_attributions("According to our analysis, drift is real.") == []
    assert find_attributions("As we noted, the window is not the fix.") == []


# --- match_subject ----------------------------------------------------------

def _sources():
    return [
        CorpusSource(url="https://dev.to/authora/why-x", title="Why Your AI Agent Gets Dumber", text="why your ai agent gets dumber authora"),
        CorpusSource(url="https://getmaxim.ai/blog/drift", title="Context Drift", text="context drift coherence maxim"),
        CorpusSource(url="https://kore.ai/memory", title="Agent Memory", text="agent memory kore"),
        CorpusSource(url="https://arxiv.org/html/2601.11653v1", title="Unbounded Context", text="unbounded context growth"),
    ]


def test_match_subject_brand_via_domain_sld():
    src = match_subject("GetMaxim", _sources())
    assert src is not None and "getmaxim.ai" in src.url


def test_match_subject_domain_with_tld():
    src = match_subject("Kore.ai", _sources())
    assert src is not None and "kore.ai" in src.url


def test_match_subject_author_not_in_corpus_returns_none():
    # The author name appears nowhere in the corpus text/titles/domains.
    assert match_subject("M. Huzaifa Rizwan", _sources()) is None


def test_match_subject_unknown_brand_returns_none():
    assert match_subject("Ai Insights", _sources()) is None


# --- link_matched_attributions (repair, scan-1) -----------------------------

def test_link_repairs_matched_attribution_only():
    content = (
        "GetMaxim points out that drift hurts coherence. "
        "But as noted by M. Huzaifa Rizwan, tokens are not the fix."
    )
    new_content, linked = link_matched_attributions(content, _sources())
    # GetMaxim matched the corpus → linked to its URL.
    assert "[GetMaxim](https://getmaxim.ai/blog/drift)" in new_content
    assert any(x["url"] == "https://getmaxim.ai/blog/drift" for x in linked)
    # The unmatched author name is left untouched (no guessed link).
    assert "M. Huzaifa Rizwan," in new_content
    assert "](https://dev.to" not in new_content.split("M. Huzaifa Rizwan")[0][-40:]


def test_link_is_idempotent():
    content = "GetMaxim points out that drift hurts coherence."
    once, _ = link_matched_attributions(content, _sources())
    twice, linked2 = link_matched_attributions(once, _sources())
    assert once == twice
    assert linked2 == []  # nothing left to link the second time


def test_link_does_not_touch_non_attribution_prose():
    # "Python" appears as ordinary prose, not an attribution subject — even
    # though a python.org-style source could exist, it must not be linked.
    sources = [CorpusSource(url="https://python.org", title="Python", text="python docs")]
    content = "Python is a great language for building AI agents."
    new_content, linked = link_matched_attributions(content, sources)
    assert new_content == content
    assert linked == []


# --- find_unmatched_attributions (advisory, scan-2) -------------------------

def test_unmatched_flags_author_and_unknown_brand():
    content = (
        "But as noted by M. Huzaifa Rizwan, tokens are not the fix. "
        "AI companions suffer erasure (Ai Insights)."
    )
    unmatched = find_unmatched_attributions(content, _sources())
    assert "M. Huzaifa Rizwan" in unmatched
    assert "Ai Insights" in unmatched


def test_unmatched_excludes_corpus_matched_and_linked():
    content = (
        "GetMaxim points out that drift hurts coherence. "
        "As discussed in [DEV Community](https://dev.to/authora/why-x), memory differs."
    )
    unmatched = find_unmatched_attributions(content, _sources())
    # GetMaxim matches the corpus; DEV Community is already linked.
    assert unmatched == []


def test_unmatched_empty_when_no_corpus():
    # Without a corpus we can't tell real from fabricated — defer to the LLM
    # pass rather than flagging every attribution. Returns [].
    content = "As noted by Someone Important, this matters."
    assert find_unmatched_attributions(content, []) == []
