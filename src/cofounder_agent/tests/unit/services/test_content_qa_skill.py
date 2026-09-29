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

from poindexter.services.prompt_manager import PromptCategory, UnifiedPromptManager
from tests.unit._nonempty import nonempty

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
    "qa.vision_preview_screenshot": ("{title}", "{topic}", "{tile_count}", "{tile_guide}", "{page_facts}"),
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
        for placeholder in nonempty(placeholders, "placeholders"):
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


def test_the_preview_prompt_renders_for_tiles() -> None:
    """The rendered-preview prompt takes the tile count and a per-tile row guide.

    A template that formats to text with a placeholder left in it (or a JSON
    brace that lost its escape) would only fail at runtime, mid-pipeline.
    """
    import re

    pm = UnifiedPromptManager()
    guide = "Tile 1: page rows 0-1024\nTile 2: page rows 1024-2048"
    facts = "- Images: 4 in the page, all loaded.\n- Horizontal overflow: none."
    text = pm.get_prompt(
        "qa.vision_preview_screenshot",
        title="A Title", topic="a topic", tile_count=2, tile_guide=guide, page_facts=facts,
    )
    assert "TITLE: A Title" in text and "TOPIC: a topic" in text
    assert "tile count: 2" in text
    assert guide in text
    assert facts in text
    assert re.findall(r"\{[a-z_]+\}", text) == [], "an unfilled placeholder is left in the prompt"
    assert '{"score": int, "approved": true/false, "issues": [' in text, "the JSON verdict shape lost its braces"


def test_the_preview_prompt_asks_the_judge_to_ground_every_issue_in_a_tile() -> None:
    """A judge that must name the tile for each issue reports only what it saw:
    the false 'placeholder hero' and 'missing images' verdicts were unanchored."""
    pm = UnifiedPromptManager()
    template = pm.prompts["qa.vision_preview_screenshot"]["template"]
    assert "name the tile for every issue" in template
    assert "An image is loaded when its tile shows a picture there" in template
    # tiles are cut mechanically, so an element can continue into the next one
    assert "run from the bottom of one tile into the top of the next" in template


def test_the_preview_prompt_hands_the_judge_the_facts_the_browser_measured() -> None:
    """Whether an image loaded, and whether the page overflows sideways, are measured by
    the browser and enforced in code (multi_model_qa). The prompt carries them as
    given so the judge has nothing to guess about: guessing about images is where the
    false 'placeholder hero' verdicts came from. The rubric keeps its image and
    layout lines: trimming them made the judge invent problems on clean pages
    (11 of 20 flagged, against 0 of 20 with the rubric intact)."""
    pm = UnifiedPromptManager()
    template = pm.prompts["qa.vision_preview_screenshot"]["template"]
    assert "The browser measured these facts while it rendered the page." in template
    assert "take them as given" in template
    assert "{page_facts}" in template
    assert "Broken or missing images" in template
    assert "Layout problems" in template
    assert template.index("{page_facts}") < template.index("Rate 0-100")
