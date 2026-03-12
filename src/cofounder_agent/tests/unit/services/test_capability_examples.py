"""
Unit tests for services/capability_examples.py

Tests standalone capability functions, class-based capabilities, and
the registration entry point. All functions are async and return predictable
shapes without real external calls.
"""

import pytest
from unittest.mock import MagicMock, patch

from services.capability_examples import (
    ComplianceCheckCapability,
    FinancialAnalysisCapability,
    critique_capability,
    generate_content_capability,
    publish_capability,
    register_example_capabilities,
    research_capability,
    select_images_capability,
)


# ---------------------------------------------------------------------------
# Function-based capabilities
# ---------------------------------------------------------------------------


class TestResearchCapability:
    @pytest.mark.asyncio
    async def test_returns_topic(self):
        result = await research_capability("Artificial Intelligence")
        assert result["topic"] == "Artificial Intelligence"

    @pytest.mark.asyncio
    async def test_contains_findings(self):
        result = await research_capability("ML")
        assert "findings" in result
        assert "ML" in result["findings"]

    @pytest.mark.asyncio
    async def test_contains_sources(self):
        result = await research_capability("AI")
        assert "sources" in result
        assert len(result["sources"]) > 0

    @pytest.mark.asyncio
    async def test_default_depth_medium(self):
        result = await research_capability("Python")
        assert result["depth"] == "medium"

    @pytest.mark.asyncio
    async def test_custom_depth(self):
        result = await research_capability("Python", depth="deep")
        assert result["depth"] == "deep"


class TestGenerateContentCapability:
    @pytest.mark.asyncio
    async def test_returns_topic(self):
        result = await generate_content_capability("Future of AI")
        assert result["topic"] == "Future of AI"

    @pytest.mark.asyncio
    async def test_default_style_professional(self):
        result = await generate_content_capability("Topic")
        assert result["style"] == "professional"

    @pytest.mark.asyncio
    async def test_custom_style(self):
        result = await generate_content_capability("Topic", style="casual")
        assert result["style"] == "casual"

    @pytest.mark.asyncio
    async def test_medium_length_word_count_500(self):
        result = await generate_content_capability("Topic", length="medium")
        assert result["word_count"] == 500

    @pytest.mark.asyncio
    async def test_non_medium_length_word_count_1000(self):
        result = await generate_content_capability("Topic", length="long")
        assert result["word_count"] == 1000

    @pytest.mark.asyncio
    async def test_content_mentions_topic(self):
        result = await generate_content_capability("Blockchain")
        assert "Blockchain" in result["content"]


class TestCritiqueCapability:
    @pytest.mark.asyncio
    async def test_returns_feedback(self):
        result = await critique_capability("Some content to review")
        assert "feedback" in result
        assert len(result["feedback"]) > 0

    @pytest.mark.asyncio
    async def test_returns_score(self):
        result = await critique_capability("Content")
        assert "score" in result
        assert 0 <= result["score"] <= 10

    @pytest.mark.asyncio
    async def test_returns_suggestions(self):
        result = await critique_capability("Content")
        assert "suggestions" in result
        assert isinstance(result["suggestions"], list)

    @pytest.mark.asyncio
    async def test_default_focus_quality(self):
        result = await critique_capability("Content")
        assert result["focus"] == "quality"

    @pytest.mark.asyncio
    async def test_custom_focus(self):
        result = await critique_capability("Content", focus="clarity")
        assert result["focus"] == "clarity"


class TestSelectImagesCapability:
    @pytest.mark.asyncio
    async def test_returns_images_list(self):
        result = await select_images_capability("AI")
        assert "images" in result
        assert isinstance(result["images"], list)

    @pytest.mark.asyncio
    async def test_default_count_3(self):
        result = await select_images_capability("AI")
        assert result["count"] == 3
        assert len(result["images"]) == 3

    @pytest.mark.asyncio
    async def test_custom_count(self):
        result = await select_images_capability("AI", count=5)
        assert result["count"] == 5
        assert len(result["images"]) == 5

    @pytest.mark.asyncio
    async def test_image_structure(self):
        result = await select_images_capability("AI", count=1)
        img = result["images"][0]
        assert "url" in img
        assert "title" in img
        assert "alt_text" in img

    @pytest.mark.asyncio
    async def test_returns_topic(self):
        result = await select_images_capability("Machine Learning")
        assert result["topic"] == "Machine Learning"


class TestPublishCapability:
    @pytest.mark.asyncio
    async def test_default_platform_blog(self):
        result = await publish_capability("Post content")
        assert result["platform"] == "blog"

    @pytest.mark.asyncio
    async def test_custom_platform(self):
        result = await publish_capability("Post content", platform="twitter")
        assert result["platform"] == "twitter"

    @pytest.mark.asyncio
    async def test_status_published(self):
        result = await publish_capability("Post content")
        assert result["status"] == "published"

    @pytest.mark.asyncio
    async def test_url_contains_platform(self):
        result = await publish_capability("Post content", platform="linkedin")
        assert "linkedin" in result["url"]

    @pytest.mark.asyncio
    async def test_scheduled_at_used(self):
        result = await publish_capability("Post", schedule_at="2025-12-25T10:00:00Z")
        assert result["published_at"] == "2025-12-25T10:00:00Z"

    @pytest.mark.asyncio
    async def test_default_published_at_now(self):
        result = await publish_capability("Post")
        assert result["published_at"] == "now"


# ---------------------------------------------------------------------------
# Class-based capabilities
# ---------------------------------------------------------------------------


class TestFinancialAnalysisCapability:
    def test_metadata_name(self):
        cap = FinancialAnalysisCapability()
        assert cap.metadata.name == "financial.analysis"

    def test_metadata_has_tags(self):
        cap = FinancialAnalysisCapability()
        assert "financial" in cap.metadata.tags

    def test_input_schema_has_company_id(self):
        cap = FinancialAnalysisCapability()
        param_names = [p.name for p in cap.input_schema.parameters]
        assert "company_id" in param_names

    def test_input_schema_has_period(self):
        cap = FinancialAnalysisCapability()
        param_names = [p.name for p in cap.input_schema.parameters]
        assert "period" in param_names

    @pytest.mark.asyncio
    async def test_execute_returns_company_id(self):
        cap = FinancialAnalysisCapability()
        result = await cap.execute(company_id="ACME")
        assert result["company_id"] == "ACME"

    @pytest.mark.asyncio
    async def test_execute_default_period_quarterly(self):
        cap = FinancialAnalysisCapability()
        result = await cap.execute(company_id="ACME")
        assert result["period"] == "quarterly"

    @pytest.mark.asyncio
    async def test_execute_custom_period(self):
        cap = FinancialAnalysisCapability()
        result = await cap.execute(company_id="ACME", period="annual")
        assert result["period"] == "annual"

    @pytest.mark.asyncio
    async def test_execute_returns_financials(self):
        cap = FinancialAnalysisCapability()
        result = await cap.execute(company_id="ACME")
        assert "revenue" in result
        assert "profit" in result
        assert "roi" in result


class TestComplianceCheckCapability:
    def test_metadata_name(self):
        cap = ComplianceCheckCapability()
        assert cap.metadata.name == "compliance.check"

    def test_metadata_has_compliance_tag(self):
        cap = ComplianceCheckCapability()
        assert "compliance" in cap.metadata.tags

    def test_input_schema_has_content(self):
        cap = ComplianceCheckCapability()
        param_names = [p.name for p in cap.input_schema.parameters]
        assert "content" in param_names

    @pytest.mark.asyncio
    async def test_execute_is_compliant(self):
        cap = ComplianceCheckCapability()
        result = await cap.execute(content="Safe content text")
        assert result["is_compliant"] is True
        assert result["issues_found"] == []

    @pytest.mark.asyncio
    async def test_execute_with_regulations(self):
        cap = ComplianceCheckCapability()
        regs = ["GDPR", "CCPA"]
        result = await cap.execute(content="Content", regulations=regs)
        assert result["regulations_checked"] == regs

    @pytest.mark.asyncio
    async def test_execute_confidence_is_float(self):
        cap = ComplianceCheckCapability()
        result = await cap.execute(content="Content")
        assert isinstance(result["confidence"], float)
        assert 0 <= result["confidence"] <= 1


# ---------------------------------------------------------------------------
# register_example_capabilities
# ---------------------------------------------------------------------------


class TestRegisterExampleCapabilities:
    def test_registers_without_error(self):
        """register_example_capabilities should not raise."""
        from services.capability_registry import CapabilityRegistry, set_registry
        set_registry(CapabilityRegistry())
        register_example_capabilities()

    def test_capabilities_available_after_registration(self):
        from services.capability_registry import CapabilityRegistry, get_registry, set_registry
        set_registry(CapabilityRegistry())
        register_example_capabilities()
        registry = get_registry()
        all_caps = registry.list_capabilities()
        # list_capabilities() returns Dict[str, CapabilityMetadata] — keys are names
        cap_names = list(all_caps.keys())
        assert "research" in cap_names
        assert "generate_content" in cap_names
        assert "critique" in cap_names
        assert "select_images" in cap_names
        assert "publish" in cap_names
        assert "financial.analysis" in cap_names
        assert "compliance.check" in cap_names
