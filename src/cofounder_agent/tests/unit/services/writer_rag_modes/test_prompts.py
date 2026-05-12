"""Snapshot tests for the writer_rag_modes prompts.

These pin the rendered output of each prompt byte-for-byte so the
YAML default + str.format wiring stays in lockstep with the inline
fallback. The Lane A migration (poindexter#450) introduced this
pattern for the seven prompts it moved to YAML; this file extends it
to the two writer-mode prompts migrated in batch 12 (poindexter#485).

If a prompt is intentionally reworded, update both:

  - prompts/writer_rag_modes.yaml (the canonical default)
  - the inline ``_*_PROMPT_FALLBACK`` constant in the module
  - the expected string in this test

Snapshot equality between YAML-loaded and inline-fallback paths is
the contract — operators editing the YAML should produce the same
text the bootstrap path would produce without it.
"""

from __future__ import annotations

import pytest

from services.prompt_manager import UnifiedPromptManager


@pytest.fixture
def pm() -> UnifiedPromptManager:
    return UnifiedPromptManager()


@pytest.mark.unit
class TestStorySpineOutlinePrompt:
    """Pins writer_rag_modes.story_spine.outline_prompt rendering."""

    def test_key_present_in_registry(self, pm: UnifiedPromptManager):
        assert "writer_rag_modes.story_spine.outline_prompt" in pm.prompts

    def test_render_matches_inline_fallback(self, pm: UnifiedPromptManager):
        from services.writer_rag_modes.story_spine import _OUTLINE_PROMPT_FALLBACK

        kwargs = dict(
            snippet_limit=15,
            topic="FastAPI architecture",
            angle="hard-won opinions",
            snippet_block="snippet 1\n---\nsnippet 2",
        )
        rendered_yaml = pm.get_prompt(
            "writer_rag_modes.story_spine.outline_prompt", **kwargs,
        )
        rendered_inline = _OUTLINE_PROMPT_FALLBACK.format(**kwargs)
        assert rendered_yaml == rendered_inline

    def test_render_includes_substituted_values(self, pm: UnifiedPromptManager):
        rendered = pm.get_prompt(
            "writer_rag_modes.story_spine.outline_prompt",
            snippet_limit=12,
            topic="Postgres tuning",
            angle="for indie builders",
            snippet_block="some context here",
        )
        assert "12 snippets" in rendered
        assert "Postgres tuning" in rendered
        assert "for indie builders" in rendered
        assert "some context here" in rendered
        # JSON shape preserved verbatim (curly literals not doubled in output).
        assert '"hook"' in rendered
        assert '"close"' in rendered


@pytest.mark.unit
class TestTwoPassRevisePrompt:
    """Pins writer_rag_modes.two_pass.revise_prompt rendering."""

    def test_key_present_in_registry(self, pm: UnifiedPromptManager):
        assert "writer_rag_modes.two_pass.revise_prompt" in pm.prompts

    def test_render_matches_inline_fallback(self, pm: UnifiedPromptManager):
        from services.writer_rag_modes.two_pass import _REVISE_PROMPT_FALLBACK

        kwargs = dict(
            draft="draft body with [EXTERNAL_NEEDED: timestamp]",
            aug_block="[EXTERNAL_NEEDED: timestamp] → 2026-05-12T17:00Z",
        )
        rendered_yaml = pm.get_prompt(
            "writer_rag_modes.two_pass.revise_prompt", **kwargs,
        )
        rendered_inline = _REVISE_PROMPT_FALLBACK.format(**kwargs)
        assert rendered_yaml == rendered_inline

    def test_render_preserves_marker_syntax(self, pm: UnifiedPromptManager):
        rendered = pm.get_prompt(
            "writer_rag_modes.two_pass.revise_prompt",
            draft="A claim [EXTERNAL_NEEDED: source]",
            aug_block="[EXTERNAL_NEEDED: source] → it was 2024",
        )
        # The marker syntax must survive verbatim — that's how the model
        # knows what to substitute.
        assert "[EXTERNAL_NEEDED:" in rendered
        assert rendered.count("[EXTERNAL_NEEDED:") >= 2
