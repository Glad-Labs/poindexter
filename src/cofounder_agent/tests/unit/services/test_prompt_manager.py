"""
Unit tests for UnifiedPromptManager service.

Tests prompt registration, retrieval with variable formatting,
metadata access, category filtering, and JSON export — no LLM or DB calls.
"""

import json
from unittest.mock import MagicMock

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
        for _key, data in result.items():
            assert data["category"] == PromptCategory.BLOG_GENERATION.value

    def test_filter_by_seo_category(self, pm: UnifiedPromptManager):
        result = pm.list_prompts(category=PromptCategory.SEO_METADATA)
        assert len(result) > 0
        for _key, data in result.items():
            assert data["category"] == PromptCategory.SEO_METADATA.value

    def test_filter_by_qa_category(self, pm: UnifiedPromptManager):
        result = pm.list_prompts(category=PromptCategory.CONTENT_QA)
        assert len(result) > 0

    def test_result_contains_expected_fields(self, pm: UnifiedPromptManager):
        result = pm.list_prompts()
        for _key, data in result.items():
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
        for _key, entry in parsed.items():
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


# ---------------------------------------------------------------------------
# Premium gating (gitea#225)
# ---------------------------------------------------------------------------


class _FakeRow(dict):
    """asyncpg.Record stand-in that supports both dict + ``row[key]`` access."""

    def __init__(self, **kw):
        super().__init__(**kw)


class _FakePool:
    """Minimal pool stub for ``load_from_db`` — returns the prompt rows
    we hand it and a single ``premium_active`` value when needed."""

    def __init__(self, rows, premium_active_value=None):
        self._rows = [_FakeRow(**r) for r in rows]
        self._premium_active = premium_active_value

    async def fetch(self, _query):
        return self._rows

    async def fetchrow(self, _query):
        if self._premium_active is None:
            return None
        return _FakeRow(value=self._premium_active)


class _FakeSiteConfig:
    def __init__(self, **kv):
        self._kv = kv

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self._kv.get(key)
        if v is None:
            return default
        return str(v).strip().lower() in ("true", "1", "yes", "on")


@pytest.mark.unit
class TestPremiumGating:
    @pytest.fixture
    def rows_default_only(self):
        return [
            {"key": "seo_title", "template": "FREE TITLE: {topic}", "source": "default"},
        ]

    @pytest.fixture
    def rows_default_and_premium(self):
        return [
            {"key": "seo_title", "template": "FREE TITLE: {topic}", "source": "default"},
            {"key": "seo_title", "template": "PRO TITLE for {topic}", "source": "premium"},
            {"key": "blog_intro", "template": "PRO INTRO: {topic}", "source": "premium"},
        ]

    @pytest.mark.asyncio
    async def test_default_only_returns_default(self, pm, rows_default_only):
        pool = _FakePool(rows_default_only)
        await pm.load_from_db(pool, site_config=_FakeSiteConfig(premium_active=True))
        # Premium flag is true but no premium row exists — fall back to default
        out = pm.get_prompt("seo_title", topic="x")
        assert out == "FREE TITLE: x"

    @pytest.mark.asyncio
    async def test_premium_active_uses_premium_override(
        self, pm, rows_default_and_premium,
    ):
        pool = _FakePool(rows_default_and_premium)
        await pm.load_from_db(pool, site_config=_FakeSiteConfig(premium_active=True))
        assert pm.get_prompt("seo_title", topic="x") == "PRO TITLE for x"

    @pytest.mark.asyncio
    async def test_premium_inactive_uses_default(self, pm, rows_default_and_premium):
        pool = _FakePool(rows_default_and_premium)
        await pm.load_from_db(pool, site_config=_FakeSiteConfig(premium_active=False))
        # Premium row exists but flag is off — must use default
        assert pm.get_prompt("seo_title", topic="x") == "FREE TITLE: x"

    @pytest.mark.asyncio
    async def test_premium_only_key_falls_back_to_yaml_when_inactive(
        self, pm, rows_default_and_premium,
    ):
        """Premium has 'blog_intro' but no default — when inactive, YAML
        fallback or KeyError. (No YAML key by this name exists.)"""
        pool = _FakePool(rows_default_and_premium)
        await pm.load_from_db(pool, site_config=_FakeSiteConfig(premium_active=False))
        with pytest.raises(KeyError):
            pm.get_prompt("blog_intro", topic="x")

    @pytest.mark.asyncio
    async def test_live_flag_flip_takes_effect_without_reload(
        self, pm, rows_default_and_premium,
    ):
        """The whole point of passing site_config to load_from_db: flipping
        ``premium_active`` should change ``get_prompt`` output without a
        second ``load_from_db`` call (no worker restart on activate)."""
        sc = _FakeSiteConfig(premium_active=False)
        pool = _FakePool(rows_default_and_premium)
        await pm.load_from_db(pool, site_config=sc)

        assert pm.get_prompt("seo_title", topic="x") == "FREE TITLE: x"

        # Operator activates Pro tier — site_config now reports True.
        sc._kv["premium_active"] = True
        assert pm.get_prompt("seo_title", topic="x") == "PRO TITLE for x"

        # …and back to free if they cancel.
        sc._kv["premium_active"] = False
        assert pm.get_prompt("seo_title", topic="x") == "FREE TITLE: x"

    @pytest.mark.asyncio
    async def test_no_site_config_falls_back_to_db_snapshot(
        self, pm, rows_default_and_premium,
    ):
        """Without site_config, the loader takes a one-shot DB read of
        ``premium_active``. Useful for tests + REPL but won't pick up
        later activations."""
        pool = _FakePool(rows_default_and_premium, premium_active_value="true")
        await pm.load_from_db(pool, site_config=None)
        assert pm.get_prompt("seo_title", topic="x") == "PRO TITLE for x"


# ---------------------------------------------------------------------------
# Langfuse-first lookup (#203 Phase 2a)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfuseFirstLookup:
    """When Langfuse returns a template, it wins over DB + YAML.
    When Langfuse returns None / errors, fall through cleanly."""

    def test_langfuse_template_wins_over_yaml(self, pm: UnifiedPromptManager):
        # Stub Langfuse client that returns a template for one key.
        fake_prompt = MagicMock()
        fake_prompt.prompt = "LANGFUSE TEMPLATE for {topic}"
        pm._langfuse = MagicMock()
        pm._langfuse.get_prompt = MagicMock(return_value=fake_prompt)

        # Pick any real key that already has a YAML template.
        key = "blog_generation.initial_draft"
        result = pm.get_prompt(key, topic="X", style="s", tone="t",
                               target_length=500, tags="")
        assert result.startswith("LANGFUSE TEMPLATE")

    def test_langfuse_miss_falls_through_to_yaml(self, pm: UnifiedPromptManager):
        # Stub Langfuse client that raises (e.g. 404 on get_prompt).
        pm._langfuse = MagicMock()
        pm._langfuse.get_prompt = MagicMock(side_effect=Exception("not found"))

        # YAML still serves the prompt. Use seo.generate_title (single var).
        result = pm.get_prompt("seo.generate_title", topic="AI")
        assert "LANGFUSE" not in result
        assert "AI" in result

    def test_langfuse_lookup_cached_per_key(self, pm: UnifiedPromptManager):
        # Calling get_prompt twice with the same key only hits Langfuse once.
        fake_prompt = MagicMock()
        fake_prompt.prompt = "TEMPLATE {topic}"
        pm._langfuse = MagicMock()
        pm._langfuse.get_prompt = MagicMock(return_value=fake_prompt)

        key = "blog_generation.initial_draft"
        kwargs = dict(topic="X", style="s", tone="t",
                      target_length=500, tags="")
        pm.get_prompt(key, **kwargs)
        pm.get_prompt(key, **kwargs)

        assert pm._langfuse.get_prompt.call_count == 1

    def test_no_langfuse_env_silently_skips(self, monkeypatch, pm: UnifiedPromptManager):
        # Without env vars, _try_langfuse short-circuits to None and the
        # YAML path serves normally — no exception, no log spam.
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        # Force re-init by clearing cache.
        pm._langfuse = None
        pm._langfuse_lookup_cache.clear()

        result = pm.get_prompt("seo.generate_title", topic="AI")
        assert isinstance(result, str)
        assert "AI" in result
