"""Tests for the migrated video-director skill.

`prompts/video_director.yaml` → `skills/content/video-director/SKILL.md`
(the final pack of the #528 prompt-catalog migration). The "Glad Labs"
persona token became a `{site_name}` placeholder rendered from site_config
by the generate_video_shot_list stage.
"""

from __future__ import annotations

import json

from modules.content.stages.generate_video_shot_list import _extract_json_object
from schemas.video_shot_list import scan_for_human_tokens
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


# ---------------------------------------------------------------------------
# HUMAN-SUBJECT POLICY regression — the SCHEMA example shots are what the
# director LLM imitates. A human subject in an example must route to
# source="pexels" (Pexels queries are never scanned); an sdxl / sdxl_kenburns
# / wan21 example prompt must NEVER carry a _HUMAN_TOKENS noun — not even
# inside a "no people" negation, which scan_for_human_tokens flags regardless
# (only the literal "silhouette"/"faceless" escape hatch clears it). Clean
# examples are what drive the server-side advisory-warning count toward zero
# (the ~15-warning/30-shot render on task 5466fd20). Catches a re-introduced
# "no people"/"developer"/… in either director's example block.
# ---------------------------------------------------------------------------

_AI_SOURCES = ("sdxl", "sdxl_kenburns", "wan21", "generative")


def _example_ai_prompts(rendered: str) -> list[str]:
    """Return the prompts of the AI-source example shots in a rendered director
    prompt's SCHEMA block.

    Parses the example with the SAME ``_extract_json_object`` the stage runs on
    real director output, so the examples are read exactly as the renderer
    reads the live LLM result.
    """
    body = _extract_json_object(rendered)
    assert body, "no JSON schema example found in rendered director prompt"
    shots = json.loads(body)["shots"]
    return [
        s["prompt"] for s in shots
        if s.get("source") in _AI_SOURCES and s.get("prompt")
    ]


def test_long_director_example_ai_prompts_have_no_human_tokens() -> None:
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _KEY,
        title="T", content="C", podcast_script="P",
        target_duration_s="60.0", model="m",
        now_iso="2026-05-30T00:00:00Z", site_name="Glad Labs",
    )
    ai_prompts = _example_ai_prompts(rendered)
    assert ai_prompts, "expected at least one AI-source example shot to scan"
    for prompt in ai_prompts:
        assert scan_for_human_tokens(prompt) == [], (
            "long director example AI-source prompt carries human tokens "
            "(would render as stylized line-art instead of routing the human "
            f"subject to pexels real footage): {prompt!r}"
        )


def test_short_director_example_ai_prompts_have_no_human_tokens() -> None:
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _SHORT_KEY,
        title="T", content="C", short_script="S",
        target_duration_s="20.0", model="m",
        now_iso="2026-06-08T00:00:00Z", site_name="Glad Labs",
    )
    ai_prompts = _example_ai_prompts(rendered)
    assert ai_prompts, "expected at least one AI-source example shot to scan"
    for prompt in ai_prompts:
        assert scan_for_human_tokens(prompt) == [], (
            "short director example AI-source prompt carries human tokens: "
            f"{prompt!r}"
        )
