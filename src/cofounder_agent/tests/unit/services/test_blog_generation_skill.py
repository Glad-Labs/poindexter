"""Tests for the migrated ``blog_generation`` SKILL.md prompt pack.

The blog-generation prompts were migrated from
``prompts/blog_generation.yaml`` to
``skills/content/blog-generation/SKILL.md`` (agentskills.io format),
following the ``research`` / ``seo_metadata`` / ``video`` / ``podcast``
bricks (#528). These tests pin:

1. that all five blog_generation keys still resolve (the migration
   didn't drop them),
2. that each template carries the placeholders the pipeline formats
   in (no silent placeholder loss during the YAML->SKILL.md copy),
3. that every template ends in exactly one trailing ``\n`` — the YAML
   ``|`` clip-chomp semantics the loader normalises to, so a migrated
   SKILL.md template is byte-identical to the YAML ``template: |`` it
   replaced.

All five YAML keys used the ``|`` (clip-chomp, single trailing
newline) block style, so no snapshot needed a byte change: the loader's
``_extract_skill_section`` produces exactly one trailing ``\n``, which
matches what the old YAML shipped.

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

import pytest

from services.prompt_manager import PromptCategory, UnifiedPromptManager


_BLOG_KEYS = (
    "blog_generation.initial_draft",
    "blog_generation.seo_and_social",
    "blog_generation.iterative_refinement",
    "blog_generation.blog_system_prompt",
    "blog_generation.blog_generation_request",
)

# Each key's required str.format() placeholders — the pipeline supplies
# these at render time. Dropping one silently would raise a KeyError in
# production, so pin them here.
_REQUIRED_PLACEHOLDERS = {
    "blog_generation.initial_draft": (
        "{topic}",
        "{style}",
        "{tone}",
        "{target_length}",
        "{research_context}",
    ),
    "blog_generation.seo_and_social": ("{topic}",),
    "blog_generation.iterative_refinement": ("{content}", "{feedback}"),
    "blog_generation.blog_system_prompt": (
        "{style}",
        "{target_audience}",
        "{domain}",
        "{tone}",
    ),
    "blog_generation.blog_generation_request": (
        "{topic}",
        "{style}",
        "{target_length}",
    ),
}


@pytest.fixture
def pm() -> UnifiedPromptManager:
    """Fresh UnifiedPromptManager — YAML/skill defaults, no Langfuse."""
    return UnifiedPromptManager()


@pytest.mark.unit
def test_blog_keys_resolve_from_skill(pm: UnifiedPromptManager) -> None:
    """All five blog_generation keys must load from the skill pack."""
    for key in _BLOG_KEYS:
        assert key in pm.prompts, (
            f"{key} did not load from skills/content/blog-generation/SKILL.md"
        )
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


@pytest.mark.unit
def test_blog_keys_have_blog_generation_category(
    pm: UnifiedPromptManager,
) -> None:
    """Every key must register under the BLOG_GENERATION category."""
    for key in _BLOG_KEYS:
        meta = pm.get_metadata(key)
        assert meta.category == PromptCategory.BLOG_GENERATION, (
            f"{key} registered under {meta.category} not BLOG_GENERATION"
        )


@pytest.mark.unit
@pytest.mark.parametrize("key", _BLOG_KEYS)
def test_blog_template_carries_required_placeholders(
    pm: UnifiedPromptManager, key: str,
) -> None:
    """Each template must keep the placeholders the pipeline formats in."""
    template = pm.prompts[key]["template"]
    for placeholder in _REQUIRED_PLACEHOLDERS[key]:
        assert placeholder in template, (
            f"{key} dropped placeholder {placeholder} during migration"
        )


@pytest.mark.unit
@pytest.mark.parametrize("key", _BLOG_KEYS)
def test_blog_template_ends_with_single_trailing_newline(
    pm: UnifiedPromptManager, key: str,
) -> None:
    """Loader clips to exactly one trailing newline (YAML ``|`` semantics).

    Downstream rendered prompts and snapshot tests assume this single
    trailing ``\\n``; a regression to ``|+`` (keep-all) or stripped
    output would trip operator-side diff tools (Langfuse).
    """
    template = pm.prompts[key]["template"]
    assert template.endswith("\n"), f"{key} lost its trailing newline"
    assert not template.endswith("\n\n"), f"{key} has >1 trailing newline"
