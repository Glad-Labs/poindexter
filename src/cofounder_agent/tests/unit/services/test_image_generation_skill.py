"""Tests for the image_generation SKILL.md prompt pack.

The image_generation prompts were migrated from
``prompts/image_generation.yaml`` to
``skills/content/image-generation/SKILL.md`` (agentskills.io format),
following the proven research/video/podcast/seo_metadata pattern. These
tests pin:

1. that the three image keys still resolve (the migration didn't drop them),
2. that the templates carry their placeholders (no silent drift),
3. that templates end with a single trailing newline (YAML ``|`` clip
   semantics, which the SKILL.md loader normalizes to).

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager

_IMAGE_KEYS = (
    "image.featured_image",
    "image.search_queries",
    "image.decision",
)


def test_image_keys_resolve_from_skill() -> None:
    """All three image keys must load from the image-generation skill."""
    pm = UnifiedPromptManager()
    for key in _IMAGE_KEYS:
        assert key in pm.prompts, f"{key} did not load from the image-generation skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


def test_image_templates_carry_placeholders() -> None:
    """Templates must keep the placeholders the old YAML shipped.

    Guards against silent drift during the YAML->SKILL.md migration.
    """
    pm = UnifiedPromptManager()

    featured = pm.prompts["image.featured_image"]["template"]
    assert "featured blog image about: {topic}" in featured

    queries = pm.prompts["image.search_queries"]["template"]
    assert "image search queries as JSON array for the topic: {topic}" in queries

    decision = pm.prompts["image.decision"]["template"]
    assert "image director for a {category} content site" in decision
    assert "ARTICLE TOPIC: {topic}" in decision
    assert "CATEGORY: {category}" in decision
    assert "{section_list}" in decision
    assert "{max_images}" in decision
    # Literal JSON braces in the template must survive (escaped as {{ }}).
    assert '{{\n  "featured"' in decision


def test_image_templates_end_with_single_newline() -> None:
    """Each template ends with exactly one trailing newline (clip semantics)."""
    pm = UnifiedPromptManager()
    for key in _IMAGE_KEYS:
        template = pm.prompts[key]["template"]
        assert template.endswith("\n"), f"{key} must end with a trailing newline"
        assert not template.endswith("\n\n"), f"{key} must clip to one trailing newline"
