"""Pin the cycle-4 UnifiedPromptManager migrations.

Three inline prompts migrated to YAML+Langfuse per
``feedback_prompts_must_be_db_configurable``:

* ``services.social_poster._build_twitter_prompt`` →
  ``social.twitter_promote``
* ``services.social_poster._build_linkedin_prompt`` →
  ``social.linkedin_promote``
* ``modules.content.quality_service._resolve_quality_prompt`` →
  ``qa.quality_evaluation_llm_rubric``

Note: ``memory.collapse_old_embeddings.summary`` was migrated from
``services.jobs.collapse_old_embeddings._resolve_summary_prompt_template``
— the job was retired 2026-06-24 (folded into retention_policies handler
``embeddings_collapse``). The handler uses the inline prompt constant
directly; the YAML key remains registered so the prompt can be tuned via
Langfuse if needed in the future.

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
    from modules.content import quality_service

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
    from modules.content import quality_service

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
def test_collapse_summary_prompt_constant_has_required_placeholders():
    """The inline summary prompt in the collapse handler must contain the
    three format placeholders used by build_summary_text_via_llm."""
    from services.integrations.handlers.retention_embeddings_collapse import (
        _DEFAULT_SUMMARY_PROMPT,
    )

    assert "{n}" in _DEFAULT_SUMMARY_PROMPT
    assert "{source_table}" in _DEFAULT_SUMMARY_PROMPT
    assert "{joined}" in _DEFAULT_SUMMARY_PROMPT
    assert "compressing a cluster of older memories" in _DEFAULT_SUMMARY_PROMPT
