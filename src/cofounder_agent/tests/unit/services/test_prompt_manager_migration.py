"""Pin the UnifiedPromptManager migration of three previously-inline prompts:

* ``services.self_review._resolve_prompt`` →
  ``qa.self_review.contradictions_review`` + ``qa.self_review.contradictions_revise``
* ``services.self_consistency_rail._resolve_summary_prompt`` →
  ``qa.self_consistency.summarize``
* ``services.integrations.handlers.retention_summarize_to_table._resolve_summary_prompt_template`` →
  ``ops.retention.summarize_to_table``

Each resolver must:
1. Return the prompt-manager template when the manager resolves the key.
2. Fall back to the inline constant when the manager raises.
3. Apply user-supplied placeholders (review_text / draft / topic / content).

Closes #237.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_self_review_resolver_uses_prompt_manager():
    """When UnifiedPromptManager resolves the key, the resolver returns
    its template (formatted with kwargs) — NOT the inline fallback."""
    from services import self_review

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value.get_prompt.return_value = (
            "PM: title=Hello topic=AI draft=Body"
        )
        result = self_review._resolve_prompt(
            "qa.self_review.contradictions_review",
            title="Hello",
            topic="AI",
            draft="Body",
            fallback="FALLBACK {title}",
        )
    assert result == "PM: title=Hello topic=AI draft=Body"
    mock_pm.return_value.get_prompt.assert_called_once_with(
        "qa.self_review.contradictions_review",
        title="Hello", topic="AI", draft="Body",
    )


@pytest.mark.unit
def test_self_review_resolver_falls_back_when_pm_raises():
    """Bootstrap / test paths where UnifiedPromptManager is unavailable
    must keep working — the inline fallback gets ``.format(**kwargs)``'d."""
    from services import self_review

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=RuntimeError("no DB pool"),
    ):
        result = self_review._resolve_prompt(
            "qa.self_review.contradictions_review",
            title="T",
            topic="O",
            draft="D",
            fallback="FB title={title} topic={topic} draft={draft}",
        )
    assert result == "FB title=T topic=O draft=D"


@pytest.mark.unit
def test_self_consistency_resolver_uses_prompt_manager():
    from services import self_consistency_rail

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value.get_prompt.return_value = "PM SUMMARY: topic=t content=c"
        result = self_consistency_rail._resolve_summary_prompt(
            topic="t", content="c",
        )
    assert result == "PM SUMMARY: topic=t content=c"
    mock_pm.return_value.get_prompt.assert_called_once_with(
        "qa.self_consistency.summarize", topic="t", content="c",
    )


@pytest.mark.unit
def test_self_consistency_resolver_falls_back_on_pm_failure():
    from services import self_consistency_rail

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=RuntimeError("pm broken"),
    ):
        result = self_consistency_rail._resolve_summary_prompt(
            topic="quantum gardening", content="ARTICLE_BODY",
        )
    # Fallback template includes "Summarize the following article" + topic + content
    assert "Summarize the following article" in result
    assert "quantum gardening" in result
    assert "ARTICLE_BODY" in result


@pytest.mark.unit
def test_retention_resolver_returns_raw_template_from_prompt_manager():
    """Unlike the other two resolvers, the retention handler needs the
    *raw* template (with {bucket_start_iso}/{row_count} unfilled) so the
    handler can apply the per-bucket replacements before handing it to
    ``build_summary_text_via_llm`` for the remaining placeholders."""
    from services.integrations.handlers import retention_summarize_to_table

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value._fetch_from_langfuse.return_value = None
        mock_pm.return_value.prompts = {
            "ops.retention.summarize_to_table": {
                "template": "PM RAW: {source_table}/{n}/{bucket_start_iso}/"
                            "{row_count}/{joined}",
            },
        }
        result = retention_summarize_to_table._resolve_summary_prompt_template()
    # Raw template returned — placeholders intact for the caller to fill
    assert "{bucket_start_iso}" in result
    assert "{row_count}" in result
    assert "{joined}" in result


@pytest.mark.unit
def test_retention_resolver_falls_back_when_pm_unavailable():
    """When prompt_manager import / lookup fails, the inline fallback
    template is returned — handler keeps working without Langfuse / YAML."""
    from services.integrations.handlers import retention_summarize_to_table

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=ImportError("module missing"),
    ):
        result = retention_summarize_to_table._resolve_summary_prompt_template()
    assert "compressing one calendar day" in result
    assert "{bucket_start_iso}" in result
    assert "{row_count}" in result
