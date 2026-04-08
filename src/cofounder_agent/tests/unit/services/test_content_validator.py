"""
Unit tests for services/content_validator.py

Tests the programmatic quality gate: fake names, stats, impossible claims,
fabricated quotes, and company fact validation.
"""

import pytest

from services.content_validator import (
    GLAD_LABS_FACTS,
    validate_content,
    ValidationResult,
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
