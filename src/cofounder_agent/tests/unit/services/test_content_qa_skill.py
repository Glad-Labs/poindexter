"""Tests for the content_qa skill (migrated from prompts/content_qa.yaml).

The QA "moat" pack was migrated from ``prompts/content_qa.yaml`` to
``skills/content/content-qa/SKILL.md`` (agentskills.io format) as part of the
skill-catalog adoption (Glad-Labs/poindexter#528). These tests pin:

1. that every content_qa key still resolves (the migration dropped nothing),
2. that each key's documented placeholders survive in the loaded template,
3. that the loader's YAML ``|`` clip semantics hold — exactly one trailing
   ``\n`` on every template (the byte-fidelity contract the snapshot tests in
   test_cross_model_qa_prompts.py / test_multi_model_qa_prompts.py rely on).

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

from services.prompt_manager import PromptCategory, UnifiedPromptManager

# Every key the content_qa pack provides, with the placeholders the
# template must still contain after migration. Guards against silent
# truncation of the long QA templates.
_CONTENT_QA_KEYS: dict[str, tuple[str, ...]] = {
    "qa.content_review": ("{content}",),
    "qa.self_critique": ("{content}",),
    "qa.topic_delivery": ("{topic}", "{opening}"),
    "qa.consistency": ("{content}",),
    "qa.review": ("{current_date}", "{title}", "{topic}", "{sources_block}", "{content}"),
    "qa.aggregate_rewrite": ("{title}", "{issues_to_fix}", "{content}"),
    "qa.self_review.contradictions_review": ("{title}", "{topic}", "{draft}"),
    "qa.self_review.contradictions_revise": ("{review_text}", "{draft}"),
    "qa.self_consistency.summarize": ("{topic}", "{content}"),
    "qa.quality_evaluation_llm_rubric": ("{topic}", "{content_excerpt}"),
    "qa.vision_image_relevance": ("{title}", "{topic}", "{content_snippet}"),
    "qa.vision_preview_screenshot": ("{title}", "{topic}"),
}


def test_every_content_qa_key_resolves_from_skill() -> None:
    """All content_qa keys must load from skills/content/content-qa/SKILL.md."""
    pm = UnifiedPromptManager()
    for key in _CONTENT_QA_KEYS:
        assert key in pm.prompts, f"{key} did not load from the content-qa skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


def test_content_qa_keys_keep_their_placeholders() -> None:
    """Each migrated template must still contain its documented placeholders.

    Catches a long template silently truncated during the migration — every
    declared brace placeholder has to survive.
    """
    pm = UnifiedPromptManager()
    for key, placeholders in _CONTENT_QA_KEYS.items():
        template = pm.prompts[key]["template"]
        for placeholder in placeholders:
            assert placeholder in template, f"{key} lost placeholder {placeholder}"


def test_content_qa_templates_end_with_single_newline() -> None:
    """The loader clips to YAML ``|`` semantics — exactly one trailing newline.

    This is the byte-fidelity contract the snapshot tests depend on. The
    ``|-`` (no trailing newline) YAML entries gain one trailing ``\\n`` from
    the loader's clip; that single newline is the only acceptable byte change
    from the YAML→SKILL.md migration.
    """
    pm = UnifiedPromptManager()
    for key in _CONTENT_QA_KEYS:
        template = pm.prompts[key]["template"]
        assert template.endswith("\n"), f"{key} must end with a newline"
        assert not template.endswith("\n\n"), f"{key} must end with exactly one newline"


def test_content_qa_metadata_is_content_qa_category() -> None:
    """Every content_qa key reports the CONTENT_QA category."""
    pm = UnifiedPromptManager()
    for key in _CONTENT_QA_KEYS:
        assert pm.get_metadata(key).category == PromptCategory.CONTENT_QA
