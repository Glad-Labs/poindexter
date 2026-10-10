"""Pin the cycle-4 UnifiedPromptManager migrations.

Three inline prompts migrated to YAML+Langfuse per
``feedback_prompts_must_be_db_configurable``:

* ``services.social_poster._build_twitter_prompt`` →
  ``social.twitter_promote``
* ``services.social_poster._build_linkedin_prompt`` →
  ``social.linkedin_promote``

Note: ``memory.collapse_old_embeddings.summary`` was migrated from
``services.jobs.collapse_old_embeddings._resolve_summary_prompt_template``
— the job was retired 2026-06-24 (folded into retention_policies handler
``embeddings_collapse``). The handler briefly used the inline prompt
constant directly; cycle 5 (poindexter#829, see
test_cycle5_prompt_manager_migration.py) re-wired it to resolve the
registered key.

Each resolver pulls from UnifiedPromptManager and falls back to the
inline constant on any lookup failure — same pattern as the cycle-3
migrations in #612.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_social_twitter_resolver_uses_prompt_manager():
    from poindexter.services import social_poster

    with patch("poindexter.services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value.get_prompt.return_value = "PM tweet"
        result = social_poster._resolve_social_prompt(
            "social.twitter_promote",
            company_name="Glad Labs",
            char_limit=280,
            title="t",
            excerpt="e",
            post_url="u",
            hashtags="#h",
        )
    assert result == "PM tweet"


@pytest.mark.unit
def test_collapse_summary_prompt_has_required_placeholders():
    """The pack's summary template carries the three placeholders
    build_summary_text_via_llm fills (there is no in-code copy)."""
    from poindexter.services.integrations.handlers.retention_embeddings_collapse import (
        _resolve_summary_prompt_template,
    )

    template = _resolve_summary_prompt_template()
    assert "{n}" in template
    assert "{source_table}" in template
    assert "{joined}" in template
    assert "compressing a cluster of older memories" in template
