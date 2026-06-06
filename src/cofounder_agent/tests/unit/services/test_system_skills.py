"""Tests for the content skill packs split out of ``prompts/system.yaml``
and ``prompts/tasks.yaml`` (#528).

The content-pipeline prompts were migrated from the two mixed YAML files
into agentskills.io SKILL.md packs under ``skills/content/``:

* ``skills/content/writer/SKILL.md`` — ``task.creative_blog_generation`` +
  ``narrative.system`` (category ``blog_generation``)
* ``skills/content/qa/SKILL.md`` — ``task.qa_content_evaluation``
  (category ``content_qa``)
* ``skills/content/utility/SKILL.md`` — ``system.content_writer`` +
  ``task.content_summarization`` + ``task.utility_json_conversion``
  (category ``utility``)

These tests pin that each key still resolves, that templates end with a
single trailing newline (the loader's ``|`` clip semantics), that each
prompt keeps its exact YAML category, and that ``narrative.system``
renders its ``{site_name}`` / ``{site_url}`` brand placeholders.

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

import pytest

from services.prompt_manager import PromptCategory, UnifiedPromptManager

# (key, expected category) pairs — categories must match the original YAML.
_CONTENT_KEYS = (
    ("task.creative_blog_generation", PromptCategory.BLOG_GENERATION),
    ("narrative.system", PromptCategory.BLOG_GENERATION),
    ("task.qa_content_evaluation", PromptCategory.CONTENT_QA),
    ("system.content_writer", PromptCategory.UTILITY),
    ("task.content_summarization", PromptCategory.UTILITY),
    ("task.utility_json_conversion", PromptCategory.UTILITY),
)


@pytest.fixture
def pm() -> UnifiedPromptManager:
    """Fresh UnifiedPromptManager — YAML/SKILL.md only, no Langfuse, no DB."""
    return UnifiedPromptManager()


@pytest.mark.unit
@pytest.mark.parametrize(("key", "_category"), _CONTENT_KEYS)
def test_content_key_resolves_from_skill(
    pm: UnifiedPromptManager, key: str, _category: PromptCategory,
) -> None:
    """Every content key loads from a skills/content/* SKILL.md pack."""
    assert key in pm.prompts, f"{key} did not load from a content skill pack"
    assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


@pytest.mark.unit
@pytest.mark.parametrize(("key", "_category"), _CONTENT_KEYS)
def test_content_template_clips_to_single_trailing_newline(
    pm: UnifiedPromptManager, key: str, _category: PromptCategory,
) -> None:
    """Loader normalizes to YAML ``|`` clip semantics: exactly one ``\\n``."""
    template = pm.prompts[key]["template"]
    assert template.endswith("\n"), f"{key} template lost its trailing newline"
    assert not template.endswith("\n\n"), f"{key} template has >1 trailing newline"


@pytest.mark.unit
@pytest.mark.parametrize(("key", "category"), _CONTENT_KEYS)
def test_content_category_preserved(
    pm: UnifiedPromptManager, key: str, category: PromptCategory,
) -> None:
    """Each prompt keeps its exact YAML category despite the pack split."""
    assert pm.get_metadata(key).category is category


@pytest.mark.unit
def test_creative_blog_template_matches_migrated_yaml(
    pm: UnifiedPromptManager,
) -> None:
    template = pm.prompts["task.creative_blog_generation"]["template"]
    assert "Create a blog post about: {topic}" in template
    assert "{style}" in template
    assert "{length}" in template
    assert "{research_context}" in template


@pytest.mark.unit
def test_qa_content_evaluation_matches_migrated_yaml(
    pm: UnifiedPromptManager,
) -> None:
    template = pm.prompts["task.qa_content_evaluation"]["template"]
    assert "Evaluate this content for quality" in template
    assert "overall_score" in template
    assert "{content}" in template


@pytest.mark.unit
def test_narrative_system_renders_brand_placeholder(
    pm: UnifiedPromptManager,
) -> None:
    """narrative.system is brand-templated: formatting with site_name
    injects the brand and leaves no literal ``{site_name}`` behind."""
    rendered = pm.get_prompt(
        "narrative.system", site_name="Glad Labs", site_url="gladlabs.io",
    )
    assert "Glad Labs" in rendered
    assert "{site_name}" not in rendered
    assert "{site_url}" not in rendered
