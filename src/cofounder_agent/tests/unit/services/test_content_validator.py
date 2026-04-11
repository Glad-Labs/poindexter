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
        result = validate_content(
            "Clean",
            "Studies show a 50% reduction in costs when migrating to the cloud.",
            "topic",
        )
        assert result.warning_count >= 1

    def test_score_penalty_critical_is_10(self):
        result = validate_content("Clean", "[IMAGE-1: photo]", "topic")
        # 1 critical → at least 10 penalty
        assert result.score_penalty >= 10

    def test_score_penalty_warning_is_3(self):
        """A single warning issue contributes 3 to score_penalty."""
        result = validate_content(
            "Clean", "Studies show a 50% increase in performance.", "topic"
        )
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

    def test_hallucinated_link_is_warning_not_critical(self):
        content = "Read our guide on optimization techniques."
        result = validate_content("Title", content, "tech")
        link_issues = [i for i in result.issues if i.category == "hallucinated_link"]
        assert all(i.severity == "warning" for i in link_issues)


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
