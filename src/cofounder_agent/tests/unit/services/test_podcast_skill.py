"""Tests for the migrated ``podcast`` skill in ``services.prompt_manager``.

The ``podcast`` prompt was migrated from ``prompts/podcast.yaml`` to
``skills/content/podcast/SKILL.md`` (agentskills.io format), following the
``research`` pack as the reference. These tests pin:

1. that the podcast key still resolves (the migration didn't drop it),
2. that the template carries its placeholders (no silent drift),
3. that the resolved template ends with a single trailing newline (YAML ``|``
   clip semantics preserved by the skill loader).

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager

_PODCAST_KEYS = ("podcast.script_rewrite",)


def test_podcast_keys_resolve_from_skill() -> None:
    """The podcast key must load from skills/content/podcast/SKILL.md."""
    pm = UnifiedPromptManager()
    for key in _PODCAST_KEYS:
        assert key in pm.prompts, f"{key} did not load from the podcast skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


def test_podcast_template_contains_placeholders() -> None:
    """Templates must keep the placeholders the retired YAML shipped.

    Guards against silent drift during the YAML->SKILL.md migration.
    """
    pm = UnifiedPromptManager()

    rewrite = pm.prompts["podcast.script_rewrite"]["template"]
    assert "Rewrite the following blog article as a podcast script" in rewrite
    assert "{title}" in rewrite
    assert "{content}" in rewrite
    assert "PODCAST SCRIPT:" in rewrite


def test_podcast_templates_end_with_single_newline() -> None:
    """Resolved templates end with exactly one trailing newline.

    The skill loader normalizes to YAML ``|`` clip semantics so migrated
    templates are byte-identical to the YAML they replaced.
    """
    pm = UnifiedPromptManager()
    for key in _PODCAST_KEYS:
        template = pm.prompts[key]["template"]
        assert template.endswith("\n"), f"{key} missing trailing newline"
        assert not template.endswith("\n\n"), f"{key} has extra trailing newline"
