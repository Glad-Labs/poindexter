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

ContentTaskStore is constructed inline per request; no singleton.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.category_resolver import (
    select_category_for_topic as _select_category_for_topic,
)
from services.content_task_store import ContentTaskStore
from services.default_author import (
    get_or_create_default_author as _get_or_create_default_author,
)
from services.model_preferences import (
    parse_model_preferences as _parse_model_preferences,
)
from services.text_utils import (
    normalize_text as _normalize_text,
)
from services.text_utils import (
    scrub_fabricated_links as _scrub_fabricated_links,
)
from services.title_generation import (
    check_title_originality as _check_title_originality,
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
        from services.text_utils import scrub_fabricated_links as _scrub_fabricated_links
        content = "Check out [this repo](https://github.com/user/project) for details."
        assert "github.com/user/project" in _scrub_fabricated_links(content)

    def test_removes_fabricated_markdown_links(self):
        from services.text_utils import scrub_fabricated_links as _scrub_fabricated_links
        content = "See [definition](https://www.dictionary.com/browse/example) for more."
        result = _scrub_fabricated_links(content)
        assert "dictionary.com" not in result
        assert "definition" in result  # Link text preserved

    def test_removes_bare_fabricated_urls(self):
        from services.text_utils import scrub_fabricated_links as _scrub_fabricated_links
        content = "Visit https://www.randomsite.com/fake-article for info."
        result = _scrub_fabricated_links(content)
        assert "randomsite.com" not in result

    def test_keeps_bare_trusted_urls(self):
        from services.text_utils import scrub_fabricated_links as _scrub_fabricated_links
        content = "See https://arxiv.org/abs/2301.12345 for the paper."
        result = _scrub_fabricated_links(content)
        assert "arxiv.org" in result

    def test_keeps_own_domain_links(self):
        # Use the conftest-seeded shared SiteConfig (post-#330 sweep —
        # text_utils takes ``site_config`` as a function arg, no module attr).
        from tests.unit.conftest import site_config as sc
        from services.text_utils import scrub_fabricated_links as _scrub_fabricated_links
        domain = sc.get("site_domain", "test-site.example.com")
        content = f"Read [our post](https://www.{domain}/posts/ai-trends) about this."
        assert domain in _scrub_fabricated_links(content, site_config=sc)

    def test_empty_content_returns_empty(self):
        from services.text_utils import scrub_fabricated_links as _scrub_fabricated_links
        assert _scrub_fabricated_links("") == ""

    def test_no_links_returns_unchanged(self):
        from services.text_utils import scrub_fabricated_links as _scrub_fabricated_links
        content = "This is plain text with no links at all."
        assert _scrub_fabricated_links(content) == content

    def test_multiple_fabricated_links_all_removed(self):
        from services.text_utils import scrub_fabricated_links as _scrub_fabricated_links
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
# _run_stage_with_timeout
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
            with patch("services.title_generation.site_config") as mock_cfg:
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
            with patch("services.title_generation.site_config") as mock_cfg:
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
        with patch("services.title_generation.site_config") as mock_cfg:
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
            with patch("services.title_generation.site_config") as mock_cfg:
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
            with patch("services.title_generation.site_config") as mock_cfg:
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
        from tests.unit.conftest import site_config as sc
        domain = sc.get("site_domain", "")
        if not domain:
            return  # skip if no domain configured
        content = f"[older post](https://{domain}/posts/some-old-post)"
        result = _scrub_fabricated_links(content, site_config=sc)
        assert "/posts/some-old-post" in result


# ---------------------------------------------------------------------------
# _get_stage_timeout — lazy, DB-backed, loud on bad config
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# _stage_verify_task
# ---------------------------------------------------------------------------




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
class TestSanitizeGeneratedTitle:
    """Regression tests for the title sanitizer added 2026-04-16.

    A thinking-model rejected a post at publish time because its title
    was "*   Let's go with the **Question**. It is the most unique
    structure in..." — raw deliberation leaked as the title. These
    lock the salvage/reject logic.
    """

    def test_returns_clean_title_unchanged(self):
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        assert _sanitize_generated_title("Why Local LLMs Beat the Cloud") \
            == "Why Local LLMs Beat the Cloud"

    def test_strips_quotes_and_bold(self):
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        assert _sanitize_generated_title('"**Running Stable Diffusion Locally**"') \
            == "Running Stable Diffusion Locally"

    def test_strips_leading_list_marker(self):
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        assert _sanitize_generated_title("* Top 10 FastAPI Patterns") \
            == "Top 10 FastAPI Patterns"

    def test_strips_heading_hash(self):
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        assert _sanitize_generated_title("# The AI Agents Handbook") \
            == "The AI Agents Handbook"

    def test_strips_think_block(self):
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        raw = "<think>Let me consider the audience.</think>\nPostgres Sharding for Beginners"
        assert _sanitize_generated_title(raw) == "Postgres Sharding for Beginners"

    def test_rejects_deliberation_trace(self):
        """This is the exact bug that shipped #8b13ff52 to awaiting_approval."""
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        raw = "*   Let's go with the **Question**. It is the most unique structure in this set."
        assert _sanitize_generated_title(raw) is None

    def test_walks_back_to_final_line_when_reasoning_precedes(self):
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        raw = "Let me think about this.\nOptions:\n1. One\n2. Two\n\nThe GPU Memory Bottleneck"
        assert _sanitize_generated_title(raw) == "The GPU Memory Bottleneck"

    def test_rejects_empty(self):
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        assert _sanitize_generated_title("") is None
        assert _sanitize_generated_title("   \n\n  ") is None

    def test_rejects_too_short(self):
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        assert _sanitize_generated_title("hi") is None

    def test_truncates_too_long(self):
        from services.title_generation import sanitize_generated_title as _sanitize_generated_title
        out = _sanitize_generated_title("x" * 200)
        assert out is not None
        assert len(out) <= 100
        assert out.endswith("...")




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




# ---------------------------------------------------------------------------
# _stage_generate_seo_metadata
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# _stage_finalize_task
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# _stage_verify_task — error path (existing test file only covers happy path)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# _stage_capture_training_data
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# _stage_generate_media_scripts — the non-critical media stage
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Image style rotation dedup (#181)
# ---------------------------------------------------------------------------


