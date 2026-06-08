"""Tests for the migrated video-director skill.

`prompts/video_director.yaml` → `skills/content/video-director/SKILL.md`
(the final pack of the #528 prompt-catalog migration). The "Glad Labs"
persona token became a `{site_name}` placeholder rendered from site_config
by the generate_video_shot_list stage.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager

_KEY = "video.director_v1"
_SHORT_KEY = "video.director_short_v1"


def test_director_key_resolves_from_skill() -> None:
    pm = UnifiedPromptManager()
    assert _KEY in pm.prompts, f"{_KEY} did not load from the video-director skill"
    template = pm.prompts[_KEY]["template"]
    assert template.strip(), "director template is empty"
    # Placeholders the stage fills must survive.
    for placeholder in ("{site_name}", "{title}", "{content}",
                        "{podcast_script}", "{target_duration_s}",
                        "{model}", "{now_iso}"):
        assert placeholder in template, f"missing placeholder {placeholder}"
    # JSON schema braces must be escaped so .format() leaves literal braces.
    assert '{{' in template and '}}' in template
    # Clip semantics: single trailing newline (YAML | parity).
    assert template.endswith("\n")
    assert not template.endswith("\n\n")


def test_director_renders_operator_brand_no_literal_placeholder() -> None:
    """get_prompt must substitute {site_name} (and unescape JSON braces)."""
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _KEY,
        title="T",
        content="C",
        podcast_script="P",
        target_duration_s="60.0",
        model="m",
        now_iso="2026-05-30T00:00:00Z",
        site_name="Glad Labs",
    )
    assert "video director for a Glad Labs blog post" in rendered
    assert "{site_name}" not in rendered
    # Escaped JSON braces unescaped to single braces.
    assert '"version": 1' in rendered
    assert "{{" not in rendered


def test_director_renders_clean_with_empty_site_name() -> None:
    """Unset operator brand (empty string) still renders without errors."""
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _KEY,
        title="T",
        content="C",
        podcast_script="P",
        target_duration_s="60.0",
        model="m",
        now_iso="2026-05-30T00:00:00Z",
        site_name="",
    )
    assert "{site_name}" not in rendered
    assert "blog post" in rendered


# ---------------------------------------------------------------------------
# Short-form director (Plan 3, #517): video.director_short_v1 — purpose-built
# 9:16 vertical retention clip from the short summary script. It uses
# {short_script} where the long prompt uses {podcast_script}.
# ---------------------------------------------------------------------------


def test_short_director_key_resolves_from_skill() -> None:
    pm = UnifiedPromptManager()
    assert _SHORT_KEY in pm.prompts, (
        f"{_SHORT_KEY} did not load from the video-director skill"
    )
    template = pm.prompts[_SHORT_KEY]["template"]
    assert template.strip(), "short director template is empty"
    # Placeholders the stage fills must survive — note {short_script}, not
    # {podcast_script}, for the short-form narration.
    for placeholder in ("{site_name}", "{title}", "{content}",
                        "{short_script}", "{target_duration_s}",
                        "{model}", "{now_iso}"):
        assert placeholder in template, f"missing placeholder {placeholder}"
    # JSON schema braces must be escaped so .format() leaves literal braces.
    assert '{{' in template and '}}' in template


def test_short_director_renders_9x16_and_brand() -> None:
    """get_prompt must substitute params (incl. {short_script}) + unescape braces."""
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _SHORT_KEY,
        title="T",
        content="C",
        short_script="S",
        target_duration_s="20.0",
        model="m",
        now_iso="2026-06-08T00:00:00Z",
        site_name="Glad Labs",
    )
    assert "short-form video director for a Glad Labs post" in rendered
    assert "{site_name}" not in rendered
    assert "{short_script}" not in rendered
    # The short director targets a vertical 9:16 clip.
    assert '"aspect": "9:16"' in rendered
    assert "short_v1" in rendered
    # Escaped JSON braces unescaped to single braces.
    assert "{{" not in rendered
