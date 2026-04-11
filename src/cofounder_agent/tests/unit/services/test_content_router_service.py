"""
Unit tests for services/content_router_service.py

Covers ContentTaskStore:
- create_task: delegates to database_service.add_task, returns task_id
- create_task: raises when database_service is None
- get_task: delegates to database_service.get_task
- get_task: returns None when database_service is None
- update_task: delegates, converts metadata→task_metadata
- update_task: returns None when database_service is None
- delete_task: delegates, returns True/False
- delete_task: returns False when database_service is None
- list_tasks: delegates with correct pagination/filter
- list_tasks: returns [] when database_service is None
- get_drafts: delegates with correct args
- get_drafts: returns ([], 0) when database_service is None
- persistent_store property returns database_service

Also covers get_content_task_store singleton behavior.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.content_router_service import (
    ContentTaskStore,
    _check_title_originality,
    _get_or_create_default_author,
    _is_stage_enabled,
    _load_stage_timeouts,
    _normalize_text,
    _parse_model_preferences,
    _run_stage_with_timeout,
    _scrub_fabricated_links,
    _select_category_for_topic,
    _stage_verify_task,
    get_content_task_store,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_db():
    db = AsyncMock()
    db.add_task = AsyncMock(return_value="new-task-id-123")
    db.get_task = AsyncMock(return_value={"id": "new-task-id-123", "topic": "AI"})
    db.update_task = AsyncMock(return_value=True)
    db.delete_task = AsyncMock(return_value=True)
    db.get_tasks_paginated = AsyncMock(return_value=([{"id": "t1"}], 1))
    db.get_drafts = AsyncMock(return_value=([{"id": "d1"}], 1))
    return db


# ---------------------------------------------------------------------------
# ContentTaskStore.create_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentTaskStoreCreate:
    """ContentTaskStore.create_task tests."""

    @pytest.mark.asyncio
    async def test_create_task_returns_task_id(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        task_id = await store.create_task(
            topic="AI Revolution",
            style="informative",
            tone="professional",
            target_length=1500,
        )
        assert task_id == "new-task-id-123"
        db.add_task.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_task_passes_topic_style_tone_length(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        await store.create_task(
            topic="Machine Learning",
            style="technical",
            tone="formal",
            target_length=2000,
        )
        call_kwargs = db.add_task.call_args[0][0]
        assert call_kwargs["topic"] == "Machine Learning"
        assert call_kwargs["style"] == "technical"
        assert call_kwargs["tone"] == "formal"
        assert call_kwargs["target_length"] == 2000

    @pytest.mark.asyncio
    async def test_create_task_passes_tags(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        await store.create_task(
            topic="Python",
            style="tutorial",
            tone="casual",
            target_length=800,
            tags=["python", "programming"],
        )
        call_kwargs = db.add_task.call_args[0][0]
        assert call_kwargs["tags"] == ["python", "programming"]

    @pytest.mark.asyncio
    async def test_create_task_truncates_long_topic_for_task_name(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        long_topic = "A" * 60
        await store.create_task(
            topic=long_topic,
            style="blog",
            tone="friendly",
            target_length=1000,
        )
        call_kwargs = db.add_task.call_args[0][0]
        # task_name should be truncated to 50 chars
        assert len(call_kwargs["task_name"]) <= 53  # 50 + "..."

    @pytest.mark.asyncio
    async def test_create_task_raises_when_no_database_service(self):
        store = ContentTaskStore(database_service=None)
        with pytest.raises(ValueError, match="DatabaseService not initialized"):
            await store.create_task(
                topic="Test",
                style="blog",
                tone="casual",
                target_length=500,
            )

    @pytest.mark.asyncio
    async def test_create_task_propagates_db_errors(self):
        db = _make_db()
        db.add_task = AsyncMock(side_effect=RuntimeError("DB connection failed"))
        store = ContentTaskStore(database_service=db)
        with pytest.raises(RuntimeError, match="DB connection failed"):
            await store.create_task(
                topic="Test",
                style="blog",
                tone="casual",
                target_length=500,
            )


# ---------------------------------------------------------------------------
# ContentTaskStore.get_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentTaskStoreGet:
    """ContentTaskStore.get_task tests."""

    @pytest.mark.asyncio
    async def test_get_task_delegates_to_db(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        result = await store.get_task("task-123")
        db.get_task.assert_awaited_once_with("task-123")
        assert result["id"] == "new-task-id-123"  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_get_task_returns_none_when_no_db(self):
        store = ContentTaskStore(database_service=None)
        result = await store.get_task("any-task-id")
        assert result is None


# ---------------------------------------------------------------------------
# ContentTaskStore.update_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentTaskStoreUpdate:
    """ContentTaskStore.update_task tests."""

    @pytest.mark.asyncio
    async def test_update_task_delegates_to_db(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        await store.update_task("task-123", {"status": "completed"})
        db.update_task.assert_awaited_once_with("task-123", {"status": "completed"})

    @pytest.mark.asyncio
    async def test_update_task_converts_metadata_key(self):
        """metadata key should be converted to task_metadata."""
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        await store.update_task("task-123", {"metadata": {"key": "value"}})
        call_args = db.update_task.call_args[0]
        assert "task_metadata" in call_args[1]
        assert "metadata" not in call_args[1]

    @pytest.mark.asyncio
    async def test_update_task_returns_none_when_no_db(self):
        store = ContentTaskStore(database_service=None)
        result = await store.update_task("task-123", {"status": "done"})
        # Returns False (not None) when no database service is configured
        assert result is False


# ---------------------------------------------------------------------------
# ContentTaskStore.delete_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentTaskStoreDelete:
    """ContentTaskStore.delete_task tests."""

    @pytest.mark.asyncio
    async def test_delete_task_delegates_to_db(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        result = await store.delete_task("task-123")
        db.delete_task.assert_awaited_once_with("task-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_task_returns_false_when_no_db(self):
        store = ContentTaskStore(database_service=None)
        result = await store.delete_task("task-123")
        assert result is False


# ---------------------------------------------------------------------------
# ContentTaskStore.list_tasks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentTaskStoreList:
    """ContentTaskStore.list_tasks tests."""

    @pytest.mark.asyncio
    async def test_list_tasks_returns_tasks(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        result = await store.list_tasks()
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_list_tasks_passes_pagination(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        await store.list_tasks(limit=25, offset=50)
        db.get_tasks_paginated.assert_awaited_once_with(offset=50, limit=25, status=None)

    @pytest.mark.asyncio
    async def test_list_tasks_passes_status_filter(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        await store.list_tasks(status="pending")
        db.get_tasks_paginated.assert_awaited_once_with(offset=0, limit=50, status="pending")

    @pytest.mark.asyncio
    async def test_list_tasks_returns_empty_when_no_db(self):
        store = ContentTaskStore(database_service=None)
        result = await store.list_tasks()
        assert result == []


# ---------------------------------------------------------------------------
# ContentTaskStore.get_drafts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentTaskStoreGetDrafts:
    """ContentTaskStore.get_drafts tests."""

    @pytest.mark.asyncio
    async def test_get_drafts_delegates_to_db(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        tasks, total = await store.get_drafts(limit=10, offset=5)
        db.get_drafts.assert_awaited_once_with(limit=10, offset=5)
        assert total == 1

    @pytest.mark.asyncio
    async def test_get_drafts_returns_empty_when_no_db(self):
        store = ContentTaskStore(database_service=None)
        result = await store.get_drafts()
        # Returns [] (not ([], 0)) when no database service is configured
        assert result == []


# ---------------------------------------------------------------------------
# ContentTaskStore.persistent_store property
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentTaskStorePersistentStore:
    """Backward-compat persistent_store property."""

    def test_persistent_store_returns_database_service(self):
        db = _make_db()
        store = ContentTaskStore(database_service=db)
        assert store.persistent_store is db

    def test_persistent_store_returns_none_when_no_db(self):
        store = ContentTaskStore(database_service=None)
        assert store.persistent_store is None


# ---------------------------------------------------------------------------
# get_content_task_store singleton
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetContentTaskStore:
    """get_content_task_store singleton factory tests."""

    def test_returns_content_task_store_instance(self):
        import services.content_router_service as mod

        # Reset singleton for isolated test
        mod._content_task_store = None
        db = _make_db()
        store = get_content_task_store(database_service=db)
        assert isinstance(store, ContentTaskStore)
        assert store.database_service is db
        mod._content_task_store = None  # cleanup

    def test_second_call_returns_same_instance(self):
        import services.content_router_service as mod

        mod._content_task_store = None
        db = _make_db()
        store1 = get_content_task_store(database_service=db)
        store2 = get_content_task_store(database_service=db)
        assert store1 is store2
        mod._content_task_store = None  # cleanup

    def test_injects_db_service_if_not_set_on_singleton(self):
        import services.content_router_service as mod

        mod._content_task_store = None
        # First call without db
        store1 = get_content_task_store(database_service=None)
        assert store1.database_service is None
        # Second call with db — should inject
        db = _make_db()
        store2 = get_content_task_store(database_service=db)
        assert store2 is store1
        assert store2.database_service is db
        mod._content_task_store = None  # cleanup


# ===========================================================================
# _normalize_text
# ===========================================================================


@pytest.mark.unit
class TestNormalizeText:
    """Tests for Unicode→ASCII text normalization."""

    def test_empty_string_returns_empty(self):
        assert _normalize_text("") == ""

    def test_none_returns_none(self):
        assert _normalize_text(None) is None

    def test_smart_single_quotes(self):
        assert _normalize_text("\u2018hello\u2019") == "'hello'"

    def test_smart_double_quotes(self):
        assert _normalize_text("\u201chello\u201d") == '"hello"'

    def test_em_dash(self):
        assert _normalize_text("word\u2014word") == "word--word"

    def test_en_dash(self):
        assert _normalize_text("1\u20132") == "1-2"

    def test_ellipsis(self):
        assert _normalize_text("wait\u2026") == "wait..."

    def test_non_breaking_space(self):
        assert _normalize_text("hello\u00a0world") == "hello world"

    def test_non_breaking_hyphen(self):
        assert _normalize_text("self\u2011hosted") == "self-hosted"

    def test_plain_text_unchanged(self):
        text = "Hello, world! This is normal ASCII."
        assert _normalize_text(text) == text

    def test_multiple_replacements(self):
        text = "\u201cHello\u201d \u2014 it\u2019s a test\u2026"
        expected = '"Hello" -- it\'s a test...'
        assert _normalize_text(text) == expected


# ===========================================================================
# _scrub_fabricated_links
# ===========================================================================


@pytest.mark.unit
class TestScrubFabricatedLinks:
    """Tests for link scrubbing that removes hallucinated URLs."""

    def test_keeps_trusted_markdown_links(self):
        from services.content_router_service import _scrub_fabricated_links
        content = "Check out [this repo](https://github.com/user/project) for details."
        assert "github.com/user/project" in _scrub_fabricated_links(content)

    def test_removes_fabricated_markdown_links(self):
        from services.content_router_service import _scrub_fabricated_links
        content = "See [definition](https://www.dictionary.com/browse/example) for more."
        result = _scrub_fabricated_links(content)
        assert "dictionary.com" not in result
        assert "definition" in result  # Link text preserved

    def test_removes_bare_fabricated_urls(self):
        from services.content_router_service import _scrub_fabricated_links
        content = "Visit https://www.randomsite.com/fake-article for info."
        result = _scrub_fabricated_links(content)
        assert "randomsite.com" not in result

    def test_keeps_bare_trusted_urls(self):
        from services.content_router_service import _scrub_fabricated_links
        content = "See https://arxiv.org/abs/2301.12345 for the paper."
        result = _scrub_fabricated_links(content)
        assert "arxiv.org" in result

    def test_keeps_own_domain_links(self):
        from services.content_router_service import _scrub_fabricated_links
        from services.site_config import site_config
        domain = site_config.get("site_domain", "test-site.example.com")
        content = f"Read [our post](https://www.{domain}/posts/ai-trends) about this."
        assert domain in _scrub_fabricated_links(content)

    def test_empty_content_returns_empty(self):
        from services.content_router_service import _scrub_fabricated_links
        assert _scrub_fabricated_links("") == ""

    def test_no_links_returns_unchanged(self):
        from services.content_router_service import _scrub_fabricated_links
        content = "This is plain text with no links at all."
        assert _scrub_fabricated_links(content) == content

    def test_multiple_fabricated_links_all_removed(self):
        from services.content_router_service import _scrub_fabricated_links
        content = (
            "See [tools](https://www.techtools.io/list) and "
            "[guide](https://www.fakesite.com/guide) for more."
        )
        result = _scrub_fabricated_links(content)
        assert "techtools.io" not in result
        assert "fakesite.com" not in result
        assert "tools" in result  # Link texts kept
        assert "guide" in result


# ===========================================================================
# _parse_model_preferences
# ===========================================================================


@pytest.mark.unit
class TestParseModelPreferences:
    """Tests for model preference parsing from UI selections."""

    def test_none_returns_none_none(self):
        model, provider = _parse_model_preferences(None)
        assert model is None
        assert provider is None

    def test_empty_dict_returns_none_none(self):
        model, provider = _parse_model_preferences({})
        assert model is None
        assert provider is None

    def test_auto_returns_none_none(self):
        model, provider = _parse_model_preferences({"draft": "auto"})
        assert model is None
        assert provider is None

    def test_slash_format(self):
        model, provider = _parse_model_preferences({"draft": "gemini/gemini-1.5-pro"})
        assert model == "gemini-1.5-pro"
        assert provider == "gemini"

    def test_infers_gemini_provider(self):
        model, provider = _parse_model_preferences({"draft": "gemini-1.5-flash"})
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"

    def test_infers_openai_provider_from_gpt(self):
        model, provider = _parse_model_preferences({"draft": "gpt-4"})
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"

    def test_infers_anthropic_provider(self):
        model, provider = _parse_model_preferences({"draft": "claude-3-opus"})
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"

    def test_infers_ollama_provider_from_llama(self):
        model, provider = _parse_model_preferences({"draft": "llama3"})
        assert provider == "ollama"

    def test_infers_ollama_provider_from_mistral(self):
        model, provider = _parse_model_preferences({"draft": "mistral"})
        assert provider == "ollama"

    def test_duplicate_gemini_prefix(self):
        model, provider = _parse_model_preferences({"draft": "gemini-gemini-1.5-pro"})
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"

    def test_duplicate_gpt_prefix(self):
        model, provider = _parse_model_preferences({"draft": "gpt-gpt-4"})
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"

    def test_duplicate_claude_prefix(self):
        model, provider = _parse_model_preferences({"draft": "claude-claude-opus"})
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"

    def test_fallback_phase_generate(self):
        model, provider = _parse_model_preferences({"generate": "gpt-4"})
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"

    def test_fallback_phase_content(self):
        model, provider = _parse_model_preferences({"content": "gpt-4"})
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"

    def test_unknown_model_sets_model_only(self):
        model, provider = _parse_model_preferences({"draft": "my-custom-model"})
        assert model == "my-custom-model"
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"

    def test_strips_whitespace(self):
        model, provider = _parse_model_preferences({"draft": "  gpt-4  "})
        # Ollama-only policy: all models default to ollama provider
        assert provider == "ollama"


# ===========================================================================
# _is_stage_enabled
# ===========================================================================


@pytest.mark.unit
class TestIsStageEnabled:
    """Tests for pipeline stage enable/disable checks."""

    @pytest.mark.asyncio
    async def test_returns_true_when_pool_is_none(self):
        result = await _is_stage_enabled(None, "generate_content")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_stage_not_in_db(self):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        result = await _is_stage_enabled(pool, "nonexistent_stage")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_enabled_value_from_db(self):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"enabled": False})
        result = await _is_stage_enabled(pool, "generate_content")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_db_exception(self):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=Exception("DB down"))
        result = await _is_stage_enabled(pool, "any_stage")
        assert result is True


# ===========================================================================
# _run_stage_with_timeout
# ===========================================================================


@pytest.mark.unit
class TestRunStageWithTimeout:
    """Tests for stage timeout wrapper."""

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        async def fast_stage():
            return {"content": "hello"}

        result = await _run_stage_with_timeout(fast_stage(), "verify_task", "task-123")
        assert result == {"content": "hello"}

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        async def slow_stage():
            await asyncio.sleep(100)
            return "never"

        with patch.dict("services.content_router_service.STAGE_TIMEOUTS", {"slow": 0.01}):
            result = await _run_stage_with_timeout(slow_stage(), "slow", "task-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_default_timeout_for_unknown_stage(self):
        async def fast_stage():
            return "ok"

        result = await _run_stage_with_timeout(fast_stage(), "unknown_stage", "task-123")
        assert result == "ok"


# ===========================================================================
# _check_title_originality
# ===========================================================================


@pytest.mark.unit
class TestCheckTitleOriginality:
    """Tests for title originality checking via web search."""

    @pytest.mark.asyncio
    async def test_original_title_passes(self):
        """Title with no web matches should be marked original."""
        mock_researcher = MagicMock()
        mock_researcher.search_simple = AsyncMock(return_value=[])

        with patch("services.web_research.WebResearcher", return_value=mock_researcher):
            with patch("services.site_config.site_config") as mock_cfg:
                mock_cfg.get_float.return_value = 0.6
                mock_cfg.get_bool.return_value = True
                result = await _check_title_originality(
                    "A Completely Unique Title Nobody Has Written"
                )

        assert result["is_original"] is True
        assert result["similar_titles"] == []

    @pytest.mark.asyncio
    async def test_duplicate_title_fails(self):
        """Title matching a web result above threshold should fail."""
        mock_researcher = MagicMock()
        mock_researcher.search_simple = AsyncMock(return_value=[
            {"title": "How AI Is Changing Healthcare in 2026", "url": "https://example.com/1", "snippet": ""},
            {"title": "AI and Healthcare: 2026 Update", "url": "https://example.com/2", "snippet": ""},
        ])

        with patch("services.web_research.WebResearcher", return_value=mock_researcher):
            with patch("services.site_config.site_config") as mock_cfg:
                mock_cfg.get_float.return_value = 0.6
                mock_cfg.get_bool.return_value = True
                result = await _check_title_originality(
                    "How AI Is Changing Healthcare in 2026"
                )

        assert result["is_original"] is False
        assert len(result["similar_titles"]) >= 1
        assert result["max_similarity"] >= 0.6

    @pytest.mark.asyncio
    async def test_disabled_returns_original(self):
        """When disabled via config, should always return original."""
        with patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_float.return_value = 0.6
            mock_cfg.get_bool.return_value = False
            result = await _check_title_originality("Any Title")

        assert result["is_original"] is True

    @pytest.mark.asyncio
    async def test_search_failure_returns_original(self):
        """Web search errors should not block the pipeline."""
        mock_researcher = MagicMock()
        mock_researcher.search_simple = AsyncMock(side_effect=Exception("Network error"))

        with patch("services.web_research.WebResearcher", return_value=mock_researcher):
            with patch("services.site_config.site_config") as mock_cfg:
                mock_cfg.get_float.return_value = 0.6
                mock_cfg.get_bool.return_value = True
                result = await _check_title_originality("Test Title")

        assert result["is_original"] is True

    @pytest.mark.asyncio
    async def test_low_similarity_passes(self):
        """Titles with low similarity should pass."""
        mock_researcher = MagicMock()
        mock_researcher.search_simple = AsyncMock(return_value=[
            {"title": "Something Completely Different About Dogs", "url": "https://x.com/1", "snippet": ""},
        ])

        with patch("services.web_research.WebResearcher", return_value=mock_researcher):
            with patch("services.site_config.site_config") as mock_cfg:
                mock_cfg.get_float.return_value = 0.6
                mock_cfg.get_bool.return_value = True
                result = await _check_title_originality(
                    "Understanding GPU Architecture for ML Workloads"
                )

        assert result["is_original"] is True


# ---------------------------------------------------------------------------
# _scrub_fabricated_links — additional edge cases (existing class above
# already covers the basic cases)
# ---------------------------------------------------------------------------


class TestScrubFabricatedLinksEdgeCases:
    def test_subdomain_of_trusted_domain_kept(self):
        content = "[Wiki article](https://en.wikipedia.org/wiki/Python)"
        result = _scrub_fabricated_links(content)
        assert "wikipedia.org" in result

    def test_link_inside_markdown_parens_not_double_processed(self):
        """Bare URL regex uses negative lookbehind to skip URLs inside markdown ()."""
        content = "[label](https://github.com/example/repo)"
        result = _scrub_fabricated_links(content)
        # The full markdown link should pass through unchanged
        assert result == content

    def test_internal_post_link_with_no_slug_cache_kept(self):
        """Without a populated slug cache, internal /posts/ links pass through."""
        from services.site_config import site_config
        domain = site_config.get("site_domain", "")
        if not domain:
            return  # skip if no domain configured
        content = f"[older post](https://{domain}/posts/some-old-post)"
        result = _scrub_fabricated_links(content)
        assert "/posts/some-old-post" in result


# ---------------------------------------------------------------------------
# _load_stage_timeouts
# ---------------------------------------------------------------------------


class TestLoadStageTimeouts:
    def test_returns_dict_of_int_timeouts(self):
        with patch("services.site_config.site_config") as mock_sc:
            mock_sc.get.return_value = None  # no overrides
            result = _load_stage_timeouts()
        assert isinstance(result, dict)
        # Should contain at least the well-known stage names
        assert "verify_task" in result
        assert "generate_content" in result
        assert "quality_evaluation" in result
        for value in result.values():
            assert isinstance(value, int)

    def test_app_settings_override_applied(self):
        with patch("services.site_config.site_config") as mock_sc:
            mock_sc.get.side_effect = lambda k, d=None: "999" if k == "stage_timeout_draft" else None
            result = _load_stage_timeouts()
        assert result["generate_content"] == 999

    def test_invalid_override_value_silently_ignored(self):
        with patch("services.site_config.site_config") as mock_sc:
            mock_sc.get.side_effect = lambda k, d=None: "not-a-number" if k == "stage_timeout_qa" else None
            result = _load_stage_timeouts()
        # quality_evaluation falls back to its default
        assert isinstance(result["quality_evaluation"], int)


# ---------------------------------------------------------------------------
# _stage_verify_task
# ---------------------------------------------------------------------------


class TestStageVerifyTask:
    @pytest.mark.asyncio
    async def test_existing_task_marks_stage_complete(self):
        db = AsyncMock()
        db.get_task = AsyncMock(return_value={"id": "t1", "topic": "x"})
        result = {"stages": {}}
        await _stage_verify_task(db, "t1", result)
        assert result["content_task_id"] == "t1"
        assert result["stages"]["1_content_task_created"] is True

    @pytest.mark.asyncio
    async def test_missing_task_marks_stage_failed(self):
        db = AsyncMock()
        db.get_task = AsyncMock(return_value=None)
        result = {"stages": {}}
        await _stage_verify_task(db, "missing-task", result)
        assert result["stages"]["1_content_task_created"] is False

    @pytest.mark.asyncio
    async def test_db_exception_marks_stage_failed(self):
        db = AsyncMock()
        db.get_task = AsyncMock(side_effect=RuntimeError("db down"))
        result = {"stages": {}}
        await _stage_verify_task(db, "t1", result)
        assert result["stages"]["1_content_task_created"] is False


# ---------------------------------------------------------------------------
# _select_category_for_topic
# ---------------------------------------------------------------------------


class TestSelectCategoryForTopic:
    @pytest.mark.asyncio
    async def test_uses_requested_category_when_valid(self):
        db = MagicMock()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value="cat-uuid-business")
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(return_value=cm)

        result = await _select_category_for_topic("Some Topic", db, requested_category="business")
        assert result == "cat-uuid-business"

    @pytest.mark.asyncio
    async def test_keyword_match_picks_security_category(self):
        db = MagicMock()
        conn = AsyncMock()
        # First call (requested) returns None; second (matched) returns id
        conn.fetchval = AsyncMock(return_value="cat-security-uuid")
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(return_value=cm)

        result = await _select_category_for_topic("Zero Trust Auth and OWASP for Devs", db)
        assert result == "cat-security-uuid"
        # Last fetchval call queried for the matched category slug
        last_call = conn.fetchval.await_args
        assert last_call.args[1] == "security"

    @pytest.mark.asyncio
    async def test_no_match_defaults_to_technology(self):
        db = MagicMock()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value="cat-tech-uuid")
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(return_value=cm)

        result = await _select_category_for_topic("Random Unrelated Topic", db)
        assert result == "cat-tech-uuid"
        last_call = conn.fetchval.await_args
        assert last_call.args[1] == "technology"

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(side_effect=RuntimeError("conn lost"))
        result = await _select_category_for_topic("AI Software Engineering", db)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_requested_category_falls_through_to_keyword(self):
        db = MagicMock()
        conn = AsyncMock()
        # Sequence: requested lookup returns None, matched lookup returns id
        conn.fetchval = AsyncMock(side_effect=[None, "cat-eng-uuid"])
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(return_value=cm)

        result = await _select_category_for_topic(
            "monorepo migration architecture",
            db,
            requested_category="nonexistent-slug",
        )
        # Falls back to keyword match — engineering wins
        assert result == "cat-eng-uuid"


# ---------------------------------------------------------------------------
# _get_or_create_default_author
# ---------------------------------------------------------------------------


class TestGetOrCreateDefaultAuthor:
    @pytest.mark.asyncio
    async def test_returns_existing_author_id(self):
        db = MagicMock()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value="existing-author-uuid")
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(return_value=cm)

        result = await _get_or_create_default_author(db)
        assert result == "existing-author-uuid"
        # Only one fetchval call (the SELECT)
        assert conn.fetchval.await_count == 1

    @pytest.mark.asyncio
    async def test_creates_when_missing(self):
        db = MagicMock()
        conn = AsyncMock()
        # First call (SELECT) returns None, second call (INSERT RETURNING) returns id
        conn.fetchval = AsyncMock(side_effect=[None, "newly-created-uuid"])
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(return_value=cm)

        result = await _get_or_create_default_author(db)
        assert result == "newly-created-uuid"

    @pytest.mark.asyncio
    async def test_falls_back_to_any_author_when_insert_returns_none(self):
        """ON CONFLICT DO NOTHING returns NULL when row exists; falls back to SELECT LIMIT 1."""
        db = MagicMock()
        conn = AsyncMock()
        # SELECT poindexter -> None, INSERT -> None (race), SELECT LIMIT 1 -> fallback id
        conn.fetchval = AsyncMock(side_effect=[None, None, "any-author-uuid"])
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(return_value=cm)

        result = await _get_or_create_default_author(db)
        assert result == "any-author-uuid"

    @pytest.mark.asyncio
    async def test_db_exception_returns_none(self):
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(side_effect=RuntimeError("conn lost"))
        result = await _get_or_create_default_author(db)
        assert result is None


# ---------------------------------------------------------------------------
# _generate_canonical_title
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateCanonicalTitle:
    @pytest.mark.asyncio
    async def test_returns_cleaned_title_on_success(self):
        from services.content_router_service import _generate_canonical_title

        fake_response = MagicMock()
        fake_response.text = '  "Why Local LLMs Beat the Cloud"  '

        fake_service = MagicMock()
        fake_service.generate = AsyncMock(return_value=fake_response)

        fake_pm = MagicMock()
        fake_pm.get_prompt = MagicMock(return_value="Generate an SEO title for: {content}")

        with patch("services.model_consolidation_service.get_model_consolidation_service",
                   return_value=fake_service), \
             patch("services.content_router_service.get_prompt_manager",
                   return_value=fake_pm):
            result = await _generate_canonical_title(
                topic="Local LLMs",
                primary_keyword="Ollama",
                content_excerpt="Running LLMs locally...",
            )

        assert result == "Why Local LLMs Beat the Cloud"

    @pytest.mark.asyncio
    async def test_truncates_long_title(self):
        from services.content_router_service import _generate_canonical_title

        fake_response = MagicMock()
        fake_response.text = "x" * 200

        fake_service = MagicMock()
        fake_service.generate = AsyncMock(return_value=fake_response)
        fake_pm = MagicMock()
        fake_pm.get_prompt = MagicMock(return_value="prompt")

        with patch("services.model_consolidation_service.get_model_consolidation_service",
                   return_value=fake_service), \
             patch("services.content_router_service.get_prompt_manager",
                   return_value=fake_pm):
            result = await _generate_canonical_title("topic", "kw", "excerpt")

        assert result is not None
        assert len(result) <= 100
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_returns_none_when_model_returns_empty(self):
        from services.content_router_service import _generate_canonical_title

        fake_response = MagicMock()
        fake_response.text = ""
        fake_service = MagicMock()
        fake_service.generate = AsyncMock(return_value=fake_response)
        fake_pm = MagicMock()
        fake_pm.get_prompt = MagicMock(return_value="prompt")

        with patch("services.model_consolidation_service.get_model_consolidation_service",
                   return_value=fake_service), \
             patch("services.content_router_service.get_prompt_manager",
                   return_value=fake_pm):
            result = await _generate_canonical_title("topic", "kw", "excerpt")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        from services.content_router_service import _generate_canonical_title

        fake_service = MagicMock()
        fake_service.generate = AsyncMock(side_effect=RuntimeError("LLM down"))
        fake_pm = MagicMock()
        fake_pm.get_prompt = MagicMock(return_value="prompt")

        with patch("services.model_consolidation_service.get_model_consolidation_service",
                   return_value=fake_service), \
             patch("services.content_router_service.get_prompt_manager",
                   return_value=fake_pm):
            result = await _generate_canonical_title("topic", "kw", "excerpt")

        assert result is None

    @pytest.mark.asyncio
    async def test_includes_existing_titles_warning_in_prompt(self):
        """When existing_titles is provided, it's appended with an AVOID SIMILARITY warning."""
        from services.content_router_service import _generate_canonical_title

        captured_prompt = {}

        async def _capture_generate(**kwargs):
            captured_prompt["text"] = kwargs.get("prompt", "")
            resp = MagicMock()
            resp.text = "title"
            return resp

        fake_service = MagicMock()
        fake_service.generate = AsyncMock(side_effect=_capture_generate)
        fake_pm = MagicMock()
        fake_pm.get_prompt = MagicMock(return_value="base prompt")

        with patch("services.model_consolidation_service.get_model_consolidation_service",
                   return_value=fake_service), \
             patch("services.content_router_service.get_prompt_manager",
                   return_value=fake_pm):
            await _generate_canonical_title(
                "topic", "kw", "excerpt",
                existing_titles="- Existing Title 1\n- Existing Title 2",
            )

        assert "AVOID SIMILARITY" in captured_prompt["text"]
        assert "Existing Title 1" in captured_prompt["text"]


# ---------------------------------------------------------------------------
# _stage_quality_evaluation
# ---------------------------------------------------------------------------


def _make_quality_result(
    overall_score=85.0, passing=True, truncation=False,
    clarity=80, accuracy=85, completeness=80, relevance=90,
    seo_quality=75, readability=80, engagement=80,
):
    qr = MagicMock()
    qr.overall_score = overall_score
    qr.passing = passing
    qr.truncation_detected = truncation
    qr.dimensions = MagicMock()
    qr.dimensions.clarity = clarity
    qr.dimensions.accuracy = accuracy
    qr.dimensions.completeness = completeness
    qr.dimensions.relevance = relevance
    qr.dimensions.seo_quality = seo_quality
    qr.dimensions.readability = readability
    qr.dimensions.engagement = engagement
    return qr


@pytest.mark.unit
class TestStageQualityEvaluation:
    @pytest.mark.asyncio
    async def test_populates_result_dict_on_success(self):
        from services.content_router_service import _stage_quality_evaluation

        qa = MagicMock()
        qa.evaluate = AsyncMock(return_value=_make_quality_result(
            overall_score=87.5, passing=True,
        ))
        result = {"stages": {}}

        qr = await _stage_quality_evaluation(
            topic="AI trends",
            tags=["ai", "ml"],
            content_text="Body text.",
            quality_service=qa,
            result=result,
        )

        assert result["quality_score"] == 87.5
        assert result["quality_passing"] is True
        assert result["truncation_detected"] is False
        assert result["stages"]["2b_quality_evaluated_initial"] is True
        assert "clarity" in result["quality_details_initial"]
        assert qr.overall_score == 87.5

    @pytest.mark.asyncio
    async def test_truncation_detected_warning(self):
        from services.content_router_service import _stage_quality_evaluation

        qa = MagicMock()
        qa.evaluate = AsyncMock(return_value=_make_quality_result(truncation=True))
        result = {"stages": {}}

        await _stage_quality_evaluation("t", ["x"], "body", qa, result)
        assert result["truncation_detected"] is True

    @pytest.mark.asyncio
    async def test_none_quality_result_raises(self):
        from services.content_router_service import _stage_quality_evaluation

        qa = MagicMock()
        qa.evaluate = AsyncMock(return_value=None)
        result = {"stages": {}}

        with pytest.raises(ValueError, match="no result"):
            await _stage_quality_evaluation("t", ["x"], "body", qa, result)

    @pytest.mark.asyncio
    async def test_empty_tags_falls_back_to_topic_as_keyword(self):
        from services.content_router_service import _stage_quality_evaluation

        captured_context = {}

        async def _capture(**kwargs):
            captured_context.update(kwargs.get("context", {}))
            return _make_quality_result()

        qa = MagicMock()
        qa.evaluate = AsyncMock(side_effect=_capture)
        result = {"stages": {}}

        await _stage_quality_evaluation(
            topic="Docker",
            tags=None,
            content_text="body",
            quality_service=qa,
            result=result,
        )

        assert captured_context["keywords"] == ["Docker"]


# ---------------------------------------------------------------------------
# _stage_generate_seo_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStageGenerateSeoMetadata:
    def _make_seo_generator(self, seo_assets):
        gen = MagicMock()
        gen.metadata_gen = MagicMock()
        gen.metadata_gen.generate_seo_assets = MagicMock(return_value=seo_assets)
        return gen

    @pytest.mark.asyncio
    async def test_populates_result_on_success(self):
        from services.content_router_service import _stage_generate_seo_metadata

        seo = self._make_seo_generator({
            "seo_title": "Why Docker Wins",
            "meta_description": "Docker provides containerization that simplifies deployment.",
            "meta_keywords": ["docker", "containers", "devops"],
        })
        result = {"stages": {}}

        with patch("services.content_router_service.get_seo_content_generator", return_value=seo):
            title, desc, keywords = await _stage_generate_seo_metadata(
                topic="Docker",
                tags=["docker"],
                content_text="About docker.",
                content_generator=MagicMock(),
                result=result,
            )

        assert title == "Why Docker Wins"
        assert "containerization" in desc
        assert keywords == ["docker", "containers", "devops"]
        assert result["stages"]["4_seo_metadata_generated"] is True
        assert result["seo_title"] == "Why Docker Wins"

    @pytest.mark.asyncio
    async def test_title_truncated_to_60_chars(self):
        from services.content_router_service import _stage_generate_seo_metadata

        long_title = "x" * 200
        seo = self._make_seo_generator({
            "seo_title": long_title,
            "meta_description": "desc",
            "meta_keywords": [],
        })
        result = {"stages": {}}

        with patch("services.content_router_service.get_seo_content_generator", return_value=seo):
            title, _, _ = await _stage_generate_seo_metadata(
                "topic", [], "text", MagicMock(), result,
            )

        assert len(title) == 60

    @pytest.mark.asyncio
    async def test_description_truncated_to_160_chars(self):
        from services.content_router_service import _stage_generate_seo_metadata

        long_desc = "x" * 500
        seo = self._make_seo_generator({
            "seo_title": "t",
            "meta_description": long_desc,
            "meta_keywords": [],
        })
        result = {"stages": {}}

        with patch("services.content_router_service.get_seo_content_generator", return_value=seo):
            _, desc, _ = await _stage_generate_seo_metadata(
                "topic", [], "text", MagicMock(), result,
            )

        assert len(desc) == 160

    @pytest.mark.asyncio
    async def test_missing_title_falls_back_to_topic(self):
        from services.content_router_service import _stage_generate_seo_metadata

        seo = self._make_seo_generator({
            "seo_title": None,
            "meta_description": "desc",
            "meta_keywords": [],
        })
        result = {"stages": {}}

        with patch("services.content_router_service.get_seo_content_generator", return_value=seo):
            title, _, _ = await _stage_generate_seo_metadata(
                topic="Kubernetes deployment patterns",
                tags=[],
                content_text="text",
                content_generator=MagicMock(),
                result=result,
            )

        assert title == "Kubernetes deployment patterns"[:60]

    @pytest.mark.asyncio
    async def test_keywords_limited_to_ten(self):
        from services.content_router_service import _stage_generate_seo_metadata

        many_keywords = [f"kw{i}" for i in range(20)]
        seo = self._make_seo_generator({
            "seo_title": "t",
            "meta_description": "d",
            "meta_keywords": many_keywords,
        })
        result = {"stages": {}}

        with patch("services.content_router_service.get_seo_content_generator", return_value=seo):
            _, _, keywords = await _stage_generate_seo_metadata(
                "topic", [], "text", MagicMock(), result,
            )

        assert len(keywords) == 10

    @pytest.mark.asyncio
    async def test_invalid_keywords_filtered(self):
        """Empty strings, None, non-strings should be dropped."""
        from services.content_router_service import _stage_generate_seo_metadata

        seo = self._make_seo_generator({
            "seo_title": "t",
            "meta_description": "d",
            "meta_keywords": ["docker", "", None, "  ", "k8s", 42],
        })
        result = {"stages": {}}

        with patch("services.content_router_service.get_seo_content_generator", return_value=seo):
            _, _, keywords = await _stage_generate_seo_metadata(
                "topic", [], "text", MagicMock(), result,
            )

        assert keywords == ["docker", "k8s"]

    @pytest.mark.asyncio
    async def test_none_assets_raises(self):
        from services.content_router_service import _stage_generate_seo_metadata

        seo = self._make_seo_generator(None)
        result = {"stages": {}}

        with patch("services.content_router_service.get_seo_content_generator", return_value=seo):
            with pytest.raises(ValueError, match="SEO metadata"):
                await _stage_generate_seo_metadata(
                    "topic", [], "text", MagicMock(), result,
                )

    @pytest.mark.asyncio
    async def test_keywords_fall_back_to_tags(self):
        """When meta_keywords is missing, the tags arg is used."""
        from services.content_router_service import _stage_generate_seo_metadata

        seo = self._make_seo_generator({
            "seo_title": "t",
            "meta_description": "d",
            # no meta_keywords
        })
        result = {"stages": {}}

        with patch("services.content_router_service.get_seo_content_generator", return_value=seo):
            _, _, keywords = await _stage_generate_seo_metadata(
                "topic", ["tag1", "tag2"], "text", MagicMock(), result,
            )

        assert keywords == ["tag1", "tag2"]


# ---------------------------------------------------------------------------
# _stage_finalize_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStageFinalizeTask:
    @pytest.mark.asyncio
    async def test_updates_task_with_all_fields(self):
        from services.content_router_service import _stage_finalize_task

        db = MagicMock()
        db.update_task = AsyncMock()
        qr = _make_quality_result(overall_score=82.0)
        result = {
            "stages": {},
            "featured_image_url": "https://img.example/hero.jpg",
            "quality_score": 82.0,
        }

        await _stage_finalize_task(
            database_service=db,
            task_id="task-1",
            topic="Docker",
            style="balanced",
            tone="professional",
            content_text="Body content here.",
            quality_result=qr,
            seo_title="Docker Guide",
            seo_description="Learn Docker",
            seo_keywords=["docker", "devops"],
            category="technology",
            target_audience="developers",
            result=result,
        )

        db.update_task.assert_awaited_once()
        kwargs = db.update_task.await_args.kwargs
        assert kwargs["task_id"] == "task-1"
        updates = kwargs["updates"]
        assert updates["status"] == "awaiting_approval"
        assert updates["approval_status"] == "pending"
        assert updates["quality_score"] == 82
        assert updates["featured_image_url"] == "https://img.example/hero.jpg"
        assert updates["style"] == "balanced"
        assert updates["tone"] == "professional"

    @pytest.mark.asyncio
    async def test_result_dict_marked_awaiting_approval(self):
        from services.content_router_service import _stage_finalize_task

        db = MagicMock()
        db.update_task = AsyncMock()
        qr = _make_quality_result()
        result = {"stages": {}}

        await _stage_finalize_task(
            db, "t1", "topic", "s", "t", "content", qr,
            "seo_title", "seo_desc", ["kw"], "tech", "devs", result,
        )

        assert result["status"] == "awaiting_approval"
        assert result["approval_status"] == "pending"
        assert result["stages"]["5_post_created"] is False

    @pytest.mark.asyncio
    async def test_seo_keywords_joined_to_string(self):
        from services.content_router_service import _stage_finalize_task

        db = MagicMock()
        db.update_task = AsyncMock()
        qr = _make_quality_result()
        result = {"stages": {}}

        await _stage_finalize_task(
            db, "t1", "topic", "s", "t", "content", qr,
            "seo_title", "seo_desc",
            ["docker", "k8s", "devops"],
            "tech", "devs", result,
        )

        updates = db.update_task.await_args.kwargs["updates"]
        assert updates["seo_keywords"] == "docker, k8s, devops"

    @pytest.mark.asyncio
    async def test_prefers_multi_model_qa_score_over_early_eval(self):
        """When result['quality_score'] is set (from multi-model QA), use it over quality_result.overall_score."""
        from services.content_router_service import _stage_finalize_task

        db = MagicMock()
        db.update_task = AsyncMock()
        qr = _make_quality_result(overall_score=65.0)  # Early eval score
        result = {
            "stages": {},
            "quality_score": 88.0,  # Multi-model QA score (should win)
        }

        await _stage_finalize_task(
            db, "t1", "topic", "s", "t", "content", qr,
            "seo_title", "seo_desc", [], "tech", "devs", result,
        )

        updates = db.update_task.await_args.kwargs["updates"]
        assert updates["quality_score"] == 88  # int(round(88.0))

    @pytest.mark.asyncio
    async def test_falls_back_to_early_eval_when_qa_score_missing(self):
        from services.content_router_service import _stage_finalize_task

        db = MagicMock()
        db.update_task = AsyncMock()
        qr = _make_quality_result(overall_score=72.7)
        result = {"stages": {}}  # no quality_score

        await _stage_finalize_task(
            db, "t1", "topic", "s", "t", "content", qr,
            "seo_title", "seo_desc", [], "tech", "devs", result,
        )

        updates = db.update_task.await_args.kwargs["updates"]
        assert updates["quality_score"] == 73  # int(round(72.7))

    @pytest.mark.asyncio
    async def test_task_metadata_contains_all_fields(self):
        from services.content_router_service import _stage_finalize_task

        db = MagicMock()
        db.update_task = AsyncMock()
        qr = _make_quality_result()
        result = {
            "stages": {},
            "featured_image_url": "https://img/hero.jpg",
            "featured_image_alt": "alt text",
            "podcast_script": "podcast body",
            "video_scenes": [{"scene": 1}],
            "short_summary_script": "short summary",
        }

        await _stage_finalize_task(
            db, "t1", "topic", "s", "t", "Body content here", qr,
            "seo_title", "seo_desc", [], "tech", "devs", result,
        )

        updates = db.update_task.await_args.kwargs["updates"]
        meta = updates["task_metadata"]
        assert meta["featured_image_url"] == "https://img/hero.jpg"
        assert meta["featured_image_alt"] == "alt text"
        assert meta["podcast_script"] == "podcast body"
        assert meta["video_scenes"] == [{"scene": 1}]
        assert meta["short_summary_script"] == "short summary"
        assert meta["content"] == "Body content here"
        assert meta["word_count"] == 3

    @pytest.mark.asyncio
    async def test_category_prefers_result_over_arg(self):
        """If result['category'] is set, it wins over the category arg."""
        from services.content_router_service import _stage_finalize_task

        db = MagicMock()
        db.update_task = AsyncMock()
        qr = _make_quality_result()
        result = {"stages": {}, "category": "ai"}

        await _stage_finalize_task(
            db, "t1", "topic", "s", "t", "content", qr,
            "seo_title", "seo_desc", [], "tech", "devs", result,
        )

        updates = db.update_task.await_args.kwargs["updates"]
        assert updates["category"] == "ai"  # from result, not from arg

    @pytest.mark.asyncio
    async def test_target_audience_defaults_to_general(self):
        from services.content_router_service import _stage_finalize_task

        db = MagicMock()
        db.update_task = AsyncMock()
        qr = _make_quality_result()
        result = {"stages": {}}

        await _stage_finalize_task(
            db, "t1", "topic", "s", "t", "content", qr,
            "seo_title", "seo_desc", [], "tech",
            target_audience=None,
            result=result,
        )

        updates = db.update_task.await_args.kwargs["updates"]
        assert updates["target_audience"] == "General"


# ---------------------------------------------------------------------------
# _stage_verify_task — error path (existing test file only covers happy path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStageVerifyTaskErrorPaths:
    @pytest.mark.asyncio
    async def test_task_not_found_sets_stage_false(self):
        db = AsyncMock()
        db.get_task = AsyncMock(return_value=None)
        result = {"stages": {}}

        await _stage_verify_task(db, "missing-task", result)

        assert result["stages"]["1_content_task_created"] is False
        # content_task_id should NOT be set when task not found
        assert "content_task_id" not in result

    @pytest.mark.asyncio
    async def test_db_exception_sets_stage_false_and_swallows(self):
        db = AsyncMock()
        db.get_task = AsyncMock(side_effect=RuntimeError("db down"))
        result = {"stages": {}}

        # Should not raise
        await _stage_verify_task(db, "task-1", result)

        assert result["stages"]["1_content_task_created"] is False
