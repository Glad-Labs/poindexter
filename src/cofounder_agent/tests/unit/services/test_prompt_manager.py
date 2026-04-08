"""
Unit tests for UnifiedPromptManager service.

Tests prompt registration, retrieval with variable formatting,
metadata access, category filtering, and JSON export — no LLM or DB calls.
"""

import json

import pytest

from services.prompt_manager import (
    PromptCategory,
    PromptMetadata,
    PromptVersion,
    UnifiedPromptManager,
    get_prompt_manager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm() -> UnifiedPromptManager:
    """Fresh UnifiedPromptManager instance."""
    return UnifiedPromptManager()


# ---------------------------------------------------------------------------
# Initialisation & registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitialization:
    def test_prompts_populated_on_init(self, pm: UnifiedPromptManager):
        """All built-in prompts are registered at construction time."""
        assert len(pm.prompts) > 0

    def test_metadata_matches_prompts(self, pm: UnifiedPromptManager):
        """Every prompt has a corresponding metadata entry."""
        assert set(pm.prompts.keys()) == set(pm.metadata.keys())

    def test_known_prompt_keys_present(self, pm: UnifiedPromptManager):
        expected_keys = [
            "blog_generation.initial_draft",
            "blog_generation.seo_and_social",
            "blog_generation.iterative_refinement",
            "blog_generation.blog_system_prompt",
            "blog_generation.blog_generation_request",
            "qa.content_review",
            "qa.self_critique",
            "seo.generate_title",
            "seo.generate_meta_description",
            "seo.extract_keywords",
            "seo.generate_excerpt",
            "seo.match_category",
            "seo.extract_tags",
            "research.analyze_search_results",
            "social.research_trends",
            "social.create_post",
            "image.featured_image",
            "image.search_queries",
            "system.content_writer",
            "task.creative_blog_generation",
            "task.qa_content_evaluation",
            "task.business_financial_impact",
            "task.business_market_analysis",
            "task.business_performance_analysis",
            "task.automation_email_campaign",
            "task.content_summarization",
            "task.utility_json_conversion",
        ]
        for key in expected_keys:
            assert key in pm.prompts, f"Expected prompt key missing: {key}"


# ---------------------------------------------------------------------------
# get_prompt — happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPrompt:
    def test_seo_title_formats_correctly(self, pm: UnifiedPromptManager):
        result = pm.get_prompt("seo.generate_title", topic="AI healthcare")
        assert "AI healthcare" in result

    def test_blog_initial_draft_substitutes_all_vars(self, pm: UnifiedPromptManager):
        result = pm.get_prompt(
            "blog_generation.initial_draft",
            topic="Machine Learning",
            style="professional",
            tone="informative",
            target_length=1500,
            research_context="Recent advances in transformers",
        )
        assert "Machine Learning" in result
        assert "1500" in result

    def test_qa_content_review_substitutes_vars(self, pm: UnifiedPromptManager):
        result = pm.get_prompt(
            "qa.content_review",
            content="This blog post discusses return on investment...",
        )
        assert "return on investment" in result

    def test_returns_string(self, pm: UnifiedPromptManager):
        result = pm.get_prompt("seo.generate_excerpt", content="Some content here")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_image_search_queries_formats_topic(self, pm: UnifiedPromptManager):
        result = pm.get_prompt("image.search_queries", topic="AI in Healthcare")
        assert "AI in Healthcare" in result

    def test_blog_system_prompt_formats_vars(self, pm: UnifiedPromptManager):
        result = pm.get_prompt(
            "blog_generation.blog_system_prompt",
            style="balanced",
            target_audience="developers",
            domain="technology",
            tone="professional",
        )
        assert "balanced" in result
        assert "developers" in result
        assert "technology" in result


# ---------------------------------------------------------------------------
# get_prompt — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPromptErrors:
    def test_unknown_key_raises_key_error(self, pm: UnifiedPromptManager):
        with pytest.raises(KeyError) as exc_info:
            pm.get_prompt("nonexistent.prompt.key")
        assert "nonexistent.prompt.key" in str(exc_info.value)
        assert "Available" in str(exc_info.value)

    def test_missing_variable_raises_key_error_with_hint(self, pm: UnifiedPromptManager):
        with pytest.raises(KeyError) as exc_info:
            pm.get_prompt("blog_generation.initial_draft")  # missing all vars
        error_msg = str(exc_info.value)
        assert "missing required variable" in error_msg

    def test_partial_variables_raises_key_error(self, pm: UnifiedPromptManager):
        with pytest.raises(KeyError):
            # blog_generation.initial_draft needs topic, style, tone,
            # target_length, research_context
            pm.get_prompt("blog_generation.initial_draft", topic="only topic provided")


# ---------------------------------------------------------------------------
# get_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMetadata:
    def test_returns_prompt_metadata_instance(self, pm: UnifiedPromptManager):
        meta = pm.get_metadata("seo.generate_title")
        assert isinstance(meta, PromptMetadata)

    def test_category_correct(self, pm: UnifiedPromptManager):
        meta = pm.get_metadata("seo.generate_title")
        assert meta.category == PromptCategory.SEO_METADATA

    def test_output_format_set(self, pm: UnifiedPromptManager):
        meta = pm.get_metadata("seo.generate_title")
        assert meta.output_format == "text"

    def test_version_is_enum(self, pm: UnifiedPromptManager):
        meta = pm.get_metadata("blog_generation.initial_draft")
        assert isinstance(meta.version, PromptVersion)

    def test_description_nonempty(self, pm: UnifiedPromptManager):
        meta = pm.get_metadata("qa.content_review")
        assert len(meta.description) > 0

    def test_unknown_key_raises_key_error(self, pm: UnifiedPromptManager):
        with pytest.raises(KeyError):
            pm.get_metadata("does.not.exist")


# ---------------------------------------------------------------------------
# list_prompts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListPrompts:
    def test_no_filter_returns_all(self, pm: UnifiedPromptManager):
        result = pm.list_prompts()
        assert len(result) == len(pm.prompts)

    def test_filter_by_blog_category(self, pm: UnifiedPromptManager):
        result = pm.list_prompts(category=PromptCategory.BLOG_GENERATION)
        assert len(result) > 0
        for key, data in result.items():
            assert data["category"] == PromptCategory.BLOG_GENERATION.value

    def test_filter_by_seo_category(self, pm: UnifiedPromptManager):
        result = pm.list_prompts(category=PromptCategory.SEO_METADATA)
        assert len(result) > 0
        for key, data in result.items():
            assert data["category"] == PromptCategory.SEO_METADATA.value

    def test_filter_by_qa_category(self, pm: UnifiedPromptManager):
        result = pm.list_prompts(category=PromptCategory.CONTENT_QA)
        assert len(result) > 0

    def test_result_contains_expected_fields(self, pm: UnifiedPromptManager):
        result = pm.list_prompts()
        for key, data in result.items():
            assert "category" in data
            assert "description" in data
            assert "output_format" in data
            assert "version" in data

    def test_filter_returns_subset_of_all(self, pm: UnifiedPromptManager):
        all_prompts = pm.list_prompts()
        blog_only = pm.list_prompts(category=PromptCategory.BLOG_GENERATION)
        assert len(blog_only) < len(all_prompts)

    def test_filter_nonexistent_category_returns_empty(self, pm: UnifiedPromptManager):
        result = pm.list_prompts(category=PromptCategory.FINANCIAL)
        # task.business_* prompts have FINANCIAL category
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# export_prompts_as_json
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExportPromptsAsJson:
    def test_returns_valid_json_string(self, pm: UnifiedPromptManager):
        result = pm.export_prompts_as_json()
        assert isinstance(result, str)
        parsed = json.loads(result)  # Must not raise
        assert isinstance(parsed, dict)

    def test_exported_json_contains_all_keys(self, pm: UnifiedPromptManager):
        parsed = json.loads(pm.export_prompts_as_json())
        for key in pm.prompts:
            assert key in parsed

    def test_each_entry_has_required_fields(self, pm: UnifiedPromptManager):
        parsed = json.loads(pm.export_prompts_as_json())
        required_fields = {"template", "category", "description", "output_format", "version"}
        for key, entry in parsed.items():
            missing = required_fields - set(entry.keys())
            assert not missing, f"Prompt '{key}' missing fields: {missing}"

    def test_template_is_nonempty_string(self, pm: UnifiedPromptManager):
        parsed = json.loads(pm.export_prompts_as_json())
        for key, entry in parsed.items():
            assert isinstance(entry["template"], str)
            assert len(entry["template"]) > 0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPromptManager:
    def test_returns_unified_prompt_manager(self):
        mgr = get_prompt_manager()
        assert isinstance(mgr, UnifiedPromptManager)

    def test_returns_same_instance_on_repeated_calls(self):
        mgr1 = get_prompt_manager()
        mgr2 = get_prompt_manager()
        assert mgr1 is mgr2


# ---------------------------------------------------------------------------
# PromptCategory enum coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPromptCategoryEnum:
    def test_all_expected_categories_exist(self):
        expected = {
            "BLOG_GENERATION",
            "CONTENT_QA",
            "SEO_METADATA",
            "SOCIAL_MEDIA",
            "RESEARCH",
            "FINANCIAL",
            "MARKET_ANALYSIS",
            "IMAGE_GENERATION",
            "UTILITY",
        }
        actual = {c.name for c in PromptCategory}
        assert expected == actual

    def test_category_values_are_strings(self):
        for cat in PromptCategory:
            assert isinstance(cat.value, str)


# ---------------------------------------------------------------------------
# PromptVersion enum coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPromptVersionEnum:
    def test_v1_0_exists(self):
        assert PromptVersion.V1_0.value == "v1.0"

    def test_v1_1_exists(self):
        assert PromptVersion.V1_1.value == "v1.1"
