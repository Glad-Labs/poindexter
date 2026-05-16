"""
Unit tests for services/content_validator.py

Tests the programmatic quality gate: fake names, stats, impossible claims,
fabricated quotes, and company fact validation.
"""

import pytest

from services.content_validator import (
    GLAD_LABS_FACTS,
    ValidationResult,
    validate_content,
)


class TestValidateContentClean:
    """Content that should pass validation."""

    def test_clean_content_passes(self):
        result = validate_content(
            "How to Use FastAPI",
            "FastAPI is a modern web framework for building APIs with Python. "
            "It provides automatic documentation and type checking.",
            "FastAPI",
        )
        assert result.passed is True
        assert result.critical_count == 0

    def test_long_technical_content(self):
        content = (
            "Docker containers provide isolated environments for applications. "
            "When you run docker build, the Dockerfile instructions create layers. "
            "Each layer is cached, making subsequent builds faster. "
            "The best practice is to use multi-stage builds to reduce image size. "
        ) * 10  # ~400 words
        result = validate_content("Docker Best Practices", content, "Docker")
        assert result.passed is True


class TestFakeNames:
    """Detect fabricated people cited as authorities."""

    def test_catches_fake_ceo(self):
        content = "Sarah Johnson, CEO at some company, said this was transformative."
        result = validate_content("AI Trends", content, "AI")
        assert any("fabricated" in i.description.lower() or "name" in i.description.lower()
                    for i in result.issues)

    def test_catches_fake_doctor(self):
        content = "Dr. Smith Williams published a groundbreaking study on AI."
        result = validate_content("AI Research", content, "AI")
        assert any("name" in i.description.lower() or "fabricated" in i.description.lower()
                    for i in result.issues)


class TestFakeStatistics:
    """Detect hallucinated statistics and studies."""

    def test_catches_fake_percentage(self):
        content = "Studies show a 47% reduction in deployment time when using Docker."
        result = validate_content("Docker", content, "Docker")
        has_stat_warning = any("statistic" in i.description.lower() or "percentage" in i.description.lower()
                              for i in result.issues)
        # This should flag as a potential hallucinated stat
        assert has_stat_warning or result.warning_count > 0

    def test_catches_fake_research_firm(self):
        content = "According to a 2024 study by McKinsey, AI adoption has increased 300%."
        result = validate_content("AI Adoption", content, "AI")
        assert any("statistic" in i.description.lower() or "study" in i.description.lower() or "citation" in i.description.lower()
                    for i in result.issues)


class TestCompanyFactValidation:
    """Detect impossible claims about the company."""

    def test_facts_are_configurable(self):
        """Company facts should come from config, not be hardcoded."""
        assert "company_name" in GLAD_LABS_FACTS
        assert "founded_year" in GLAD_LABS_FACTS
        assert "team_size" in GLAD_LABS_FACTS

    def test_catches_impossible_age_claim(self):
        company = GLAD_LABS_FACTS["company_name"]
        content = f"{company} has been operating for over 10 years in the AI space."
        result = validate_content("About Us", content, "company")
        assert any("claim" in i.description.lower() or "years" in i.description.lower()
                    for i in result.issues)


class TestFabricatedQuotes:
    """Detect made-up quotes attributed to people."""

    def test_catches_attributed_quote(self):
        content = '"This changes everything for our industry," says Marcus Chen, VP of Engineering.'
        result = validate_content("Industry News", content, "tech")
        assert any("quote" in i.description.lower() or "fabricated" in i.description.lower() or "name" in i.description.lower()
                    for i in result.issues)


class TestImagePlaceholders:
    """Detect LLM image placeholder artifacts left in content."""

    def test_catches_image_placeholder(self):
        content = "Here is some text.\n[IMAGE-1: A futuristic cityscape with AI robots]\nMore text follows."
        result = validate_content("AI Future", content, "AI")
        assert not result.passed
        assert any("placeholder" in i.description.lower() for i in result.issues)
        assert any(i.category == "image_placeholder" for i in result.issues)

    def test_catches_figure_placeholder(self):
        content = "The architecture is shown below.\n[FIGURE: System architecture diagram]\nAs you can see..."
        result = validate_content("Architecture", content, "tech")
        assert any(i.category == "image_placeholder" for i in result.issues)

    def test_catches_diagram_placeholder(self):
        content = "[DIAGRAM: Flow chart showing the data pipeline]"
        result = validate_content("Pipeline", content, "tech")
        assert any(i.category == "image_placeholder" for i in result.issues)

    def test_catches_screenshot_placeholder(self):
        content = "[SCREENSHOT: Dashboard showing metrics]"
        result = validate_content("Metrics", content, "tech")
        assert any(i.category == "image_placeholder" for i in result.issues)

    def test_no_false_positive_on_markdown_links(self):
        content = "Check out [this article](https://example.com) for more details."
        result = validate_content("Links", content, "tech")
        assert not any(i.category == "image_placeholder" for i in result.issues)

    def test_image_placeholder_is_critical(self):
        content = "[IMAGE-1: A beautiful sunset over the ocean]"
        result = validate_content("Sunset", content, "nature")
        image_issues = [i for i in result.issues if i.category == "image_placeholder"]
        assert all(i.severity == "critical" for i in image_issues)


class TestUnresolvedPlaceholders:
    """poindexter#489 — catch [posts/...] template tokens leaking to publish."""

    def test_catches_curly_brace_template(self):
        content = "Related reading: [posts/{slug}] on this topic."
        result = validate_content("Title", content, "tech")
        assert not result.passed
        assert any(i.category == "unresolved_placeholder" for i in result.issues)

    def test_catches_bare_uuid_reference(self):
        content = "Related: [posts/a1b2c3d4-e5f6-7890-abcd-1234567890ef] explains more."
        result = validate_content("Title", content, "tech")
        assert not result.passed
        assert any(i.category == "unresolved_placeholder" for i in result.issues)

    def test_catches_bare_slug_reference(self):
        content = "More on this in [posts/llm-workforce-thesis]."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "unresolved_placeholder" for i in result.issues)

    def test_catches_post_id_variant(self):
        content = "Cross-reference: [POST_ID: 12345] for details."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "unresolved_placeholder" for i in result.issues)

    def test_does_not_fire_on_resolved_markdown_link(self):
        # Real Markdown link: bracket immediately followed by paren.
        content = "Read [posts/llm-workforce-thesis](/posts/llm-workforce-thesis) for more."
        result = validate_content("Title", content, "tech")
        assert not any(i.category == "unresolved_placeholder" for i in result.issues)

    def test_does_not_fire_on_prose_brackets(self):
        # A real sentence with brackets but no posts/ path.
        content = "The system [logs everything] for audit purposes."
        result = validate_content("Title", content, "tech")
        assert not any(i.category == "unresolved_placeholder" for i in result.issues)

    def test_unresolved_placeholder_is_critical(self):
        content = "See [posts/llm-workforce-thesis] for context."
        result = validate_content("Title", content, "tech")
        ph_issues = [i for i in result.issues if i.category == "unresolved_placeholder"]
        assert ph_issues, "expected at least one unresolved_placeholder issue"
        assert all(i.severity == "critical" for i in ph_issues)


class TestLeakedImagePrompts:
    """Detect leaked image generation prompts in content."""

    def test_catches_italic_image_prompt(self):
        content = "Here is the header image.\n*A split-screen comparison showing old vs new architecture with dramatic lighting*"
        result = validate_content("Architecture", content, "tech")
        assert any(i.category == "leaked_image_prompt" for i in result.issues)

    def test_no_false_positive_on_short_italic(self):
        content = "This is *important* text."
        result = validate_content("Test", content, "test")
        assert not any(i.category == "leaked_image_prompt" for i in result.issues)


class TestValidationResult:
    """Test the ValidationResult data structure."""

    def test_passed_when_no_critical(self):
        result = validate_content("Clean Title", "This is clean content with no issues.", "topic")
        assert result.passed is True
        assert isinstance(result.issues, list)

    def test_score_penalty_calculation(self):
        result = validate_content("Clean Title", "Clean content.", "topic")
        assert result.score_penalty >= 0
        assert result.score_penalty <= 100

    def test_critical_count_property(self):
        result = validate_content("Clean", "[IMAGE-1: a photo]", "topic")
        assert result.critical_count >= 1

    def test_warning_count_property(self):
        # Use a late_acronym_expansion example since fabrication
        # categories were promoted to critical on 2026-04-11.
        content = (
            "We rely on AWS for hosting. AWS provides everything. "
            "AWS makes deployment easy. AWS (Amazon Web Services) keeps "
            "adding services."
        )
        result = validate_content("Clean", content, "topic")
        assert result.warning_count >= 1

    def test_score_penalty_critical_is_10(self):
        result = validate_content("Clean", "[IMAGE-1: photo]", "topic")
        # 1 critical → at least 10 penalty
        assert result.score_penalty >= 10

    def test_score_penalty_warning_is_3(self):
        """A single warning issue contributes 3 to score_penalty."""
        content = (
            "We rely on AWS for hosting. AWS provides everything. "
            "AWS makes deployment easy. AWS (Amazon Web Services) keeps "
            "adding services."
        )
        result = validate_content("Clean", content, "topic")
        if result.warning_count > 0 and result.critical_count == 0:
            # Penalty should be a multiple of 3
            assert result.score_penalty % 3 == 0


# ---------------------------------------------------------------------------
# Hallucinated internal links
# ---------------------------------------------------------------------------


class TestHallucinatedLinks:
    def test_catches_our_guide_reference(self):
        content = "For more details, check out our guide on building scalable systems."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "hallucinated_link" for i in result.issues)

    def test_catches_as_we_discussed_in_previous(self):
        content = "As we discussed in a previous post, this approach has tradeoffs."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "hallucinated_link" for i in result.issues)

    def test_catches_check_out_our_post(self):
        content = "Check out our post on Docker best practices for more info."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "hallucinated_link" for i in result.issues)

    def test_hallucinated_link_is_critical(self):
        # Promoted to critical on 2026-04-11 per Matt's "a fabrication
        # is a fail" direction — a link that looks valid but leads
        # nowhere is functionally a lie to the reader.
        content = "Read our guide on optimization techniques."
        result = validate_content("Title", content, "tech")
        link_issues = [i for i in result.issues if i.category == "hallucinated_link"]
        assert link_issues, "expected a hallucinated_link issue"
        assert all(i.severity == "critical" for i in link_issues)


# ---------------------------------------------------------------------------
# Brand contradictions
# ---------------------------------------------------------------------------


class TestBrandContradictions:
    def test_catches_openai_pricing_reference(self):
        content = "Compared to OpenAI API pricing, the local model is cheaper."
        result = validate_content("Title", content, "AI")
        assert any(i.category == "brand_contradiction" for i in result.issues)

    def test_catches_anthropic_subscription(self):
        content = "After paying for the Anthropic API subscription, costs added up fast."
        result = validate_content("Title", content, "AI")
        assert any(i.category == "brand_contradiction" for i in result.issues)

    def test_catches_paying_per_token(self):
        content = "Instead of paying per token to OpenAI, we run everything locally."
        result = validate_content("Title", content, "AI")
        assert any(i.category == "brand_contradiction" for i in result.issues)

    def test_brand_contradiction_is_warning(self):
        content = "OpenAI API costs eat into margins."
        result = validate_content("Title", content, "AI")
        contradictions = [i for i in result.issues if i.category == "brand_contradiction"]
        assert all(i.severity == "warning" for i in contradictions)


# ---------------------------------------------------------------------------
# Fabricated personal experiences
# ---------------------------------------------------------------------------


class TestFabricatedExperiences:
    def test_catches_sat_in_meeting(self):
        content = "Last week I sat in a meeting with a startup founder who needed help."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "fabricated_experience" for i in result.issues)

    def test_catches_at_my_company(self):
        content = "At my current company, we use this exact pattern."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "fabricated_experience" for i in result.issues)

    def test_catches_client_of_mine(self):
        content = "A client of mine asked about this issue last week."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "fabricated_experience" for i in result.issues)

    def test_catches_recent_anecdote(self):
        content = "Last month we deployed this to production and it worked."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "fabricated_experience" for i in result.issues)

    def test_catches_dollar_amount_anecdote(self):
        content = "It saved us $1,200/month in compute costs."
        result = validate_content("Title", content, "tech")
        assert any(i.category == "fabricated_experience" for i in result.issues)

    def test_catches_fabricated_dialogue(self):
        content = '"This is a great approach for our use case," he said with a smile.'
        result = validate_content("Title", content, "tech")
        assert any(i.category == "fabricated_experience" for i in result.issues)


# ---------------------------------------------------------------------------
# Title-based age/year claims
# ---------------------------------------------------------------------------


class TestTitleYearClaims:
    def test_catches_numeric_year_in_title(self):
        result = validate_content(
            "What I Learned in 5 Years of Building AI Systems",
            "Some content here.",
            "AI",
        )
        # Critical because company is < 1 year old
        assert any(
            i.category == "glad_labs_claim" and "year" in i.description.lower()
            for i in result.issues
        )

    def test_catches_written_year_in_title(self):
        result = validate_content(
            "Three Years of Lessons from Production",
            "Some content here.",
            "tech",
        )
        assert any(
            i.category == "glad_labs_claim" and "year" in i.description.lower()
            for i in result.issues
        )

    def test_does_not_flag_one_year(self):
        result = validate_content("After 1 Year of Building", "content", "tech")
        # Singular year is valid since company is ~1 year old
        year_claims = [
            i for i in result.issues
            if i.category == "glad_labs_claim" and "year" in i.description.lower()
        ]
        assert len(year_claims) == 0

    def test_numeric_year_claim_is_critical(self):
        result = validate_content("10 Years of Wisdom", "content", "tech")
        year_issues = [
            i for i in result.issues
            if i.category == "glad_labs_claim" and "year" in i.description.lower()
        ]
        assert all(i.severity == "critical" for i in year_issues)


# ---------------------------------------------------------------------------
# Late acronym expansion
# ---------------------------------------------------------------------------


class TestLateAcronymExpansion:
    def test_catches_late_expansion(self):
        content = (
            "We use a CRM for tracking leads. The CRM integrates with our other tools. "
            "Our CRM (Customer Relationship Management) is essential to operations."
        )
        result = validate_content("Title", content, "tech")
        assert any(i.category == "late_acronym_expansion" for i in result.issues)

    def test_does_not_flag_first_use_expansion(self):
        content = (
            "Our CRM (Customer Relationship Management) is essential. The CRM tracks leads."
        )
        result = validate_content("Title", content, "tech")
        # Expansion was on first use, not flagged
        assert not any(i.category == "late_acronym_expansion" for i in result.issues)

    def test_late_expansion_is_warning(self):
        content = (
            "We rely on AWS for hosting. AWS provides everything. AWS makes deployment easy. "
            "AWS (Amazon Web Services) keeps adding services."
        )
        result = validate_content("Title", content, "tech")
        late_issues = [i for i in result.issues if i.category == "late_acronym_expansion"]
        # If matched, should be warning severity
        for i in late_issues:
            assert i.severity == "warning"


# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_removes_simple_tags(self):
        from services.content_validator import _strip_html
        assert _strip_html("<p>Hello</p>") == "Hello"

    def test_removes_nested_tags(self):
        from services.content_validator import _strip_html
        result = _strip_html("<div><p>nested <span>text</span></p></div>")
        assert "<" not in result
        assert ">" not in result

    def test_removes_attributes(self):
        from services.content_validator import _strip_html
        assert _strip_html('<a href="https://x.com">link</a>') == "link"

    def test_plain_text_unchanged(self):
        from services.content_validator import _strip_html
        assert _strip_html("plain text no tags") == "plain text no tags"


# ---------------------------------------------------------------------------
# Edge cases: empty/None inputs
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_title_and_content(self):
        result = validate_content("", "", "")
        assert result.passed is True
        assert result.issues == []

    def test_none_title_treated_as_empty(self):
        result = validate_content(None, "content here", "topic")  # type: ignore[arg-type]
        assert isinstance(result, ValidationResult)

    def test_none_content_treated_as_empty(self):
        result = validate_content("title", None, "topic")  # type: ignore[arg-type]
        assert isinstance(result, ValidationResult)
        assert result.passed is True

    def test_html_stripped_before_pattern_matching(self):
        """HTML tags should not interfere with pattern matching."""
        content = "<p>[IMAGE-1: a photo]</p>"
        result = validate_content("Title", content, "topic")
        assert any(i.category == "image_placeholder" for i in result.issues)

    def test_line_numbers_recorded_in_issues(self):
        """Issues should have a line_number > 0 when matched."""
        content = "First line.\nSecond line.\n[IMAGE-1: third line photo]"
        result = validate_content("Title", content, "topic")
        image_issues = [i for i in result.issues if i.category == "image_placeholder"]
        assert all(i.line_number > 0 for i in image_issues)

    def test_matched_text_truncated_to_100_chars(self):
        long_match = "[IMAGE-1: " + "x" * 200 + "]"
        result = validate_content("Title", long_match, "topic")
        image_issues = [i for i in result.issues if i.category == "image_placeholder"]
        for i in image_issues:
            assert len(i.matched_text) <= 100


class TestBannedHeaders:
    """Structural warning when posts use generic section titles. The prompt
    already tells the LLM not to, but some models ignore it — this is a
    backstop that penalizes the score (warning, not critical)."""

    def test_conclusion_header_is_warning(self):
        content = "Some intro paragraph.\n\n## Conclusion\n\nwrap up.\n"
        result = validate_content("Title", content, "topic")
        banned = [i for i in result.issues if i.category == "banned_header"]
        assert banned, "Expected banned_header issue for ## Conclusion"
        assert banned[0].severity == "warning"
        assert result.passed is True  # warning does not fail content

    def test_introduction_header_is_warning(self):
        content = "First line.\n\n## Introduction\n\nhi there.\n"
        result = validate_content("Title", content, "topic")
        assert any(i.category == "banned_header" for i in result.issues)

    def test_summary_header_is_warning(self):
        content = "Some content.\n\n## Summary\n\nhere it is.\n"
        result = validate_content("Title", content, "topic")
        assert any(i.category == "banned_header" for i in result.issues)

    def test_creative_header_is_ok(self):
        content = "Content.\n\n## Why Most Developers Get This Wrong\n\nBody.\n"
        result = validate_content("Title", content, "topic")
        assert not any(i.category == "banned_header" for i in result.issues)

    def test_conclusion_case_insensitive(self):
        content = "Body.\n\n### CONCLUSION\n\nwrap.\n"
        result = validate_content("Title", content, "topic")
        assert any(i.category == "banned_header" for i in result.issues)

    def test_trailing_colon_still_caught(self):
        content = "Body.\n\n## Conclusion:\n\nwrap.\n"
        result = validate_content("Title", content, "topic")
        assert any(i.category == "banned_header" for i in result.issues)


class TestFillerIntros:
    """'In this post/article' and 'In today's fast-paced' are common LLM
    crutches. Warning-level, same policy as banned headers."""

    def test_in_this_post_intro_flagged(self):
        content = "In this post, we will explore how to build a thing.\n\n## Body\n\ncontent.\n"
        result = validate_content("Title", content, "topic")
        assert any(i.category == "filler_intro" for i in result.issues)

    def test_in_this_article_intro_flagged(self):
        content = "In this article we will cover the steps.\n\n## Section\n\ncontent.\n"
        result = validate_content("Title", content, "topic")
        assert any(i.category == "filler_intro" for i in result.issues)

    def test_in_todays_fast_paced_flagged(self):
        content = "In today's fast-paced digital world, things move quickly.\n\n## Body\n\nx.\n"
        result = validate_content("Title", content, "topic")
        assert any(i.category == "filler_intro" for i in result.issues)

    def test_strong_hook_is_fine(self):
        content = "PostgreSQL will crumble under load if you ignore connection pooling.\n\n## Body\n\nx.\n"
        result = validate_content("Title", content, "topic")
        assert not any(i.category == "filler_intro" for i in result.issues)

    def test_only_checks_first_500_chars(self):
        """Filler phrases deep in the body should NOT trigger (intro-only check)."""
        content = "Strong hook about concrete problem. " + ("Body text. " * 60) + "In this post, deep content.\n"
        result = validate_content("Title", content, "topic")
        assert not any(i.category == "filler_intro" for i in result.issues)


# ============================================================================
# Banned-transition openers (poindexter#232)
# ============================================================================


class TestBannedTransitionOpeners:
    """Stock-LLM transition words at sentence start are linguistic tells.

    Rule fires once when the post crosses the threshold (default >2 — i.e.
    three or more occurrences). Below the threshold is allowed because one
    or two of these openers is normal English.
    """

    def test_over_threshold_emits_one_warning(self):
        content = (
            "Caching changes the math of expensive queries. "
            "Furthermore, it reduces tail latency for cold reads. "
            "Moreover, it shrinks the blast radius of a stuck upstream. "
            "Additionally, the operator can tune the TTL per route. "
            "In conclusion, the trade-off lands on the side of caching.\n"
        )
        result = validate_content("Caching post", content, "infra")
        warnings = [i for i in result.issues if i.category == "banned_transition_opener"]
        assert len(warnings) == 1
        assert "4" in warnings[0].description or "4×" in warnings[0].description

    def test_under_threshold_is_silent(self):
        """Two or fewer banned openers stay below the threshold."""
        content = (
            "Caching is a real lever. "
            "Furthermore, it reduces tail latency for cold reads. "
            "Moreover, it shrinks the blast radius of a stuck upstream.\n"
        )
        result = validate_content("Caching post", content, "infra")
        assert not any(i.category == "banned_transition_opener" for i in result.issues)

    def test_midsentence_match_is_ignored(self):
        """A banned word inside a sentence (not at sentence start) is fine."""
        content = (
            "There is, furthermore, no obvious win in moreover-style writing. "
            "The team would, additionally, prefer concrete examples. "
            "Notably-named patterns aside, prose carries the post.\n"
        )
        result = validate_content("Style post", content, "writing")
        assert not any(i.category == "banned_transition_opener" for i in result.issues)

    def test_clean_post_passes(self):
        content = (
            "Postgres connection pooling deserves a real budget. "
            "Without it, a burst of cold-cache reads will queue behind every "
            "long write. PgBouncer in transaction mode is the default we reach for.\n"
        )
        result = validate_content("Pooling", content, "infra")
        assert not any(i.category == "banned_transition_opener" for i in result.issues)


# ============================================================================
# Known-Wrong Hardware Facts (added 2026-04-11, Gitea #192)
# ============================================================================


class TestKnownWrongHardwareFacts:
    """Regression tests for the RTX 5090 24GB VRAM false claim.

    The writer (gemma3:27b) has a training cutoff before the 5090 launch
    and "remembers" it as having 24GB VRAM. The actual spec is 32GB.
    This validator catches that specific false claim as a CRITICAL issue.

    Patterns are now loaded from the fact_overrides DB table at runtime.
    Tests mock _load_fact_overrides_sync to inject the patterns without a DB.
    """

    # The same patterns that are seeded in the fact_overrides table
    _TEST_OVERRIDES = [
        (
            r"(?:RTX\s*)?5090[^.]{0,80}?(?:24\s*GB|24GB|24\s*gigabytes)(?!\s*(?:/|per|plus))",
            "RTX 5090 has 32GB VRAM, not 24GB.",
            "critical",
        ),
        (
            r"(?:24\s*GB|24GB|24\s*gigabytes)(?:\s+of)?\s+(?:VRAM|video\s+memory|GDDR\w*)[^.]{0,80}?(?:RTX\s*)?5090",
            "RTX 5090 has 32GB VRAM, not 24GB.",
            "critical",
        ),
    ]

    @pytest.fixture(autouse=True)
    def _mock_fact_overrides(self, monkeypatch):
        """Inject test patterns without hitting the DB."""
        import services.content_validator as cv
        monkeypatch.setattr(cv, "_load_fact_overrides_sync", lambda: self._TEST_OVERRIDES)

    def test_rtx_5090_24gb_detected(self):
        content = "The NVIDIA GeForce RTX 5090, with its massive VRAM (24GB), allows local inference."
        result = validate_content("Title", content, "topic")
        hw_issues = [i for i in result.issues if i.category == "known_wrong_fact"]
        assert len(hw_issues) >= 1
        assert result.passed is False  # critical = blocks approval

    def test_rtx_5090_24gb_reversed_order(self):
        """'24GB VRAM on the 5090' (number before product name) is also caught."""
        content = "With 24GB of VRAM, the RTX 5090 is a beast for local AI."
        result = validate_content("Title", content, "topic")
        hw_issues = [i for i in result.issues if i.category == "known_wrong_fact"]
        assert len(hw_issues) >= 1

    def test_rtx_5090_32gb_is_fine(self):
        """Correct spec (32GB) should NOT be flagged."""
        content = "The RTX 5090 ships with 32GB GDDR7 VRAM for local LLM inference."
        result = validate_content("Title", content, "topic")
        hw_issues = [i for i in result.issues if i.category == "known_wrong_fact"]
        assert hw_issues == []

    def test_false_positive_cost_not_triggered(self):
        """'5090 costs $24,000' should NOT match (24 is a dollar amount, not GB)."""
        content = "The RTX 5090 system costs approximately $24,000 fully loaded."
        result = validate_content("Title", content, "topic")
        hw_issues = [i for i in result.issues if i.category == "known_wrong_fact"]
        assert hw_issues == []

    def test_false_positive_other_gpu_not_triggered(self):
        """'RTX 4090 24GB' should NOT match (4090 actually has 24GB — it's correct)."""
        content = "The RTX 4090 with its 24GB VRAM was the previous king of consumer AI."
        result = validate_content("Title", content, "topic")
        hw_issues = [i for i in result.issues if i.category == "known_wrong_fact"]
        assert hw_issues == []


# ============================================================================
# Filler Phrase Patterns (added 2026-04-11)
# ============================================================================


class TestFillerPhrases:
    """Test filler-phrase detector in the body text."""

    def test_many_organizations_have_found(self):
        content = "Many organizations have found that self-hosting is cost-effective. Details here."
        result = validate_content("Title", content, "topic")
        filler = [i for i in result.issues if i.category == "filler_phrase"]
        assert len(filler) >= 1
        assert result.passed is True  # warning, not critical

    def test_future_of_ai_is_here(self):
        content = "The future of AI is here and it's running on local hardware."
        result = validate_content("Title", content, "topic")
        filler = [i for i in result.issues if i.category == "filler_phrase"]
        assert len(filler) >= 1

    def test_unlock_the_potential(self):
        content = "Unlock the full potential of your local GPU with these techniques."
        result = validate_content("Title", content, "topic")
        filler = [i for i in result.issues if i.category == "filler_phrase"]
        assert len(filler) >= 1

    def test_concrete_claim_not_flagged(self):
        content = "Quantized 4-bit models run at 45 tokens/second on the RTX 5090."
        result = validate_content("Title", content, "topic")
        filler = [i for i in result.issues if i.category == "filler_phrase"]
        assert filler == []


# ============================================================================
# GH-91: Warning → critical promotion + Prometheus counter
# ============================================================================


class TestWarningThresholdPromotion:
    """When a single warning category exceeds the reject threshold, every
    warning in that category should be promoted to critical. This is the
    fix for the bug where a post with 9 `unlinked_citation` warnings
    still passed QA at Q80."""

    def _many_filler_phrases(self) -> str:
        # Construct a body with multiple `filler_phrase` warnings.
        # `filler_phrase` is a pure warning category with no separate
        # severity promotion path, so it isolates the count-based
        # promotion. Ends with a period so the truncation detector
        # stays quiet.
        lines = [
            "Many organizations have found this approach useful.",
            "The future of AI is here and it runs locally.",
            "Unlock the full potential of your GPU with these techniques.",
            "In today's fast-paced digital world, things change quickly.",
            "Many companies have found success with self-hosting.",
        ]
        # Pad with neutral prose so the truncation detector stays happy
        # and the post is long enough for other validators to not care.
        return " ".join(lines) + " End of body text."

    def test_many_warnings_promotes_to_critical(self):
        content = self._many_filler_phrases()
        result = validate_content("Title", content, "topic")
        filler = [i for i in result.issues if i.category == "filler_phrase"]
        assert len(filler) >= 4, (
            f"expected many filler_phrase matches, got {len(filler)}"
        )
        # Count (>= 4) exceeds default threshold of 3, so every
        # filler_phrase warning should be promoted to critical.
        assert all(i.severity == "critical" for i in filler), (
            f"expected all filler warnings promoted to critical, "
            f"got severities {[i.severity for i in filler]}"
        )
        assert result.passed is False, (
            "promoted warnings should block the post"
        )

    def test_two_warnings_stay_as_warnings(self):
        # Two filler_phrase matches — BELOW the default threshold of 3,
        # so they should remain warnings and the post should pass.
        content = (
            "Many organizations have found this helpful. "
            "The future of local inference is here today. "
            "Regular prose continues without any filler afterwards."
        )
        result = validate_content("Title", content, "topic")
        filler = [i for i in result.issues if i.category == "filler_phrase"]
        assert len(filler) == 2, (
            f"expected exactly 2 filler matches, got {len(filler)}"
        )
        # No promotion — count 2 <= threshold 3
        assert all(i.severity == "warning" for i in filler)
        assert result.passed is True


class TestNamedSourceNoUrlPromotion:
    """Unlinked citations that name a source type (Medium / article /
    blog post / documentation / paper / study) without a URL.

    The per-instance promotion path was gated off in 2026-05-03 (default
    ``content_validator_named_source_promote_enabled = false``) after
    task 1738's dev_diary post tripped it 7+ times on legitimate
    citations the writer hadn't yet wrapped in Markdown links and the
    veto killed the post. The (a) per-category threshold path still
    catches genuine spam — that's enough signal without a single
    instance being a hard veto.

    These tests assert the new default-off behavior. To re-enable the
    aggressive per-instance promotion (e.g. if a future writer
    regression starts emitting fabricated attributions and the
    threshold path doesn't catch them), set
    ``content_validator_named_source_promote_enabled=true`` in
    app_settings; the legacy behavior is preserved behind the flag."""

    def test_medium_article_without_url_stays_warning_by_default(self):
        # Exact log example from GH-91 — "Medium article" with no URL.
        # Pre-2026-05-03 this promoted to critical; after the gate-off
        # default it stays at warning. A single warning is not enough to
        # veto the post.
        content = (
            "This technique works well in practice, as highlighted in this Medium article."
        )
        result = validate_content("Title", content, "topic")
        unlinked = [i for i in result.issues if i.category == "unlinked_citation"]
        assert unlinked, "expected an unlinked_citation match"
        assert all(i.severity == "warning" for i in unlinked), \
            "named-source-without-URL promotion is gated off by default; should stay warning"
        # One warning is not a hard veto — passed should be True.
        assert result.passed is True

    def test_article_on_redis_memory_usage_without_url_stays_warning_by_default(self):
        content = "As noted in this article on Redis memory usage, caching matters."
        result = validate_content("Title", content, "topic")
        unlinked = [i for i in result.issues if i.category == "unlinked_citation"]
        assert unlinked
        assert all(i.severity == "warning" for i in unlinked)

    def test_medium_article_with_url_stays_warning(self):
        # The detector still flags the prose, but because a URL sits within
        # 100 chars the named-source promoter does not fire. Severity stays
        # at warning (post still passes since it's only one warning).
        content = (
            "This technique works, as highlighted in this Medium article "
            "https://medium.com/@someone/example-post-12345 which explains the details."
        )
        result = validate_content("Title", content, "topic")
        unlinked = [i for i in result.issues if i.category == "unlinked_citation"]
        if unlinked:
            assert all(i.severity == "warning" for i in unlinked)

    def test_generic_unlinked_citation_stays_warning(self):
        # An unlinked_citation that does NOT name a source type should
        # remain a warning (unless the count threshold fires, which it
        # doesn't here since we only have one match).
        content = "The Shadow Price of Speed: What Tech Teams Get Wrong Every Time"
        result = validate_content("Title", content, "topic")
        unlinked = [i for i in result.issues if i.category == "unlinked_citation"]
        if unlinked:
            assert all(i.severity == "warning" for i in unlinked)


class TestPrometheusCounterEmission:
    """The Prometheus counter exposed as
    ``content_validator_warnings_total{rule=...}`` should increment once
    per warning, labeled by the validator rule category. We test by
    reading the in-process counter's value directly.
    """

    def _read_counter(self, rule: str) -> float:
        """Read the current value of the warnings counter for ``rule``."""
        from services.content_validator import CONTENT_VALIDATOR_WARNINGS_TOTAL

        # prometheus_client Counter stores per-label data on the internal
        # metric. We read the value via the public `_metrics` dict. If
        # the counter is the no-op shim (no prometheus_client installed),
        # skip the test — there's nothing to observe.
        labeled = CONTENT_VALIDATOR_WARNINGS_TOTAL.labels(rule=rule)
        # Counter._value is an exposed synchronization primitive; .get()
        # returns the current count.
        val = getattr(labeled, "_value", None)
        if val is None or not hasattr(val, "get"):
            pytest.skip("prometheus_client not installed in this environment")
        return float(val.get())

    def test_counter_increments_per_warning(self):
        rule = "filler_phrase"
        before = self._read_counter(rule)
        # Three filler phrases → three counter increments
        content = (
            "Many organizations have found that self-hosting is cost-effective.\n"
            "The future of AI is here and it's running on local hardware.\n"
            "Unlock the full potential of your local GPU with these techniques.\n"
        )
        validate_content("Title", content, "topic")
        after = self._read_counter(rule)
        assert after - before >= 3, (
            f"expected counter to advance by at least 3, went from {before} to {after}"
        )

    def test_counter_label_matches_rule_category(self):
        """Each rule increments its own labeled series, not a shared one."""
        rule_filler = "filler_phrase"
        rule_unlinked = "unlinked_citation"
        before_filler = self._read_counter(rule_filler)
        before_unlinked = self._read_counter(rule_unlinked)
        # Only a filler phrase in this input; unlinked_citation must NOT
        # advance. (The detector doesn't fire on clean prose.)
        validate_content(
            "Title",
            "Many organizations have found this approach works.",
            "topic",
        )
        after_filler = self._read_counter(rule_filler)
        after_unlinked = self._read_counter(rule_unlinked)
        assert after_filler > before_filler
        assert after_unlinked == before_unlinked


class TestHallucinatedReferenceDetection:
    """GH-83 part b — catch hallucinated library/API references + topic
    mismatches. Built on top of #91's validator-warning plumbing; the
    ``hallucinated_reference`` category uses the same per-rule threshold
    promotion and QA-score penalty as ``unlinked_citation``.

    Real cases from the production log that motivated this rule:
      * ``schedule_callback(event)`` described as a central asyncio
        function — asyncio has no such thing (actual API: ``loop.call_soon``,
        ``loop.call_later``, etc.).
      * ``CadQuery`` recommended from an ai-ml asyncio post — CadQuery is
        a 3D CAD library, topically orthogonal to asyncio.
    """

    def _hallucinated_issues(self, result):
        return [i for i in result.issues if i.category == "hallucinated_reference"]

    def test_real_asyncio_api_not_flagged(self):
        """``asyncio.run`` / ``loop.call_soon()`` are real — no flags."""
        content = (
            "Use `asyncio.run()` to start the event loop. "
            "Within the loop, `loop.call_soon(callback)` schedules work "
            "and `loop.call_later(0.1, callback)` defers it. "
            "`asyncio.create_task(coro)` wraps a coroutine in a Task."
        )
        result = validate_content(
            "How asyncio Works",
            content,
            topic="asyncio",
            tags=["ai-ml", "backend"],
        )
        hallucinated = self._hallucinated_issues(result)
        assert not hallucinated, (
            f"real asyncio APIs should not be flagged, got: {[h.matched_text for h in hallucinated]}"
        )

    def test_schedule_callback_flagged_as_hallucinated(self):
        """Fabricated asyncio function should fire hallucinated_reference."""
        content = (
            "The `schedule_callback(event)` is a central function "
            "responsible for adding tasks to the loop's processing queue."
        )
        result = validate_content(
            "How asyncio Works",
            content,
            topic="asyncio",
            tags=["ai-ml"],
        )
        hallucinated = self._hallucinated_issues(result)
        assert any(
            "schedule_callback" in h.matched_text for h in hallucinated
        ), f"expected schedule_callback to be flagged, got: {[h.matched_text for h in hallucinated]}"

    def test_cadquery_in_ai_ml_post_flagged_as_topic_mismatch(self):
        """Real library but off-topic — flagged with topic-mismatch wording."""
        content = (
            "Consider exploring CadQuery to see how asyncio is used "
            "in a more complex application."
        )
        result = validate_content(
            "Asyncio Deep Dive",
            content,
            topic="asyncio",
            tags=["ai-ml"],
        )
        hallucinated = self._hallucinated_issues(result)
        cadquery_hits = [
            h for h in hallucinated if "cadquery" in h.matched_text.lower()
        ]
        assert cadquery_hits, (
            f"expected CadQuery topic mismatch, got: "
            f"{[h.matched_text for h in hallucinated]}"
        )
        # Topic mismatch path uses specific wording ("off-topic" /
        # "expected topics"); hallucinated-unknown path says "not found".
        # CadQuery is in the top-500 supplement so this must be the
        # mismatch branch.
        assert any(
            "off-topic" in h.description.lower()
            or "expected topics" in h.description.lower()
            for h in cadquery_hits
        ), f"expected topic-mismatch description, got: {[h.description for h in cadquery_hits]}"

    def test_bare_prose_capitalized_words_not_flagged(self):
        """Capitalized proper nouns in prose should not trip the detector."""
        content = (
            "London is a city in England. Matt teaches Python, JavaScript, "
            "and TypeScript to students. Tuesday is the best day for reviews. "
            "Apple sells computers. Netflix streams movies. "
            "The Ferrari team won the race in Monaco. "
        )
        result = validate_content(
            "Prose Test",
            content,
            topic="general",
            tags=["business"],
        )
        hallucinated = self._hallucinated_issues(result)
        assert not hallucinated, (
            f"bare prose should not be flagged as hallucinated, got: "
            f"{[h.matched_text for h in hallucinated]}"
        )

    def test_instance_variables_not_flagged(self):
        """Dotted names rooted at common vars (loop, app, db) must be ignored."""
        content = (
            "Set up `app.state.db = conn`. Later, `client.session.get(...)` "
            "fetches data. The `response.headers` dict has keys. "
            "Inside `loop.create_task(coro)` the task gets scheduled."
        )
        result = validate_content(
            "Common variable patterns",
            content,
            topic="asyncio",
            tags=["backend"],
        )
        # None of those dotted expressions should fire hallucinated_reference
        # because the roots (app, client, response, loop) are whitelisted.
        hallucinated = self._hallucinated_issues(result)
        assert not hallucinated, (
            f"instance-variable dotted access should not be flagged, got: "
            f"{[h.matched_text for h in hallucinated]}"
        )

    def test_known_pypi_package_backtick_passes(self):
        """A plain ``fastapi`` / ``django`` reference should be fine."""
        content = (
            "We use `fastapi` with `uvicorn` behind the scenes. "
            "`django` works too. `pytest` runs the suite."
        )
        result = validate_content(
            "Web Stack",
            content,
            topic="web-dev",
            tags=["web-dev", "backend"],
        )
        hallucinated = self._hallucinated_issues(result)
        assert not hallucinated, (
            f"top-500 PyPI packages should pass, got: "
            f"{[h.matched_text for h in hallucinated]}"
        )

    def test_ollama_model_names_not_flagged(self):
        """Common Ollama model names are a real source list — must pass."""
        content = (
            "We run `llama3` locally alongside `gemma3` and `qwen2.5`. "
            "For coding tasks, `qwen2.5-coder` outperforms `codellama`."
        )
        result = validate_content(
            "Local Models",
            content,
            topic="ollama",
            tags=["ai-ml"],
        )
        hallucinated = self._hallucinated_issues(result)
        assert not hallucinated, (
            f"Ollama model names should pass, got: "
            f"{[h.matched_text for h in hallucinated]}"
        )

    def test_hallucinated_reference_promotes_after_threshold(self):
        """Many hallucinated_reference warnings should promote to critical.

        Uses the same threshold (``content_validator_warning_reject_threshold``
        default 3) wired for ``unlinked_citation`` in #91.
        """
        content = (
            "The post name-drops `fakelib_one(event)`, then suggests "
            "`fakelib_two.method(arg)`, then adds `fakelib_three.do(x)`, "
            "and also relies on `fakelib_four.process(buf)` for the "
            "final step of the process."
        )
        result = validate_content(
            "Fake References",
            content,
            topic="python",
            tags=["backend"],
        )
        hallucinated = self._hallucinated_issues(result)
        # 4+ matches should exceed the default threshold of 3 and promote.
        assert len(hallucinated) >= 4, (
            f"expected 4+ hallucinated matches, got {len(hallucinated)}: "
            f"{[h.matched_text for h in hallucinated]}"
        )
        assert all(h.severity == "critical" for h in hallucinated), (
            f"expected all hallucinated warnings to promote to critical, "
            f"got severities {[h.severity for h in hallucinated]}"
        )
        assert result.passed is False

    def test_tags_parameter_is_optional(self):
        """Calling validate_content without tags must still work."""
        content = "The `schedule_callback(event)` is not a real function."
        # Positional form — no regression in legacy callers.
        result = validate_content("Asyncio", content, "asyncio")
        hallucinated = self._hallucinated_issues(result)
        assert hallucinated, (
            "schedule_callback should still be flagged without tags"
        )

    def test_topic_coherence_uses_title_fallback_when_no_tags(self):
        """Without tags, the title/topic text should drive topic coherence."""
        content = "Consider exploring CadQuery alongside your workflow."
        # Title text hints ai-ml. No tags provided. CadQuery should still
        # be flagged as off-topic because the title/topic tokens ("ai-ml",
        # "asyncio") share no overlap with ["cad", "3d-modeling"].
        result = validate_content(
            "Ai-ml Asyncio Deep Dive",
            content,
            topic="asyncio",
        )
        hallucinated = self._hallucinated_issues(result)
        assert any(
            "cadquery" in h.matched_text.lower() for h in hallucinated
        ), f"expected CadQuery flagged without tags, got: {[h.matched_text for h in hallucinated]}"

    def test_stdlib_and_pypi_lists_actually_loaded(self):
        """Guard against the data files going missing in deployment."""
        from services.content_validator import (
            _get_ollama_names,
            _get_pypi_names,
            _get_stdlib_names,
        )
        stdlib = _get_stdlib_names()
        pypi = _get_pypi_names()
        ollama = _get_ollama_names()
        # Sanity anchors: asyncio is stdlib, fastapi is top-500, llama3 is ollama.
        assert "asyncio" in stdlib
        assert "os" in stdlib
        assert "fastapi" in pypi
        assert "requests" in pypi
        assert "numpy" in pypi
        assert "llama3" in ollama or "llama" in ollama
        # Loose sanity — we expect 100+ stdlib and 400+ PyPI entries.
        assert len(stdlib) > 100, f"stdlib list unexpectedly small: {len(stdlib)}"
        assert len(pypi) > 400, f"pypi list unexpectedly small: {len(pypi)}"


# ---------------------------------------------------------------------------
# JSON envelope leak vs truncation — 2026-05-16 split (rule #10)
# ---------------------------------------------------------------------------
#
# Pins the contract that closed the ``pipeline_versions.id=1851`` failure
# mode. ``two_pass._revise_node`` was calling ``_ollama_chat_json`` (which
# forces ``format=json`` on Ollama). When the writer responded with
# ``{"content": "...the post body..."}`` and nothing un-wrapped it, the
# validator's truncation rule fired because the last line was ``}``.
# Same severity — still critical, post still rejected — but the operator
# now sees ``json_envelope_leak`` in ``qa_feedback`` and can fix the
# producer instead of chasing a token-budget red herring.


class TestJsonEnvelopeLeakDetection:
    """A lone ``}`` / ``]`` final line is a JSON-envelope leak, not a
    truncation. The validator splits the diagnostic so operators chase
    the right root cause."""

    _LONG_BODY = (
        "FastAPI is a modern Python web framework for building APIs. "
        "It uses Pydantic for validation and supports async natively. "
        "Type hints make endpoints self-documenting through OpenAPI. "
    ) * 4  # ~600 chars — well past the 200-char minimum for rule #10

    def test_lone_brace_terminator_flagged_as_envelope_leak(self):
        """Final line is just ``}`` → ``json_envelope_leak`` (NOT truncation)."""
        content = self._LONG_BODY + '"\n}'
        result = validate_content("FastAPI Intro", content, "FastAPI")
        cats = [i.category for i in result.issues]
        assert "json_envelope_leak" in cats
        assert "truncated_content" not in cats
        # Severity preserved
        leak = next(i for i in result.issues if i.category == "json_envelope_leak")
        assert leak.severity == "critical"
        # Description points at the producer, not at the LLM token budget
        assert "envelope" in leak.description.lower()
        assert "format=json" in leak.description.lower() or "writer" in leak.description.lower()

    def test_lone_bracket_terminator_flagged_as_envelope_leak(self):
        """``]`` final line — same shape but JSON array wrapper."""
        content = self._LONG_BODY + '"\n]'
        result = validate_content("FastAPI Intro", content, "FastAPI")
        cats = [i.category for i in result.issues]
        assert "json_envelope_leak" in cats

    def test_quoted_brace_terminator_flagged_as_envelope_leak(self):
        """``"}`` line — common when the writer dumped a quoted string
        immediately followed by the envelope close."""
        content = self._LONG_BODY + '\n"}'
        result = validate_content("FastAPI Intro", content, "FastAPI")
        cats = [i.category for i in result.issues]
        assert "json_envelope_leak" in cats

    def test_real_truncation_still_flagged_separately(self):
        """Mid-sentence cutoff (no trailing brace) — keep firing
        ``truncated_content``. The split must not weaken truncation
        detection."""
        # Ends mid-sentence with no terminator at all
        content = self._LONG_BODY + " The team began to investigate when"
        result = validate_content("FastAPI Intro", content, "FastAPI")
        cats = [i.category for i in result.issues]
        assert "truncated_content" in cats
        assert "json_envelope_leak" not in cats

    def test_clean_prose_passes_both_rules(self):
        """A complete sentence-terminated body fires neither rule —
        baseline."""
        content = self._LONG_BODY + " Conclusion: the framework wins."
        result = validate_content("FastAPI Intro", content, "FastAPI")
        cats = [i.category for i in result.issues]
        assert "truncated_content" not in cats
        assert "json_envelope_leak" not in cats
