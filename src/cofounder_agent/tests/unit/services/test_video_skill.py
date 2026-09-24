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

from poindexter.services.prompt_manager import UnifiedPromptManager

_VIDEO_KEYS = ("video.short_form_narration", "video.long_form_narration")


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
    assert "{site_name}" in narration
    assert "{target_seconds}" in narration
    assert "{target_words}" in narration
    assert "summarizing the article" in narration
    assert "TikTok/YouTube Shorts" in narration
    # _parse_scene_output splits on this marker.
    assert '"SHORT:"' in narration


def test_short_narration_is_wired_and_matches_its_fallback() -> None:
    """poindexter#1071: the key must be the prompt the stage actually sends.

    The stage resolves this key and falls back to an in-code copy; the two
    must be identical or an edit to the pack silently diverges from what a
    store-less boot sends. The pre-#867 fossil ("60-second", "150 words")
    must not come back.
    """
    from poindexter.modules.content.stages.generate_media_scripts import (
        _SHORT_SCENES_FALLBACK,
        _build_scene_prompt,
    )

    pm = UnifiedPromptManager()
    narration = pm.prompts["video.short_form_narration"]["template"]
    assert narration.rstrip("\n") == _SHORT_SCENES_FALLBACK
    assert "150 words" not in narration and "60-second" not in narration
    rendered = _build_scene_prompt(
        "T", "body", "Site", target_seconds=45, target_words=95,
    )
    assert "~45-second narration (about 95 words)" in rendered
    assert "Full article at Site." in rendered


def test_video_narration_renders_branded_cta() -> None:
    """The narration CTA must render the operator's site name.

    The public skill ships a ``{site_name}`` placeholder (brand-free in
    the file). The operator's deployment fills it from site_config —
    the media-scripts stage passes ``site_name`` from site_config.
    When formatted with a concrete name, the rendered prompt must carry
    that name and leave no literal ``{site_name}`` behind.
    """
    pm = UnifiedPromptManager()
    narration = pm.prompts["video.short_form_narration"]["template"]

    rendered = narration.format(
        title="Why Local LLMs Beat Cloud APIs",
        content="Some body text about local models.",
        site_name="Glad Labs",
        target_seconds=45,
        target_words=95,
    )
    assert "Glad Labs" in rendered
    assert "{site_name}" not in rendered


def test_video_narration_renders_with_empty_site_name() -> None:
    """A fresh install (no site_name) renders without raising or leaking.

    the media-scripts stage passes an empty ``site_name`` when unset;
    an empty string is the unset sentinel, so the template must format
    cleanly with no leftover placeholder.
    """
    pm = UnifiedPromptManager()
    narration = pm.prompts["video.short_form_narration"]["template"]

    rendered = narration.format(
        title="A Title",
        content="Body.",
        site_name="",
        target_seconds=45,
        target_words=95,
    )
    assert "{site_name}" not in rendered


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
