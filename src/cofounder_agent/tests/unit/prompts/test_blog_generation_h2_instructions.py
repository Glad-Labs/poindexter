"""Regression test for the 2026-05-27 writer-prompt H2 fix.

Pins the MARKDOWN STRUCTURE section in the
``blog_generation.initial_draft`` template. The preceding 12 published
canonical_blog posts emitted ``**Section Title**`` bold-text
pseudo-headings instead of real ``## Section`` markdown, which broke
the inline-image planner (planner regex only matched real H2/H3, see
test_image_decision_agent.py for the parallel fallback work).

PR #599 patched the planner to tolerate bold-text pseudo-headings as a
side-channel; this prompt edit treats the root cause — instruct the
writer to emit real H2 markdown in the first place. The two layers
together mean inline images get anchored well even when occasional
runs drift back to bold-text dividers.

A regression that strips the markdown-structure block from the prompt
would re-introduce the "0 inline images per post" symptom in
production within one canonical_blog cycle.

Source note (#528): the blog_generation prompt pack was migrated from
``prompts/blog_generation.yaml`` to
``skills/content/blog-generation/SKILL.md`` (agentskills.io format).
This test now resolves the template through ``UnifiedPromptManager``
(YAML/skill/Langfuse-agnostic) instead of reading the retired YAML file
directly.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager


def _load_prompt(key: str) -> str:
    pm = UnifiedPromptManager()
    if key not in pm.prompts:
        raise AssertionError(
            f"Prompt key {key!r} not registered — refactor likely moved "
            "the key. Update this test alongside that change."
        )
    return pm.prompts[key]["template"]


def test_initial_draft_demands_real_h2_markdown() -> None:
    """The MARKDOWN STRUCTURE section must call out real ``## ``
    headings AND ban bold-text pseudo-heading dividers. Both phrases
    are load-bearing — dropping either lets the writer drift back to
    ``**Section Title**`` style."""
    prompt = _load_prompt("blog_generation.initial_draft")

    assert "## Section Title" in prompt, (
        "Prompt no longer demonstrates real H2 markdown — writers will "
        "drift back to ``**bold-text**`` pseudo-headings and the inline "
        "image planner will see 0 sections."
    )
    assert (
        "bold-text fake" in prompt.lower()
        or "bold-text section divider" in prompt.lower()
        or "do not use ``**section" in prompt.lower()
        or "do not use `**section" in prompt.lower()
    ), (
        "Prompt no longer bans bold-text pseudo-heading dividers. "
        "The writer may regress to ``**Section Title**`` style which "
        "renders as bold prose, not <h2>."
    )


def test_initial_draft_does_not_request_h1_in_body() -> None:
    """The title is supplied separately by the pipeline. An H1 in the
    body would compete with the page title for SEO weight and clutter
    the article-card preview. This rule must stay."""
    prompt = _load_prompt("blog_generation.initial_draft")
    assert "do NOT lead the body with an H1" in prompt, (
        "Prompt no longer warns against H1 in the body — the writer "
        "may emit ``# Title`` and conflict with the SEO-supplied title."
    )


def test_initial_draft_self_check_includes_heading_check() -> None:
    """SELF-CHECK list must include the heading verification so the
    LLM gets a second look before returning. The earlier MARKDOWN
    STRUCTURE block describes the rule; the SELF-CHECK enforces it."""
    prompt = _load_prompt("blog_generation.initial_draft")
    self_check_idx = prompt.find("SELF-CHECK")
    assert self_check_idx >= 0, "SELF-CHECK section missing from prompt"
    self_check_block = prompt[self_check_idx:]
    assert (
        "## …" in self_check_block
        or "## " in self_check_block
        or "H2 markdown" in self_check_block
    ), (
        "SELF-CHECK no longer asks the writer to verify headings are "
        "real H2 markdown. Without the second-pass check, the writer "
        "regresses to bold-text dividers on ~40% of runs."
    )
