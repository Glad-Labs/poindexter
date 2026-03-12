"""
Unit tests for ResearchQualityService.

All tests are pure-function — zero DB, LLM, or network calls.
Tests verify result filtering, domain credibility scoring, snippet quality scoring,
recency scoring, deduplication, uniqueness recalculation, and context formatting.
"""

import pytest

from services.research_quality_service import ResearchQualityService, ScoredSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_result(
    link: str = "https://example.com/article",
    snippet: str = "This is a sufficiently long snippet with at least ten words in it yes.",
    title: str = "Test Title",
    result_type: str | None = None,
    date: str | None = None,
) -> dict:
    r = {"title": title, "link": link, "snippet": snippet}
    if result_type:
        r["type"] = result_type
    if date:
        r["date"] = date
    return r


@pytest.fixture()
def service() -> ResearchQualityService:
    return ResearchQualityService()


# ---------------------------------------------------------------------------
# ScoredSource.to_context_string
# ---------------------------------------------------------------------------


class TestScoredSourceContextString:
    def _make_source(self) -> ScoredSource:
        return ScoredSource(
            title="Python Guide",
            url="https://python.org/guide",
            snippet="Learn Python step by step.",
            domain="python.org",
            domain_credibility=0.95,
            snippet_quality=0.8,
            recency_score=0.7,
            uniqueness_score=0.95,
            overall_score=0.88,
        )

    def test_title_in_output(self):
        assert "Python Guide" in self._make_source().to_context_string()

    def test_url_in_output(self):
        assert "https://python.org/guide" in self._make_source().to_context_string()

    def test_snippet_in_output(self):
        assert "Learn Python step by step" in self._make_source().to_context_string()

    def test_credibility_formatted_as_percent(self):
        s = self._make_source()
        ctx = s.to_context_string()
        assert "95%" in ctx


# ---------------------------------------------------------------------------
# _is_valid_result
# ---------------------------------------------------------------------------


class TestIsValidResult:
    def test_valid_result_passes(self, service):
        r = make_result()
        assert service._is_valid_result(r) is True

    def test_featured_snippet_rejected(self, service):
        r = make_result(result_type="featured_snippet")
        assert service._is_valid_result(r) is False

    def test_missing_link_rejected(self, service):
        r = {"title": "T", "snippet": "long enough snippet yes indeed ten words here"}
        assert service._is_valid_result(r) is False

    def test_missing_snippet_rejected(self, service):
        r = {"title": "T", "link": "https://example.com"}
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

    def test_org_domain_tier1(self, service):
        assert service._score_domain_credibility("wikipedia.org") == pytest.approx(0.95)

    def test_github_com_tier2(self, service):
        assert service._score_domain_credibility("github.com") == pytest.approx(0.85)

    def test_medium_com_tier2(self, service):
        assert service._score_domain_credibility("medium.com") == pytest.approx(0.85)

    def test_techcrunch_common(self, service):
        assert service._score_domain_credibility("techcrunch.com") == pytest.approx(0.8)

    def test_bloomberg_common(self, service):
        assert service._score_domain_credibility("bloomberg.com") == pytest.approx(0.8)

    def test_dot_com_fallback(self, service):
        score = service._score_domain_credibility("randomsite.com")
        assert score == pytest.approx(0.65)

    def test_dot_io_fallback(self, service):
        score = service._score_domain_credibility("startup.io")
        assert score == pytest.approx(0.65)

    def test_unknown_extension_fallback(self, service):
        score = service._score_domain_credibility("site.xyz")
        assert score == pytest.approx(0.5)

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
        assert with_query >= without_query

    def test_spam_keyword_reduces_score(self, service):
        clean = "This is a well-written informative snippet about software engineering topics."
        spammy = clean + " Click here to buy now limited time!"
        assert service._score_snippet_quality(clean) > service._score_snippet_quality(spammy)

    def test_score_bounded_0_to_1(self, service):
        for snippet in ["", "a", "word " * 100]:
            score = service._score_snippet_quality(snippet)
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# _score_recency
# ---------------------------------------------------------------------------


class TestScoreRecency:
    def test_no_date_returns_neutral(self, service):
        assert service._score_recency({}) == pytest.approx(0.7)

    def test_hours_ago_is_high(self, service):
        assert service._score_recency({"date": "2 hours ago"}) == pytest.approx(0.9)

    def test_days_ago_is_high(self, service):
        assert service._score_recency({"date": "3 days ago"}) == pytest.approx(0.9)

    def test_weeks_ago_is_good(self, service):
        assert service._score_recency({"date": "2 weeks ago"}) == pytest.approx(0.8)

    def test_months_ago_is_okay(self, service):
        assert service._score_recency({"date": "4 months ago"}) == pytest.approx(0.8)

    def test_old_date_returns_lower(self, service):
        assert service._score_recency({"date": "Jan 2020"}) == pytest.approx(0.6)


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
        slightly_modified = "Python is a great programming language for data science and automating tasks."
        score = service._calculate_similarity(base, slightly_modified)
        assert score > service.SIMILARITY_THRESHOLD


# ---------------------------------------------------------------------------
# filter_and_score — integration
# ---------------------------------------------------------------------------


class TestFilterAndScore:
    def test_empty_input_returns_empty(self, service):
        assert service.filter_and_score([]) == []

    def test_valid_results_returned(self, service):
        results = [make_result(link=f"https://example{i}.com/page") for i in range(3)]
        sources = service.filter_and_score(results)
        assert len(sources) > 0

    def test_featured_snippets_filtered_out(self, service):
        results = [
            make_result(result_type="featured_snippet"),
            make_result(link="https://good.com/article"),
        ]
        sources = service.filter_and_score(results)
        # Only the non-featured snippet should pass
        assert len(sources) == 1

    def test_returned_objects_are_scored_sources(self, service):
        results = [make_result()]
        sources = service.filter_and_score(results)
        for s in sources:
            assert isinstance(s, ScoredSource)

    def test_results_sorted_by_score_descending(self, service):
        results = [
            # High credibility source
            make_result(link="https://stanford.edu/research", title="Stanford Research"),
            # Low credibility source
            make_result(link="https://randomsite.xyz/post", title="Random Post"),
        ]
        sources = service.filter_and_score(results)
        if len(sources) >= 2:
            assert sources[0].overall_score >= sources[1].overall_score

    def test_uniqueness_score_set_after_dedup(self, service):
        results = [make_result(link=f"https://site{i}.com/article") for i in range(2)]
        sources = service.filter_and_score(results)
        for s in sources:
            assert s.uniqueness_score == pytest.approx(0.95)

    def test_near_duplicate_reduces_result_count(self, service):
        # Snippet must be >= 50 chars AND >= 10 words to pass _is_valid_result
        base_snippet = (
            "Python programming language is widely used for data science, "
            "machine learning, and web development frameworks such as Django and Flask. "
            "It is also popular for automation, scripting, and scientific computing tasks."
        )
        # 3 results: 2 identical (duplicates) + 1 unique
        unique_snippet = (
            "JavaScript is the dominant language for front-end web development and "
            "is increasingly used on the server side via Node.js runtimes."
        )
        results = [
            make_result(link="https://site1.com/page", snippet=base_snippet),
            make_result(link="https://site2.com/page", snippet=base_snippet),
            make_result(link="https://site3.com/page", snippet=unique_snippet),
        ]
        sources = service.filter_and_score(results)
        # 3 inputs → dedup should produce fewer than 3 (the identical pair is condensed)
        assert len(sources) < 3

    def test_overall_score_bounded_0_to_1(self, service):
        results = [make_result(link=f"https://test{i}.edu/page") for i in range(5)]
        sources = service.filter_and_score(results)
        for s in sources:
            assert 0.0 <= s.overall_score <= 1.0


# ---------------------------------------------------------------------------
# format_context
# ---------------------------------------------------------------------------


class TestFormatContext:
    def _make_source(self, n: int = 1) -> list[ScoredSource]:
        return [
            ScoredSource(
                title=f"Source {i}",
                url=f"https://site{i}.com",
                snippet=f"Snippet for source {i}.",
                domain=f"site{i}.com",
                domain_credibility=0.8,
                snippet_quality=0.7,
                recency_score=0.7,
                uniqueness_score=0.95,
                overall_score=0.8,
            )
            for i in range(1, n + 1)
        ]

    def test_empty_sources_returns_fallback_message(self, service):
        result = service.format_context([])
        assert "No research sources" in result

    def test_source_count_in_header(self, service):
        sources = self._make_source(3)
        result = service.format_context(sources)
        assert "3" in result

    def test_each_source_url_present(self, service):
        sources = self._make_source(2)
        result = service.format_context(sources)
        assert "https://site1.com" in result
        assert "https://site2.com" in result

    def test_each_source_snippet_present(self, service):
        sources = self._make_source(2)
        result = service.format_context(sources)
        assert "Snippet for source 1" in result
        assert "Snippet for source 2" in result

    def test_numbered_entries(self, service):
        sources = self._make_source(3)
        result = service.format_context(sources)
        assert "1." in result
        assert "2." in result
        assert "3." in result


# ---------------------------------------------------------------------------
# _deduplicate — regression tests for bug fix (equal-score drops both sources)
# ---------------------------------------------------------------------------


def _make_scored_source(snippet: str, score: float, url: str = "https://example.com") -> ScoredSource:
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


class TestDeduplicate:
    """Regression tests for _deduplicate — verifies the bug fix where equal-score
    duplicates dropped both sources instead of keeping the winner."""

    def test_no_duplicates_keeps_all(self):
        svc = ResearchQualityService()
        a = _make_scored_source("First unique snippet with enough words", 8.0, "https://a.com")
        b = _make_scored_source("Second completely different text here", 7.0, "https://b.com")
        result = svc._deduplicate([a, b])
        assert len(result) == 2

    def test_higher_score_winner_is_kept(self):
        """When source_a wins (>= score), it must appear in output — was the bug."""
        svc = ResearchQualityService()
        # Use identical snippets to guarantee similarity >= SIMILARITY_THRESHOLD
        snippet = "identical content repeated to ensure high similarity score yes"
        winner = _make_scored_source(snippet, 9.0, "https://winner.com")
        loser = _make_scored_source(snippet, 6.0, "https://loser.com")
        result = svc._deduplicate([winner, loser])
        # Exactly one source must be kept — the winner
        assert len(result) == 1
        assert result[0].url == "https://winner.com"

    def test_equal_score_keeps_one_source(self):
        """Equal-score duplicates — old bug dropped both; fix keeps exactly one."""
        svc = ResearchQualityService()
        snippet = "identical content repeated to ensure high similarity score yes"
        s1 = _make_scored_source(snippet, 7.5, "https://s1.com")
        s2 = _make_scored_source(snippet, 7.5, "https://s2.com")
        result = svc._deduplicate([s1, s2])
        # Must keep exactly one (whichever — s1 wins the >= comparison)
        assert len(result) == 1

    def test_single_source_returned_unchanged(self):
        svc = ResearchQualityService()
        s = _make_scored_source("Single source snippet with enough words", 8.0)
        result = svc._deduplicate([s])
        assert len(result) == 1

    def test_empty_list_returned_unchanged(self):
        svc = ResearchQualityService()
        assert svc._deduplicate([]) == []

    def test_three_distinct_sources_all_kept(self):
        svc = ResearchQualityService()
        sources = [
            _make_scored_source("First source content is completely unique", 8.0, "https://a.com"),
            _make_scored_source("Second source has totally different words here", 7.0, "https://b.com"),
            _make_scored_source("Third source contains other unrelated information", 9.0, "https://c.com"),
        ]
        result = svc._deduplicate(sources)
        assert len(result) == 3
