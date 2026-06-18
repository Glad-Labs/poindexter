"""Tests for the two_pass_writer SKILL.md skill pack.

The two_pass_writer prompts were migrated from
``prompts/two_pass_writer.yaml`` to ``skills/content/two-pass-writer/SKILL.md``
(agentskills.io format), following the research/video/podcast/seo_metadata
migrations. These tests pin:

1. that both prompt keys still resolve (the migration didn't drop them),
2. that the templates keep their required placeholders and marker syntax,
3. that each template ends with exactly one trailing newline (YAML ``|`` clip
   semantics) so byte-fidelity with the retired YAML is preserved.

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager

_TWO_PASS_KEYS = (
    "atoms.two_pass_writer.revise_prompt",
    "atoms.two_pass_writer.generate_with_context",
)


def test_two_pass_keys_resolve_from_skill() -> None:
    """Both keys must load from skills/content/two-pass-writer/SKILL.md."""
    pm = UnifiedPromptManager()
    for key in _TWO_PASS_KEYS:
        assert key in pm.prompts, f"{key} did not load from the two-pass-writer skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


def test_two_pass_templates_have_placeholders() -> None:
    """Templates must keep the placeholders str.format renders against."""
    pm = UnifiedPromptManager()

    revise = pm.prompts["atoms.two_pass_writer.revise_prompt"]["template"]
    assert "{draft}" in revise
    assert "{aug_block}" in revise
    # The marker syntax must survive verbatim — that's how the model knows
    # what to substitute.
    assert "[EXTERNAL_NEEDED:" in revise

    generate = pm.prompts["atoms.two_pass_writer.generate_with_context"]["template"]
    assert "{topic}" in generate
    assert "{angle}" in generate
    assert "{instructions}" in generate
    assert "{snippet_block}" in generate


def test_two_pass_templates_end_with_single_newline() -> None:
    """Each template ends with exactly one trailing newline (YAML | clip)."""
    pm = UnifiedPromptManager()
    for key in _TWO_PASS_KEYS:
        template = pm.prompts[key]["template"]
        assert template.endswith("\n"), f"{key} should end with a newline"
        assert not template.endswith("\n\n"), f"{key} has extra trailing newlines"


def test_two_pass_templates_render_without_stray_braces() -> None:
    """get_prompt() str.format()s the template — any literal ``{`` that isn't a
    real placeholder raises KeyError/ValueError. Render both via the production
    path to prove the enriched bodies have no stray braces."""
    pm = UnifiedPromptManager()
    generate = pm.get_prompt(
        "atoms.two_pass_writer.generate_with_context",
        topic="RTX 5090 local LLM inference",
        angle="real benchmarks",
        instructions="SOURCES: ...",
        snippet_block="[posts/1] we ran it on a 32GB card",
    )
    assert "RTX 5090 local LLM inference" in generate
    revise = pm.get_prompt(
        "atoms.two_pass_writer.revise_prompt",
        draft="a draft",
        aug_block="[EXTERNAL_NEEDED: x] -> fact",
    )
    assert "a draft" in revise


def test_generate_prompt_carries_assembly_directives() -> None:
    """The enriched draft prompt must keep the directives that stop the local
    model's assembly failures (duplication, truncation, unlinked citations,
    fake headings) and enable grounded first-person. Pinned so a future prompt
    edit can't silently drop them and reopen the QA-veto regression
    (glad-labs-stack#1672 follow-up)."""
    pm = UnifiedPromptManager()
    g = pm.prompts["atoms.two_pass_writer.generate_with_context"]["template"].lower()
    # Anti-duplication + clean-ending (the two ollama_critic vetoes)
    assert "once" in g and ("repeat" in g or "duplicate" in g)
    assert "mid-sentence" in g
    # Markdown-link citation (the programmatic unlinked-citation veto)
    assert "markdown link" in g and "url" in g
    # Real H2 headings, not bold fakes
    assert "## " in pm.prompts["atoms.two_pass_writer.generate_with_context"]["template"]
    # Grounded first-person voice (Matt's voice-policy update)
    assert "first person" in g


def test_revise_prompt_guards_against_duplication() -> None:
    """The revise pass feeds the model the full draft; without an explicit
    'exactly once / do not duplicate' guard a weak model re-emits it doubled
    (the observed second-half-duplicate failure)."""
    pm = UnifiedPromptManager()
    r = pm.prompts["atoms.two_pass_writer.revise_prompt"]["template"].lower()
    assert "once" in r
    assert "duplicate" in r or "repeat" in r
