"""Tests for the migrated ``social_media`` skill pack.

The four social-media prompts were migrated from
``prompts/social_media.yaml`` to ``skills/content/social-media/SKILL.md``
(agentskills.io format), following the same mechanical pattern as the
``research`` / ``video`` / ``podcast`` / ``seo_metadata`` migrations. These
tests pin:

1. that all four social keys still resolve (the migration didn't drop them),
2. that the templates carry their expected placeholders (no silent drift),
3. that every migrated template ends with a single trailing newline (YAML
   ``|`` / ``|-`` -> SKILL.md clip-chomp normalization).

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

import pytest

from services.prompt_manager import UnifiedPromptManager

_SOCIAL_KEYS = (
    "social.research_trends",
    "social.create_post",
    "social.twitter_promote",
    "social.linkedin_promote",
)


@pytest.mark.unit
def test_social_keys_resolve_from_skill() -> None:
    """All four social keys must load from skills/content/social-media/SKILL.md."""
    pm = UnifiedPromptManager()
    for key in _SOCIAL_KEYS:
        assert key in pm.prompts, f"{key} did not load from the social-media skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


@pytest.mark.unit
def test_social_templates_carry_expected_placeholders() -> None:
    """Templates must keep the placeholders their callers fill in.

    Guards against silent drift during the YAML->SKILL.md migration.
    """
    pm = UnifiedPromptManager()

    trends = pm.prompts["social.research_trends"]["template"]
    assert "{topic}" in trends
    assert "trends (list)" in trends

    create = pm.prompts["social.create_post"]["template"]
    assert "{platform}" in create
    assert "{topic}" in create
    assert "call_to_action" in create

    twitter = pm.prompts["social.twitter_promote"]["template"]
    for placeholder in (
        "{company_name}",
        "{char_limit}",
        "{title}",
        "{excerpt}",
        "{post_url}",
        "{hashtags}",
    ):
        assert placeholder in twitter, f"twitter promo missing {placeholder}"
    assert "single tweet" in twitter

    linkedin = pm.prompts["social.linkedin_promote"]["template"]
    for placeholder in (
        "{company_name}",
        "{char_limit}",
        "{title}",
        "{excerpt}",
        "{post_url}",
        "{hashtags}",
    ):
        assert placeholder in linkedin, f"linkedin promo missing {placeholder}"
    assert "LinkedIn post" in linkedin


@pytest.mark.unit
def test_social_templates_end_with_single_trailing_newline() -> None:
    """Every migrated template gets exactly one trailing newline (clip chomp)."""
    pm = UnifiedPromptManager()
    for key in _SOCIAL_KEYS:
        template = pm.prompts[key]["template"]
        assert template.endswith("\n"), f"{key} template must end with a newline"
        assert not template.endswith("\n\n"), f"{key} has >1 trailing newline"


@pytest.mark.unit
def test_social_promo_templates_have_no_brand_leak() -> None:
    """The committed SKILL.md must not leak operator brand/URL identity.

    Brand context is supplied at render time via {company_name}; the public
    template stays generic.
    """
    pm = UnifiedPromptManager()
    for key in ("social.twitter_promote", "social.linkedin_promote"):
        template = pm.prompts[key]["template"]
        lowered = template.lower()
        assert "glad labs" not in lowered, f"{key} leaks operator brand"
        assert "gladlabs" not in lowered, f"{key} leaks operator URL"


@pytest.mark.unit
def test_social_promo_templates_render_with_caller_kwargs() -> None:
    """The promo keys render cleanly with the kwargs social_poster supplies."""
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        "social.twitter_promote",
        company_name="Acme",
        char_limit=280,
        title="Title",
        excerpt="Excerpt",
        post_url="https://example.test/posts/x",
        hashtags="#a #b",
    )
    assert "Acme" in rendered
    assert "280 characters" in rendered
    assert rendered.endswith("\n")
