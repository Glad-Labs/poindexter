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
