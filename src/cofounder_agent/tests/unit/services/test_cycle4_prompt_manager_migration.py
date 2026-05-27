"""Pin the cycle-4 UnifiedPromptManager migrations.

Four inline prompts migrated to YAML+Langfuse per
``feedback_prompts_must_be_db_configurable``:

* ``services.social_poster._build_twitter_prompt`` →
  ``social.twitter_promote``
* ``services.social_poster._build_linkedin_prompt`` →
  ``social.linkedin_promote``
* ``services.quality_service._resolve_quality_prompt`` →
  ``qa.quality_evaluation_llm_rubric``
* ``services.jobs.collapse_old_embeddings._resolve_summary_prompt_template`` →
  ``memory.collapse_old_embeddings.summary``

Each resolver pulls from UnifiedPromptManager and falls back to the
inline constant on any lookup failure — same pattern as the cycle-3
migrations in #612.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_social_twitter_resolver_uses_prompt_manager():
    from services import social_poster

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value.get_prompt.return_value = "PM tweet"
        result = social_poster._resolve_social_prompt(
            "social.twitter_promote",
            fallback=social_poster._TWITTER_PROMPT_FALLBACK,
            company_name="Glad Labs",
            char_limit=280,
            title="t",
            excerpt="e",
            post_url="u",
            hashtags="#h",
        )
    assert result == "PM tweet"


@pytest.mark.unit
def test_social_twitter_resolver_falls_back_on_pm_failure():
    from services import social_poster

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=RuntimeError("pm broken"),
    ):
        result = social_poster._resolve_social_prompt(
            "social.twitter_promote",
            fallback=social_poster._TWITTER_PROMPT_FALLBACK,
            company_name="Glad Labs",
            char_limit=280,
            title="Title",
            excerpt="Excerpt",
            post_url="https://gladlabs.io/posts/x",
            hashtags="#a #b",
        )
    assert "Glad Labs" in result
    assert "280 characters" in result
    assert "Title" in result


@pytest.mark.unit
def test_social_linkedin_resolver_falls_back_on_pm_failure():
    from services import social_poster

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=RuntimeError("pm broken"),
    ):
        result = social_poster._resolve_social_prompt(
            "social.linkedin_promote",
            fallback=social_poster._LINKEDIN_PROMPT_FALLBACK,
            company_name="Glad Labs",
            char_limit=3000,
            title="Title",
            excerpt="Excerpt",
            post_url="https://gladlabs.io/posts/x",
            hashtags="#a #b",
        )
    assert "Glad Labs" in result
    assert "LinkedIn" in result
    assert "3000 characters" in result


@pytest.mark.unit
def test_quality_resolver_uses_prompt_manager():
    from services import quality_service

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value.get_prompt.return_value = "PM rubric"
        result = quality_service._resolve_quality_prompt(
            "qa.quality_evaluation_llm_rubric",
            topic="AI",
            content_excerpt="hello",
        )
    assert result == "PM rubric"


@pytest.mark.unit
def test_quality_resolver_falls_back_on_pm_failure():
    from services import quality_service

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=RuntimeError("pm broken"),
    ):
        result = quality_service._resolve_quality_prompt(
            "qa.quality_evaluation_llm_rubric",
            topic="AI",
            content_excerpt="hello world",
        )
    assert "content quality evaluator" in result
    assert "AI" in result
    assert "hello world" in result


@pytest.mark.unit
def test_collapse_resolver_returns_raw_template_from_prompt_manager():
    """The memory-collapse resolver returns the *raw* template so the
    caller fills {n}/{source_table}/{joined} downstream via
    build_summary_text_via_llm — same shape as the retention resolver
    in #612."""
    from services.jobs import collapse_old_embeddings

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value._fetch_from_langfuse.return_value = None
        mock_pm.return_value.prompts = {
            "memory.collapse_old_embeddings.summary": {
                "template": "PM RAW: {n}/{source_table}/{joined}",
            },
        }
        result = collapse_old_embeddings._resolve_summary_prompt_template()
    assert "{n}" in result
    assert "{source_table}" in result
    assert "{joined}" in result


@pytest.mark.unit
def test_collapse_resolver_falls_back_on_pm_failure():
    from services.jobs import collapse_old_embeddings

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=ImportError("module missing"),
    ):
        result = collapse_old_embeddings._resolve_summary_prompt_template()
    assert "compressing a cluster of older memories" in result
    assert "{n}" in result
    assert "{source_table}" in result
    assert "{joined}" in result
