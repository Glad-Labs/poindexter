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
    repoint_fabricated_citations,
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


def test_link_repairs_single_word_brand_parenthetical():
    """A single-word, simple-title-case brand in parens — '(Keychron)' — is a
    real citation when the brand is in the corpus, and must be linked.

    The shape heuristic (``_looks_like_source_name``) rejects it for lacking
    internal caps / a dot / all-caps, so repair needs the corpus-grounded
    acceptance path. Reference: validation post 12db663a (mechanical keyboard
    switches) shipped "...actuation (Keychron)." unlinked even though
    keychron.com was in the research corpus (the writer had linked an earlier
    "According to [Keychron](https://www.keychron.com/...)").
    """
    sources = [CorpusSource(
        url="https://www.keychron.com/blogs/news/types-of-keyboard-switches",
        title="Types of Keyboard Switches",
        text="types of keyboard switches keychron",
    )]
    content = "They make a satisfying click upon actuation (Keychron)."
    new_content, linked = link_matched_attributions(content, sources)
    assert (
        "([Keychron](https://www.keychron.com/blogs/news/types-of-keyboard-switches))"
        in new_content
    )
    assert any(x["subject"] == "Keychron" for x in linked)


def test_link_skips_single_word_non_brand_parenthetical():
    """'(Recommended)' / '(Optional)' are editorial asides, not citations.

    The corpus-grounded acceptance must NOT fire for them — they match no
    corpus domain, so they stay untouched (no false link). This is the
    precision guard that lets the single-word path exist at all.
    """
    sources = [CorpusSource(
        url="https://www.keychron.com/x", title="Keychron", text="keychron switches",
    )]
    content = "Use linear switches for gaming (Recommended)."
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


# --- repoint_fabricated_citations (repair, scan-3) --------------------------
#
# The writer sometimes wraps a brand in a markdown link to that brand's OWN
# domain but invents the path (a 404 the host-only link scrub keeps because the
# host is trusted). When the corpus holds the real URL for that brand on that
# exact domain, re-pointing the fabricated path to it is high-precision.
#
# The hard precision boundary: this is safe ONLY on single-brand domains where
# "same registrable domain" implies "same source". On multi-tenant platforms
# (github.com, arxiv.org, dev.to, …) a different path is DIFFERENT CONTENT — a
# different repo/paper/article — so re-pointing there would silently mis-cite.
# Those are excluded by denylist and must stay excluded.

def _brand_sources():
    return [
        CorpusSource(url="https://getmaxim.ai/blog/drift", title="Context Drift", text="context drift coherence maxim"),
        CorpusSource(url="https://kore.ai/memory", title="Agent Memory", text="agent memory kore"),
    ]


def test_repoint_fixes_brand_domain_wrong_path():
    # [GetMaxim] linked to a fabricated path on getmaxim.ai; corpus holds the
    # real getmaxim.ai URL → re-point to it, preserving the link text.
    content = "Insight from [GetMaxim](https://getmaxim.ai/blog/fabricated-slug) on drift."
    new, repointed = repoint_fabricated_citations(content, _brand_sources())
    assert "[GetMaxim](https://getmaxim.ai/blog/drift)" in new
    assert "fabricated-slug" not in new
    assert len(repointed) == 1
    assert repointed[0]["old"] == "https://getmaxim.ai/blog/fabricated-slug"
    assert repointed[0]["new"] == "https://getmaxim.ai/blog/drift"
    assert repointed[0]["text"] == "GetMaxim"


def test_repoint_skips_multitenant_arxiv():
    # arXiv is multi-tenant: a different /abs/ id is a DIFFERENT paper. Even
    # though "arXiv" matches the arxiv.org corpus source by handle, re-pointing
    # would cite the wrong paper → must NOT fire.
    sources = [CorpusSource(url="https://arxiv.org/abs/2601.11653", title="Unbounded Context", text="unbounded context growth")]
    content = "See [arXiv](https://arxiv.org/abs/9999.00000) for the proof."
    new, repointed = repoint_fabricated_citations(content, sources)
    assert new == content
    assert repointed == []


def test_repoint_skips_multitenant_github():
    sources = [CorpusSource(url="https://github.com/real/repo", title="Real Repo", text="real repo")]
    content = "Code lives at [GitHub](https://github.com/fake/repo)."
    new, repointed = repoint_fabricated_citations(content, sources)
    assert new == content
    assert repointed == []


def test_repoint_skips_multitenant_devto():
    sources = [CorpusSource(url="https://dev.to/authora/why-x", title="Why X", text="why x agent memory")]
    content = "As [dev.to](https://dev.to/someoneelse/other-post) explains it."
    new, repointed = repoint_fabricated_citations(content, sources)
    assert new == content
    assert repointed == []


def test_repoint_noop_when_url_already_correct():
    # The writer already linked the real corpus URL → nothing to change.
    content = "Insight from [GetMaxim](https://getmaxim.ai/blog/drift) on drift."
    new, repointed = repoint_fabricated_citations(content, _brand_sources())
    assert new == content
    assert repointed == []


def test_repoint_skips_cross_domain():
    # The fabricated link is on a DIFFERENT domain than the corpus source. We
    # only fix wrong PATHS on the same brand site, never redirect across domains.
    content = "Insight from [GetMaxim](https://someblog.example/p/x) on drift."
    new, repointed = repoint_fabricated_citations(content, _brand_sources())
    assert new == content
    assert repointed == []


def test_repoint_skips_ambiguous_multiple_sources_same_domain():
    # Two corpus sources share getmaxim.ai → no unambiguous re-point target.
    sources = [
        CorpusSource(url="https://getmaxim.ai/blog/drift", title="Drift", text="drift maxim"),
        CorpusSource(url="https://getmaxim.ai/blog/memory", title="Memory", text="memory maxim"),
    ]
    content = "Insight from [GetMaxim](https://getmaxim.ai/blog/fabricated) on drift."
    new, repointed = repoint_fabricated_citations(content, sources)
    assert new == content
    assert repointed == []


def test_repoint_skips_when_text_does_not_name_brand():
    # Generic link text ("this guide") doesn't name the brand → no confident
    # match → leave the writer's link alone.
    content = "See [this guide](https://getmaxim.ai/blog/fabricated)."
    new, repointed = repoint_fabricated_citations(content, _brand_sources())
    assert new == content
    assert repointed == []


def test_repoint_is_idempotent():
    content = "Insight from [GetMaxim](https://getmaxim.ai/blog/fabricated) on drift."
    once, first = repoint_fabricated_citations(content, _brand_sources())
    twice, second = repoint_fabricated_citations(once, _brand_sources())
    assert once == twice
    assert len(first) == 1
    assert second == []  # nothing left to re-point the second time


def test_repoint_noop_without_corpus():
    content = "Insight from [GetMaxim](https://getmaxim.ai/blog/fabricated)."
    assert repoint_fabricated_citations(content, []) == (content, [])
    assert repoint_fabricated_citations("", _brand_sources()) == ("", [])


def test_repoint_honors_explicit_multitenant_override():
    # An operator can add a brand-like host to the denylist to suppress
    # re-pointing on it; passing it in must short-circuit the match.
    content = "Insight from [GetMaxim](https://getmaxim.ai/blog/fabricated) on drift."
    new, repointed = repoint_fabricated_citations(
        content, _brand_sources(), multi_tenant_hosts=frozenset({"getmaxim.ai"}),
    )
    assert new == content
    assert repointed == []
