"""
Unit tests for ResearchQualityService.

All tests are pure-function — zero DB, LLM, or network calls.
Tests verify result filtering, domain credibility scoring, snippet quality scoring,
recency scoring from the snippet's leading date, deduplication, uniqueness
scoring, and that every tunable is read from app_settings at call time.

Several fixtures are modeled on real DuckDuckGo results from the research
corpora stored on prod (pipeline_versions, 2026-06-23 -> 09-26): the shapes
the service actually sees now that ResearchService.build_context runs every
web tier through it. The text itself is synthetic.
"""

from datetime import date
from itertools import permutations
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.services.research_quality_service import (
    ResearchQualityService,
    ScoredSource,
    _published_age_days,
)
from poindexter.services.settings_defaults import DEFAULTS
from poindexter.services.site_config import SiteConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_result(
    url: str = "https://example.com/article",
    snippet: str = "This is a sufficiently long snippet with at least ten words in it yes.",
    title: str = "Test Title",
    content: str = "",
) -> dict:
    """A WebResearcher result: {title, url, snippet, content}."""
    return {"title": title, "url": url, "snippet": snippet, "content": content}


@pytest.fixture
def service() -> ResearchQualityService:
    return ResearchQualityService(site_config=SiteConfig())


# ---------------------------------------------------------------------------
# _is_valid_result
# ---------------------------------------------------------------------------


class TestIsValidResult:
    def test_valid_result_passes(self, service):
        r = make_result()
        assert service._is_valid_result(r) is True

    def test_missing_url_rejected(self, service):
        r = make_result()
        del r["url"]
        assert service._is_valid_result(r) is False

    def test_missing_snippet_rejected(self, service):
        r = {"title": "T", "url": "https://example.com"}
        assert service._is_valid_result(r) is False

    def test_none_snippet_rejected(self, service):
        r = make_result()
        r["snippet"] = None
        assert service._is_valid_result(r) is False

    def test_short_snippet_rejected(self, service):
        r = make_result(snippet="Too short")
        assert service._is_valid_result(r) is False

    def test_snippet_below_word_minimum_rejected(self, service):
        # Build a snippet with exactly 9 space-separated tokens (below MIN_SNIPPET_WORDS=10)
        # and pad to > MIN_SNIPPET_LENGTH=50 chars using hyphens so split() yields 9 words
        snippet = "one two three four five six seven eight nine"  # 9 words, 44 chars
        # pad with non-space characters to exceed 50 char limit while keeping word count at 9
        snippet_padded = snippet + "--------"  # now 52 chars, still 9 words
        r = make_result(snippet=snippet_padded)
        assert service._is_valid_result(r) is False

    @pytest.mark.parametrize(
        "junk",
        [
            # A Telegram channel preview, a link-aggregator stub and a page whose
            # snippet is its logo: all offered to the writer as sources in the
            # stored corpora before the service was wired.
            "You can view and join @examplechan right away.",
            "Visit the post for more.",
            "PageSpeed Insights logo. PageSpeed Insights.",
        ],
    )
    def test_real_junk_snippets_rejected(self, service, junk):
        assert service._is_valid_result(make_result(snippet=junk)) is False

    def test_serper_link_key_is_not_read(self, service):
        """The input contract is WebResearcher's ``url``. Serper's ``link`` is
        retired: nothing produces it any more."""
        r = make_result()
        r["link"] = r.pop("url")
        assert service._is_valid_result(r) is False

    def test_minimums_come_from_settings(self):
        svc = ResearchQualityService(
            site_config=SiteConfig(initial_config={"research_min_snippet_words": "20"})
        )
        assert svc._is_valid_result(make_result()) is False  # 14 words


# ---------------------------------------------------------------------------
# _extract_domain
# ---------------------------------------------------------------------------


class TestExtractDomain:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.example.com/page", "example.com"),
            ("http://blog.github.com/post", "blog.github.com"),
            ("https://arxiv.org/abs/123", "arxiv.org"),
            ("https://docs.python.org/3/", "docs.python.org"),
            ("not-a-url", ""),
            # A port and a path-less query no longer leak into the host.
            ("https://Example.com:8443/x", "example.com"),
            ("https://www.example.com?utm_source=ddg", "example.com"),
            # Malformed (unclosed IPv6 bracket): urlparse raises, we return "".
            ("http://[::1/x", ""),
        ],
    )
    def test_extract_domain(self, service, url, expected):
        assert service._extract_domain(url) == expected


# ---------------------------------------------------------------------------
# _score_domain_credibility
# ---------------------------------------------------------------------------


class TestScoreDomainCredibility:
    def test_edu_domain_tier1(self, service):
        assert service._score_domain_credibility("mit.edu") == pytest.approx(0.95)

    def test_gov_domain_tier1(self, service):
        assert service._score_domain_credibility("cdc.gov") == pytest.approx(0.95)

    def test_ac_uk_domain_tier1(self, service):
        assert service._score_domain_credibility("ox.ac.uk") == pytest.approx(0.95)

    @pytest.mark.parametrize("host", ["fatsil.org", "blazebeaver.org", "wacclearinghouse.org"])
    def test_org_is_not_tier1_by_suffix(self, service, host):
        """Tier 1 shipped with "org". In the replay of 181 stored web tiers,
        ranked on quality alone, these suffix-only .org hosts took the top slot
        from DuckDuckGo's first result; fatsil.org outranked a textbook author's
        own page."""
        assert service._score_domain_credibility(host) == pytest.approx(0.65)

    @pytest.mark.parametrize("host", ["en.wikipedia.org", "arxiv.org"])
    def test_wikipedia_and_arxiv_stay_credible_through_tier2(self, service, host):
        assert service._score_domain_credibility(host) == pytest.approx(0.85)

    def test_stackoverflow_com_tier2(self, service):
        assert service._score_domain_credibility("stackoverflow.com") == pytest.approx(0.85)

    @pytest.mark.parametrize(
        "host", ["github.com", "medium.com", "someone.medium.com", "dev.to"]
    )
    def test_user_content_platforms_are_neutral(self, service, host):
        """Dropped from tier 2 on 2026-09-28: they host anyone's writing, and
        their promotions were a coin flip (dev.to over bun.com's own site; a
        GitHub page mirroring Hacker News over the Authors Guild's own post)."""
        assert service._score_domain_credibility(host) == pytest.approx(0.65)

    @pytest.mark.parametrize(
        "host",
        [
            # .org hosts that appeared in the stored research corpora
            "genai.owasp.org", "dl.acm.org", "developer.mozilla.org",
            "wiki.postgresql.org", "cran.r-project.org", "dblp.org", "hbr.org",
            "npr.org", "propublica.org", "mlcommons.org", "opensource.org",
            # and core bodies tech topics cite
            "docs.python.org", "datatracker.ietf.org", "www.rfc-editor.org",
            "aclanthology.org", "data.worldbank.org",
        ],
    )
    def test_authoritative_org_hosts_are_tier2(self, service, host):
        """Added 2026-09-28, once "org" had left tier 1: authoritative sources
        that would otherwise score as neutral as the content farms that share
        their suffix. The domain passes through _extract_domain first, as it
        does in filter_and_score, so a leading www. is stripped."""
        domain = service._extract_domain(f"https://{host}/x")
        assert service._score_domain_credibility(domain) == pytest.approx(0.85)

    @pytest.mark.parametrize("host", ["docs.python.org", "learn.microsoft.com"])
    def test_subdomain_of_a_tier2_domain_is_tier2(self, service, host):
        """Tier 2 used to match exactly, so learn.microsoft.com scored as a
        generic host (0.65) while microsoft.com scored 0.85."""
        assert service._score_domain_credibility(host) == pytest.approx(0.85)

    def test_lookalike_of_a_tier2_domain_is_not_tier2(self, service):
        assert service._score_domain_credibility("notpython.org") == pytest.approx(0.65)

    def test_tier1_setting_accepts_a_full_domain(self):
        """Tier 1 once matched suffixes only, so a full domain there could never
        match: ``"nature.com".endswith(".nature.com")`` is False. The setting
        also replaces the defaults rather than adding to them."""
        svc = ResearchQualityService(
            site_config=SiteConfig(initial_config={"research_tier1_domains": "nature.com"})
        )
        assert svc._score_domain_credibility("nature.com") == pytest.approx(0.95)
        assert svc._score_domain_credibility("blogs.nature.com") == pytest.approx(0.95)
        assert svc._score_domain_credibility("mit.edu") == pytest.approx(0.65)

    @pytest.mark.parametrize(
        "host",
        [
            "randomsite.com",
            "startup.io",
            "site.xyz",
            # Scored 0.5 by the old non-.com/.net/.io/.co floor, which cost
            # DuckDuckGo's first result the top slot in 16 replayed tiers.
            "shopify.engineering",
            "hatchet.run",
            "galileo.ai",
            "otto.de",
            # Scored 0.75-0.8 from a hardcoded mid-tier until 2026-09-28.
            "techcrunch.com",
            "forbes.com",
        ],
    )
    def test_any_unlisted_host_is_neutral(self, service, host):
        assert service._score_domain_credibility(host) == pytest.approx(0.65)

    def test_listing_a_publication_in_tier2_lifts_it(self):
        svc = ResearchQualityService(
            site_config=SiteConfig(initial_config={"research_tier2_domains": "techcrunch.com"})
        )
        assert svc._score_domain_credibility("techcrunch.com") == pytest.approx(0.85)

    def test_empty_domain(self, service):
        assert service._score_domain_credibility("") == pytest.approx(0.5)

    def test_case_insensitive(self, service):
        assert service._score_domain_credibility("MIT.EDU") == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# _score_snippet_quality
# ---------------------------------------------------------------------------


class TestScoreSnippetQuality:
    def test_empty_snippet_returns_zero(self, service):
        assert service._score_snippet_quality("") == pytest.approx(0.0)

    def test_long_snippet_higher_than_short(self, service):
        short = "word " * 15
        long_s = "word " * 35
        assert service._score_snippet_quality(long_s) > service._score_snippet_quality(short)

    def test_query_term_match_boosts_score(self, service):
        snippet = "Python is a popular programming language used widely."
        without_query = service._score_snippet_quality(snippet)
        with_query = service._score_snippet_quality(snippet, query="Python programming language")
        assert with_query == pytest.approx(without_query + 0.2)

    def test_relevance_is_the_share_of_significant_terms_matched(self, service):
        snippet = "A compiler construction course covering parsing and code generation."
        base = service._score_snippet_quality(snippet)
        scored = service._score_snippet_quality(
            snippet, query="compiler construction textbook"
        )
        assert scored == pytest.approx(base + 0.2 * 2 / 3)

    def test_relevance_matches_whole_significant_words_only(self, service):
        """The old matcher took the topic's first three words and substring-
        matched them, so "How to build a compiler" credited this snippet for
        "how" (in "how-to") and "to" (in "tomato")."""
        snippet = "A tomato how-to guide for growing vegetables in raised garden beds this spring."
        assert service._score_snippet_quality(
            snippet, query="How to build a compiler"
        ) == pytest.approx(service._score_snippet_quality(snippet))

    def test_spam_keyword_reduces_score(self, service):
        clean = "This is a well-written informative snippet about software engineering topics."
        spammy = clean + " Click here to buy now limited time!"
        assert service._score_snippet_quality(clean) > service._score_snippet_quality(spammy)

    def test_score_bounded_0_to_1(self, service):
        for snippet in ["", "a", "word " * 100]:
            score = service._score_snippet_quality(snippet)
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# _published_age_days — the date DuckDuckGo leads a snippet with
# ---------------------------------------------------------------------------

_TODAY = date(2026, 9, 28)


class TestPublishedAgeDays:
    @pytest.mark.parametrize(
        "snippet,expected",
        [
            ("3 days ago - The use of the generation label ...", 3),
            ("1 day ago · A step-by-step framework for auto ...", 1),
            ("2 hours ago · Benchmarks landed overnight ...", 2 / 24),
            ("3 weeks ago - The negative effect is more likely ...", 21),
            ("4 months ago - Release notes ...", 120),
            ("2 years ago - An older survey ...", 730),
            ("15 hours ago ... .self: A new top-level domain ...", 15 / 24),
        ],
    )
    def test_relative_dates(self, snippet, expected):
        assert _published_age_days(snippet, _TODAY) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "snippet,published",
        [
            ("February 15, 2026 - This shift erodes human judgment ...", date(2026, 2, 15)),
            ("Mar 15, 2024 · OOTP Developments Forums ...", date(2024, 3, 15)),
            ("Jan 12, 2021 ... ... no-results-message ...", date(2021, 1, 12)),
            ("Jan. 12, 2021 - A dotted abbreviation ...", date(2021, 1, 12)),
            ("Sept 5, 2026 - The four-letter September ...", date(2026, 9, 5)),
            ("Jan 2020 - Month and year only ...", date(2020, 1, 1)),
            ("2026-09-20 - An ISO date ...", date(2026, 9, 20)),
        ],
    )
    def test_absolute_dates(self, snippet, published):
        assert _published_age_days(snippet, _TODAY) == float((_TODAY - published).days)

    def test_future_date_counts_as_today(self):
        """An event page dated ahead of today is fresh, not negative-old."""
        assert _published_age_days("Oct 10, 2026 - Visual Studio Live ...", _TODAY) == 0.0

    @pytest.mark.parametrize(
        "snippet",
        [
            "Local LLM inference is memory-bound: VRAM capacity decides ...",
            # A month opening ordinary prose, with no date separator after it.
            "May 2026 bring faster builds to everyone who ships software.",
            # Not a month name.
            "Mayor 12, 2020 - the mayor said ...",
            # Not a real date.
            "Feb 30, 2026 - an impossible day ...",
            # A duration, not an age.
            "3 days of hiking - a trail guide ...",
            "",
        ],
    )
    def test_no_date(self, snippet):
        assert _published_age_days(snippet, _TODAY) is None


# ---------------------------------------------------------------------------
# _score_recency
# ---------------------------------------------------------------------------


class TestScoreRecency:
    def test_no_date_returns_neutral(self, service):
        assert service._score_recency(None) == pytest.approx(0.7)

    @pytest.mark.parametrize(
        "age_days,expected",
        [(0, 0.9), (2 / 24, 0.9), (7, 0.9), (8, 0.8), (120, 0.8), (365, 0.8), (366, 0.6)],
    )
    def test_default_windows(self, service, age_days, expected):
        assert service._score_recency(age_days) == pytest.approx(expected)

    def test_windows_come_from_settings(self):
        svc = ResearchQualityService(
            site_config=SiteConfig(
                initial_config={
                    "research_recency_fresh_days": "30",
                    "research_recency_recent_days": "60",
                }
            )
        )
        assert svc._score_recency(20) == pytest.approx(0.9)
        assert svc._score_recency(90) == pytest.approx(0.6)

    def test_absolute_dates_are_no_longer_all_scored_old(self, service):
        """The old scorer substring-matched hour/day/week/month in a Serper
        ``date`` field, so any absolute date scored 0.6 however recent. 253 of
        815 stored web sources lead with an absolute date."""
        age = _published_age_days("Sep 25, 2026 - Three days old ...", _TODAY)
        assert service._score_recency(age) == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# _calculate_similarity
# ---------------------------------------------------------------------------


class TestCalculateSimilarity:
    def test_identical_texts_score_1(self, service):
        text = "The quick brown fox jumps over the lazy dog."
        assert service._calculate_similarity(text, text) == pytest.approx(1.0)

    def test_completely_different_texts_low_score(self, service):
        a = "apple banana cherry orange grape"
        b = "quantum physics relativity nuclear reactor"
        score = service._calculate_similarity(a, b)
        assert score < 0.5

    def test_empty_texts_return_0(self, service):
        assert service._calculate_similarity("", "") == pytest.approx(0.0)
        assert service._calculate_similarity("text", "") == pytest.approx(0.0)

    def test_near_duplicate_exceeds_threshold(self, service):
        base = "Python is a great programming language for data science and automation."
        slightly_modified = (
            "Python is a great programming language for data science and automating tasks."
        )
        score = service._calculate_similarity(base, slightly_modified)
        assert score > service.SIMILARITY_THRESHOLD


# A realistic research snippet: long enough to clear difflib's 200-element
# autojunk floor (198 of 200 sampled snippets do) and varied, because a
# fixture that repeats one phrase hands the matcher huge identical blocks and
# hides the collapse being pinned.
_SNIPPET_VOCAB = (
    "distributed training keeps policy weights synchronized across separate jobs "
    "without a dedicated interconnect the adapter carries the update and object "
    "storage stands in for a shared filesystem so trainer and inference replicas "
    "agree on the version they serve while stale rollouts score against an "
    "obsolete policy and quietly poison the gradient"
).split()
_SNIPPET = " ".join(
    _SNIPPET_VOCAB[(i * 7 + (i * i) % 11) % len(_SNIPPET_VOCAB)] for i in range(180)
)


def _reworded(text: str, every: int) -> str:
    """A re-scrape or light syndication rewrite: differences SCATTERED through
    the snippet rather than gathered in one place."""
    return " ".join(("data" if i % every == 0 else w) for i, w in enumerate(text.split()))


class TestSimilarityOnRealisticSnippets:
    """The short fixtures above cannot see this: ``SequenceMatcher`` compares
    CHARACTERS when handed strings, and past 200 elements its autojunk
    heuristic discards every element occurring in more than 1% of the
    sequence — across a snippet that is every common letter.

    Measured 2026-09-23 over 120 real snippet pairs, **75 flipped the dedup
    verdict** at 0.7: pairs 83-87% alike scored 0.32-0.45 and were kept as
    distinct sources, padding research_context with the same facts twice.
    """

    def test_a_reworded_snippet_is_caught_as_a_duplicate(self, service):
        score = service._calculate_similarity(_SNIPPET, _reworded(_SNIPPET, 6))
        assert score > service.SIMILARITY_THRESHOLD

    def test_the_old_char_comparison_would_have_missed_it(self, service):
        """The guard. This is the comparison that shipped; it must still fall
        under the threshold on a pair the fix catches, or the fix is no longer
        doing anything."""
        from difflib import SequenceMatcher

        rewritten = _reworded(_SNIPPET, 6)
        shipped = SequenceMatcher(None, _SNIPPET.lower(), rewritten.lower()).ratio()
        assert shipped < service.SIMILARITY_THRESHOLD, (
            "autojunk no longer collapses the ratio; re-check the fix"
        )
        assert service._calculate_similarity(_SNIPPET, rewritten) > service.SIMILARITY_THRESHOLD

    def test_genuinely_different_snippets_stay_below_the_threshold(self, service):
        """The fix must not collapse everything into one duplicate."""
        other = " ".join(["quarterly revenue guidance margin expansion retail footprint"] * 30)
        assert service._calculate_similarity(_SNIPPET, other) < service.SIMILARITY_THRESHOLD

    def test_identical_long_snippets_still_score_1(self, service):
        assert service._calculate_similarity(_SNIPPET, _SNIPPET) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _deduplicate
# ---------------------------------------------------------------------------


def _make_scored_source(
    snippet: str, score: float, url: str = "https://example.com"
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="Title",
        snippet=snippet,
        domain="example.com",
        domain_credibility=score,
        snippet_quality=score,
        recency_score=score,
        uniqueness_score=score,
        overall_score=score,
    )


_DUP = "identical content repeated to ensure high similarity score yes"


class TestDeduplicate:
    def test_no_duplicates_keeps_all(self):
        svc = ResearchQualityService(site_config=SiteConfig())
        a = _make_scored_source("First unique snippet with enough words", 8.0, "https://a.com")
        b = _make_scored_source("Second completely different text here", 7.0, "https://b.com")
        result = svc._deduplicate([a, b])
        assert len(result) == 2

    def test_higher_score_winner_is_kept(self):
        svc = ResearchQualityService(site_config=SiteConfig())
        winner = _make_scored_source(_DUP, 9.0, "https://winner.com")
        loser = _make_scored_source(_DUP, 6.0, "https://loser.com")
        result = svc._deduplicate([loser, winner])
        assert [s.url for s in result] == ["https://winner.com"]

    def test_equal_score_keeps_the_first_listed(self):
        svc = ResearchQualityService(site_config=SiteConfig())
        s1 = _make_scored_source(_DUP, 7.5, "https://s1.com")
        s2 = _make_scored_source(_DUP, 7.5, "https://s2.com")
        result = svc._deduplicate([s1, s2])
        assert [s.url for s in result] == ["https://s1.com"]

    @pytest.mark.parametrize("order", list(permutations([5.0, 7.0, 9.0])))
    def test_a_triplet_collapses_to_its_best_copy_in_any_order(self, order):
        """The old pairwise pass stopped at a source's first match, so
        ``[9, 5, 7]`` kept both 9 and 7. Triplets are the commonest real
        duplicate: arXiv html + abs + a Hugging Face papers page."""
        svc = ResearchQualityService(site_config=SiteConfig())
        sources = [_make_scored_source(_DUP, s, f"https://s{int(s)}.com") for s in order]
        result = svc._deduplicate(sources)
        assert [s.url for s in result] == ["https://s9.com"]

    def test_single_source_returned_unchanged(self):
        svc = ResearchQualityService(site_config=SiteConfig())
        s = _make_scored_source("Single source snippet with enough words", 8.0)
        result = svc._deduplicate([s])
        assert len(result) == 1

    def test_empty_list_returned_unchanged(self):
        svc = ResearchQualityService(site_config=SiteConfig())
        assert svc._deduplicate([]) == []

    def test_three_distinct_sources_all_kept(self):
        svc = ResearchQualityService(site_config=SiteConfig())
        sources = [
            _make_scored_source("First source content is completely unique", 8.0, "https://a.com"),
            _make_scored_source(
                "Second source has totally different words here", 7.0, "https://b.com"
            ),
            _make_scored_source(
                "Third source contains other unrelated information", 9.0, "https://c.com"
            ),
        ]
        result = svc._deduplicate(sources)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# _score_uniqueness
# ---------------------------------------------------------------------------

# Twelve words each. A and B share their first six (similarity 0.5, under the
# 0.7 dedup threshold, so both survive); C shares nothing with either.
_HALF_A = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
_HALF_B = "alpha beta gamma delta epsilon zeta nu xi omicron pi rho sigma"
_DISTINCT = "one two three four five six seven eight nine ten eleven twelve"


class TestScoreUniqueness:
    def test_a_lone_source_is_fully_unique(self, service):
        (source,) = service.filter_and_score([make_result()])
        assert source.uniqueness_score == pytest.approx(1.0)

    def test_uniqueness_is_one_minus_the_closest_overlap(self, service):
        sources = service.filter_and_score([
            make_result(url="https://a.example.com/x", snippet=_HALF_A),
            make_result(url="https://b.example.com/x", snippet=_HALF_B),
            make_result(url="https://c.example.com/x", snippet=_DISTINCT),
        ])
        by_url = {s.url: s for s in sources}
        assert by_url["https://a.example.com/x"].uniqueness_score == pytest.approx(0.5)
        assert by_url["https://b.example.com/x"].uniqueness_score == pytest.approx(0.5)
        assert by_url["https://c.example.com/x"].uniqueness_score == pytest.approx(1.0)

    def test_the_uniqueness_weight_reorders_sources(self):
        """It was a constant (0.95 for every survivor, and never folded back
        into overall_score), so research_uniqueness_weight could not reorder
        anything. Now the source that adds something new outranks two that
        half-repeat each other, even listed last. The search-rank weight is
        off here so uniqueness is the only thing that differs."""
        svc = ResearchQualityService(
            site_config=SiteConfig(initial_config={"research_search_rank_weight": "0"})
        )
        sources = svc.filter_and_score([
            make_result(url="https://a.example.com/x", snippet=_HALF_A),
            make_result(url="https://b.example.com/x", snippet=_HALF_B),
            make_result(url="https://c.example.com/x", snippet=_DISTINCT),
        ])
        assert sources[0].url == "https://c.example.com/x"

    def test_zero_uniqueness_weight_keeps_input_order(self):
        svc = ResearchQualityService(
            site_config=SiteConfig(initial_config={
                "research_uniqueness_weight": "0", "research_search_rank_weight": "0",
            })
        )
        sources = svc.filter_and_score([
            make_result(url="https://a.example.com/x", snippet=_HALF_A),
            make_result(url="https://b.example.com/x", snippet=_HALF_B),
            make_result(url="https://c.example.com/x", snippet=_DISTINCT),
        ])
        assert [s.url for s in sources] == [
            "https://a.example.com/x", "https://b.example.com/x", "https://c.example.com/x",
        ]


# ---------------------------------------------------------------------------
# filter_and_score — integration
# ---------------------------------------------------------------------------

# One arXiv paper as DuckDuckGo returned it three times in a stored corpus.
_PAPER_SNIPPET = (
    "6 days ago · We prune attention heads after training and then recover the "
    "lost accuracy with a short distillation pass against the unpruned teacher "
    "model, measured on four benchmarks."
)


# Five unrelated snippets of 11-14 words: the same snippet-length band, no dates
# and no shared phrasing, so only domain and search position tell them apart.
_FIVE = (
    "Kubernetes operators reconcile desired state through control loops that watch cluster resources.",
    "Terraform modules package reusable infrastructure definitions that teams version across environments.",
    "Rust ownership rules prevent data races at compile time without a garbage collector.",
    "PostgreSQL vacuum reclaims dead tuples and keeps transaction identifiers from wrapping around.",
    "WebAssembly runs sandboxed bytecode in browsers at near-native speed for compute heavy code.",
)


def _five(hosts: list[str], snippets=_FIVE) -> list[dict]:
    return [make_result(url=f"https://{h}/x", snippet=s) for h, s in zip(hosts, snippets, strict=True)]


_NEUTRAL = ["a.example.com", "b.example.com", "c.example.com", "d.example.com", "e.example.com"]


class TestFilterAndScore:
    def test_empty_input_returns_empty(self, service):
        assert service.filter_and_score([]) == []

    def test_valid_results_returned(self, service):
        results = [
            make_result(url="https://example0.com/page", snippet=_HALF_A),
            make_result(url="https://example1.com/page", snippet=_DISTINCT),
        ]
        sources = service.filter_and_score(results)
        assert len(sources) == 2

    def test_thin_snippets_filtered_out(self, service):
        results = [
            make_result(url="https://t.me/examplechan", snippet="You can view and join @examplechan right away."),
            make_result(url="https://good.com/article"),
        ]
        sources = service.filter_and_score(results)
        assert [s.url for s in sources] == ["https://good.com/article"]

    def test_returned_objects_are_scored_sources(self, service):
        sources = service.filter_and_score([make_result()])
        assert sources
        for s in sources:
            assert isinstance(s, ScoredSource)

    def test_content_is_carried_through_for_the_writer(self, service):
        (source,) = service.filter_and_score(
            [make_result(content="Extracted page text with a number: 142.")]
        )
        assert source.content == "Extracted page text with a number: 142."

    def test_results_sorted_by_score_descending(self, service):
        results = [
            make_result(url=f"https://{host}/x", snippet=snippet)
            for host, snippet in zip(
                ["a.example.com", "cs.stanford.edu", "c.example.com", "d.example.com", "e.example.com"],
                _FIVE,
                strict=True,
            )
        ]
        scores = [s.overall_score for s in service.filter_and_score(results)]
        assert len(scores) == 5
        assert scores == sorted(scores, reverse=True)

    def test_near_duplicate_reduces_result_count(self, service):
        base_snippet = (
            "Python programming language is widely used for data science, "
            "machine learning, and web development frameworks such as Django and Flask. "
            "It is also popular for automation, scripting, and scientific computing tasks."
        )
        unique_snippet = (
            "JavaScript is the dominant language for front-end web development and "
            "is increasingly used on the server side via Node.js runtimes."
        )
        results = [
            make_result(url="https://site1.com/page", snippet=base_snippet),
            make_result(url="https://site2.com/page", snippet=base_snippet),
            make_result(url="https://site3.com/page", snippet=unique_snippet),
        ]
        sources = service.filter_and_score(results)
        assert len(sources) == 2

    def test_same_paper_from_three_hosts_keeps_the_most_credible(self, service):
        results = [
            make_result(url="https://huggingface.co/papers/2608.20953", snippet=_PAPER_SNIPPET),
            make_result(url="https://arxiv.org/html/2608.20953v1", snippet=_PAPER_SNIPPET),
            make_result(url="https://arxiv.org/abs/2608.20953v1", snippet=_PAPER_SNIPPET),
        ]
        sources = service.filter_and_score(results)
        assert [s.url for s in sources] == ["https://arxiv.org/html/2608.20953v1"]

    def test_a_fresh_source_outranks_an_old_one(self):
        """Recency used to read a Serper ``date`` key DuckDuckGo never sends,
        so research_recency_weight scaled a constant. Two sources equal in
        every other respect now order by the date their snippet leads with.
        The search-rank weight is off so recency is the only difference; with
        it on, a fresher #2 does not displace #1 (see TestSearchRank)."""
        service = ResearchQualityService(
            site_config=SiteConfig(initial_config={"research_search_rank_weight": "0"})
        )
        stale = make_result(
            url="https://a.example.com/x",
            snippet=(
                "Mar 1, 2019 - Terraform modules package reusable infrastructure "
                "definitions that teams version and share across environments."
            ),
        )
        fresh = make_result(
            url="https://b.example.com/x",
            snippet=(
                "2 days ago - Kubernetes operators reconcile desired state through "
                "control loops that watch cluster resources continuously."
            ),
        )
        sources = service.filter_and_score([stale, fresh])
        assert [s.url for s in sources] == ["https://b.example.com/x", "https://a.example.com/x"]
        assert sources[0].recency_score == pytest.approx(0.9)
        assert sources[1].recency_score == pytest.approx(0.6)

    def test_query_relevance_reaches_the_score(self, service):
        results = [make_result(snippet=_DISTINCT)]
        (plain,) = service.filter_and_score(results)
        (relevant,) = service.filter_and_score(results, query="twelve eleven")
        assert relevant.overall_score > plain.overall_score

    def test_overall_score_bounded_0_to_1(self, service):
        results = [make_result(url=f"https://test{i}.edu/page", snippet=s)
                   for i, s in enumerate([_HALF_A, _DISTINCT, _PAPER_SNIPPET])]
        sources = service.filter_and_score(results)
        assert len(sources) == 3
        assert all(0.0 <= s.overall_score <= 1.0 for s in sources)

    def test_logs_what_it_kept_and_why(self, service, caplog):
        results = [
            make_result(url="https://t.me/examplechan", snippet="You can view and join @examplechan right away."),
            make_result(url="https://arxiv.org/abs/2608.20953v1", snippet=_PAPER_SNIPPET),
            make_result(url="https://huggingface.co/papers/2608.20953", snippet=_PAPER_SNIPPET),
            make_result(url="https://c.example.com/x", snippet=_DISTINCT),
        ]
        with caplog.at_level("INFO"):
            service.filter_and_score(results)
        assert (
            "ResearchQualityService: Processed 4 results, kept 2 "
            "(1 thin snippet, 1 duplicate)"
        ) in caplog.text


# ---------------------------------------------------------------------------
# Search rank — DuckDuckGo's order is an input to the score
# ---------------------------------------------------------------------------


class TestSearchRank:
    """Without the search engine's order in the score, the other components
    (most of them weak) replaced DuckDuckGo's first result in 102 of 181
    replayed tiers, and in 13 of 19 tiers of a live check on 2026-09-28. That
    included anthropic.com, the primary source for its own post, dropping below
    an aggregator that sat at #5, on snippet length alone. At the default weight
    (0.4) a tier-1 host right behind can take the first slot and a tier-2 host
    alone cannot."""

    def test_rank_runs_from_one_to_zero_across_the_results(self, service):
        sources = service.filter_and_score(_five(_NEUTRAL))
        by_url = {s.url: s.search_rank_score for s in sources}
        assert [by_url[f"https://{h}/x"] for h in _NEUTRAL] == pytest.approx(
            [1.0, 0.75, 0.5, 0.25, 0.0]
        )

    def test_search_order_holds_when_nothing_else_differs(self, service):
        sources = service.filter_and_score(_five(_NEUTRAL))
        assert [s.url for s in sources] == [f"https://{h}/x" for h in _NEUTRAL]

    def test_weak_evidence_does_not_displace_the_first_result(self, service):
        fresher = list(_FIVE)
        fresher[1] = "2 days ago - " + fresher[1]
        sources = service.filter_and_score(_five(_NEUTRAL, fresher))
        assert sources[0].url == "https://a.example.com/x"
        assert sources[1].recency_score == pytest.approx(0.9)

    def test_a_more_credible_host_right_behind_takes_the_first_slot(self, service):
        hosts = ["a.example.com", "cs.stanford.edu", "c.example.com", "d.example.com", "e.example.com"]
        sources = service.filter_and_score(_five(hosts))
        assert [s.url for s in sources][:2] == [
            "https://cs.stanford.edu/x", "https://a.example.com/x",
        ]

    def test_a_curated_host_alone_does_not_displace_an_adjacent_result(self, service):
        """On 2026-09-28's run a GitHub page mirroring Hacker News (github.com
        was tier 2 then) sat right behind the Authors Guild's own post about its
        lawsuit, and at weight 0.3 it took that slot by 0.005. Any tier-2 host
        alone stays behind an adjacent result at the default weight."""
        hosts = ["a.example.com", "stackoverflow.com", "c.example.com", "d.example.com", "e.example.com"]
        sources = service.filter_and_score(_five(hosts))
        assert [s.url for s in sources][:2] == [
            "https://a.example.com/x", "https://stackoverflow.com/x",
        ]

    def test_credibility_does_not_leapfrog_far_down_the_list(self, service):
        hosts = ["a.example.com", "b.example.com", "c.example.com", "cs.stanford.edu", "e.example.com"]
        sources = service.filter_and_score(_five(hosts))
        assert [s.url.split("/")[2] for s in sources] == [
            "a.example.com", "b.example.com", "cs.stanford.edu", "c.example.com", "e.example.com",
        ]

    def test_the_primary_source_keeps_its_slot(self):
        """The live-check case: the primary source first with an ordinary
        snippet, an aggregator last with a long one."""
        long_snippet = (
            "Discovering cryptographic weaknesses with language models: an aggregated "
            "summary of the announcement, collected commentary, related links, the "
            "original post, several reactions and a short list of similar stories."
        )
        hosts = ["anthropic.com", "b.example.com", "c.example.com", "d.example.com", "neura.market"]
        snippets = [*_FIVE[:4], long_snippet]
        ranked = ResearchQualityService(site_config=SiteConfig()).filter_and_score(
            _five(hosts, snippets)
        )
        assert ranked[0].url == "https://anthropic.com/x"
        unranked = ResearchQualityService(
            site_config=SiteConfig(initial_config={"research_search_rank_weight": "0"})
        ).filter_and_score(_five(hosts, snippets))
        assert unranked[0].url == "https://neura.market/x"

    def test_a_kept_duplicate_takes_its_clusters_best_position(self, service):
        """DuckDuckGo ranked the paper first. The copy dedup keeps (arXiv,
        the more credible host) stands for it at #1, not at its own #2."""
        results = [
            make_result(url="https://huggingface.co/papers/2608.20953", snippet=_PAPER_SNIPPET),
            make_result(url="https://arxiv.org/abs/2608.20953v1", snippet=_PAPER_SNIPPET),
            *_five(["c.example.com", "d.example.com", "e.example.com"], _FIVE[:3]),
        ]
        sources = service.filter_and_score(results)
        assert sources[0].url == "https://arxiv.org/abs/2608.20953v1"
        assert sources[0].search_position == 0
        assert sources[0].search_rank_score == pytest.approx(1.0)

    def test_dedup_keeps_the_more_credible_copy_not_the_higher_ranked_one(self, service):
        """Rank is a placeholder while dedup picks a copy. Otherwise a listing
        ranked first would beat the publisher's own page for the same book."""
        blurb = (
            "A study of how a few large firms captured most of the gains from "
            "digital markets over four decades, and what that cost everyone else."
        )
        results = [
            make_result(url="https://www.amazon.com/dp/0000000000", snippet=blurb),
            make_result(url="https://a.example.com/x", snippet=_FIVE[0]),
            make_result(url="https://press.princeton.edu/books/example-title", snippet=blurb),
        ]
        urls = [s.url for s in service.filter_and_score(results)]
        assert "https://press.princeton.edu/books/example-title" in urls
        assert "https://www.amazon.com/dp/0000000000" not in urls

    def test_zero_rank_weight_ranks_on_quality_alone(self):
        svc = ResearchQualityService(
            site_config=SiteConfig(initial_config={"research_search_rank_weight": "0"})
        )
        hosts = ["a.example.com", "b.example.com", "c.example.com", "cs.stanford.edu", "e.example.com"]
        assert svc.filter_and_score(_five(hosts))[0].url == "https://cs.stanford.edu/x"


# ---------------------------------------------------------------------------
# Tunables are read at call time
# ---------------------------------------------------------------------------


class TestTunablesAreLive:
    @pytest.mark.asyncio
    async def test_a_reload_after_construction_takes_effect(self):
        """The service used to resolve its tunables once in __init__, so the
        cached AppContainer instance kept the values it was built with."""
        site_config = SiteConfig(initial_config={"research_min_snippet_words": "10"})
        svc = ResearchQualityService(site_config=site_config)
        assert svc._is_valid_result(make_result()) is True

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[{
            "key": "research_min_snippet_words", "value": "50",
            "deprecated": False, "superseded_by": None,
        }])
        await site_config.reload(pool)

        assert svc._is_valid_result(make_result()) is False


# ---------------------------------------------------------------------------
# Settings — code defaults, seeds and reads agree
# ---------------------------------------------------------------------------

# No lint can see a key built by _weight()'s f-string, so these tests are the
# only thing tying each seeded row to the read it is meant to back.
_WEIGHT_SETTINGS = {
    "research_credibility_weight": "credibility_weight",
    "research_snippet_quality_weight": "snippet_quality_weight",
    "research_recency_weight": "recency_weight",
    "research_uniqueness_weight": "uniqueness_weight",
    "research_search_rank_weight": "search_rank_weight",
}

# Literal-key reads (the phantom-read lint sees these), pinned here too so a
# fresh install without the row behaves exactly like one seeded from DEFAULTS.
_SCALAR_SETTINGS = {
    "research_min_snippet_length": "min_snippet_length",
    "research_min_snippet_words": "min_snippet_words",
    "research_dedup_similarity_threshold": "similarity_threshold",
    "research_recency_fresh_days": "recency_fresh_days",
    "research_recency_recent_days": "recency_recent_days",
}


class TestScoringWeightSettings:
    @pytest.mark.parametrize(("key", "attr"), _WEIGHT_SETTINGS.items())
    def test_unseeded_default_matches_the_seed(self, key, attr):
        svc = ResearchQualityService(site_config=SiteConfig())
        assert getattr(svc, attr) == float(DEFAULTS[key])

    @pytest.mark.parametrize(("key", "attr"), _WEIGHT_SETTINGS.items())
    def test_the_seeded_key_is_the_one_the_service_reads(self, key, attr):
        svc = ResearchQualityService(site_config=SiteConfig(initial_config={key: "0.77"}))
        assert getattr(svc, attr) == 0.77

    def test_overall_score_is_a_weighted_average(self):
        """An average, not a sum: adding the search-rank weight in 2026-09
        needed no change to the four seeded weights, which no longer have to
        sum to 1."""
        only_credibility = dict.fromkeys(_WEIGHT_SETTINGS, "0")
        only_credibility["research_credibility_weight"] = "5"
        svc = ResearchQualityService(site_config=SiteConfig(initial_config=only_credibility))
        (source,) = svc.filter_and_score([make_result(url="https://cs.stanford.edu/x")])
        assert source.overall_score == pytest.approx(0.95)

    def test_scores_stay_between_zero_and_one_for_any_weights(self):
        weights = dict(zip(_WEIGHT_SETTINGS, ["5", "3", "0", "2", "7"], strict=True))
        svc = ResearchQualityService(site_config=SiteConfig(initial_config=weights))
        sources = svc.filter_and_score(_five(_NEUTRAL))
        assert len(sources) == 5
        assert all(0.0 <= s.overall_score <= 1.0 for s in sources)

    def test_all_zero_weights_keep_search_order(self):
        """The escape hatch: filter and dedup, but no reordering."""
        svc = ResearchQualityService(
            site_config=SiteConfig(initial_config=dict.fromkeys(_WEIGHT_SETTINGS, "0"))
        )
        hosts = ["a.example.com", "cs.stanford.edu", "c.example.com", "d.example.com", "e.example.com"]
        sources = svc.filter_and_score(_five(hosts))
        assert [s.url for s in sources] == [f"https://{h}/x" for h in hosts]
        assert {s.overall_score for s in sources} == {0.0}


class TestScalarSettings:
    @pytest.mark.parametrize(("key", "attr"), _SCALAR_SETTINGS.items())
    def test_unseeded_default_matches_the_seed(self, key, attr):
        svc = ResearchQualityService(site_config=SiteConfig())
        assert getattr(svc, attr) == pytest.approx(float(DEFAULTS[key]))

    @pytest.mark.parametrize(("key", "attr"), _SCALAR_SETTINGS.items())
    def test_the_seeded_key_is_the_one_the_service_reads(self, key, attr):
        svc = ResearchQualityService(site_config=SiteConfig(initial_config={key: "77"}))
        assert getattr(svc, attr) == 77

    @pytest.mark.parametrize(
        ("key", "attr", "default"),
        [
            ("research_tier1_domains", "tier1_domains", "_DEFAULT_TIER_1_DOMAINS"),
            ("research_tier2_domains", "tier2_domains", "_DEFAULT_TIER_2_DOMAINS"),
        ],
    )
    def test_domain_tier_defaults_match_the_seed(self, key, attr, default):
        seeded = {d.strip() for d in DEFAULTS[key].split(",")}
        assert seeded == set(getattr(ResearchQualityService, default))
        assert getattr(ResearchQualityService(site_config=SiteConfig()), attr) == seeded
