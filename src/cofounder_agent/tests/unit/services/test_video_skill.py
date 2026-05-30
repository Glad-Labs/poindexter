"""Tests for the ``video`` SKILL.md prompt pack.

The ``video`` prompts were migrated from ``prompts/video.yaml`` to
``skills/content/video/SKILL.md`` (agentskills.io format), following the
``research`` pack as the reference migration. These tests pin:

1. that the video key still resolves (the migration didn't drop it),
2. that the template still carries its key placeholders,
3. that the resolved template ends with a single trailing newline (the
   YAML ``|`` clip-chomp guard the loader normalizes to).

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager


_VIDEO_KEYS = ("video.short_form_narration",)


def test_video_keys_resolve_from_skill() -> None:
    """All video keys must load from skills/content/video/SKILL.md."""
    pm = UnifiedPromptManager()
    for key in _VIDEO_KEYS:
        assert key in pm.prompts, f"{key} did not load from the video skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


def test_video_templates_contain_key_placeholders() -> None:
    """Templates must keep the placeholders the YAML shipped with.

    Guards against silent drift during the YAML->SKILL.md migration.
    """
    pm = UnifiedPromptManager()

    narration = pm.prompts["video.short_form_narration"]["template"]
    assert "{title}" in narration
    assert "{content}" in narration
    assert "summarizing this article" in narration
    assert "TikTok/YouTube Shorts" in narration


def test_video_templates_end_with_single_newline() -> None:
    """Each resolved template ends with exactly one trailing newline.

    The loader normalizes SKILL.md bodies to YAML ``|`` clip semantics so
    migrated templates are byte-identical to the YAML they replaced.
    """
    pm = UnifiedPromptManager()
    for key in _VIDEO_KEYS:
        template = pm.prompts[key]["template"]
        assert template.endswith("\n"), f"{key} must end with a trailing newline"
        assert not template.endswith("\n\n"), f"{key} has more than one trailing newline"
