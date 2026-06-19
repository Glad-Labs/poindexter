"""Variety + DB/skill-configurability contract for image prompt construction.

Covers the #image-zimage-and-variety fixes:
- inline style pool is stylized-only (no photoreal) and DB-configurable via
  the ``inline_image_styles`` app_setting,
- the featured + inline prompt builders fall back to a de-funnelled instruction
  (no "evoke the FEELING" funnel) when the prompt manager/skill is unavailable,
- the cross-post dedup window is the ``image_style_dedup_window`` setting.
"""

from __future__ import annotations

import pytest

from modules.content.stages.replace_inline_images import (
    INLINE_STYLES,
    _build_inline_prompt_instruction,
    _load_inline_styles,
)
from modules.content.stages.source_featured_image import (
    _load_recent_published_styles,
    _resolve_image_prompt,
)
from services.site_config import SiteConfig


@pytest.mark.unit
def test_inline_styles_are_stylized_not_photoreal():
    """Photoreal inline styles were removed — low-step SDXL butchers photoreal
    detail and the brand is stylized. The fallback pool must carry none."""
    joined = " ".join(INLINE_STYLES).lower()
    assert "photoreal" not in joined
    assert "photograph" not in joined
    assert len(INLINE_STYLES) >= 3


@pytest.mark.unit
def test_load_inline_styles_none_site_config_uses_fallback():
    assert _load_inline_styles(None) == INLINE_STYLES


@pytest.mark.unit
def test_load_inline_styles_empty_setting_uses_fallback():
    sc = SiteConfig(initial_config={"inline_image_styles": ""})
    assert _load_inline_styles(sc) == INLINE_STYLES


@pytest.mark.unit
def test_load_inline_styles_reads_setting_json():
    sc = SiteConfig(
        initial_config={"inline_image_styles": '["watercolor wash", "pixel art"]'},
    )
    assert _load_inline_styles(sc) == ("watercolor wash", "pixel art")


@pytest.mark.unit
def test_load_inline_styles_bad_json_falls_back():
    sc = SiteConfig(initial_config={"inline_image_styles": "{not valid json"})
    assert _load_inline_styles(sc) == INLINE_STYLES


@pytest.mark.unit
def test_resolve_featured_prompt_fallback_is_defunnelled():
    """A missing key forces the deterministic fallback, which must be style-aware
    and free of the old 'evoke the FEELING / do not depict literally' funnel."""
    out = _resolve_image_prompt(
        "image.__does_not_exist__",
        topic="GPU thermals",
        style="low-poly 3D render",
        style_tags="faceted surfaces",
    )
    assert "low-poly 3D render" in out
    assert "GPU thermals" in out
    assert "feeling" not in out.lower()
    assert "do not depict" not in out.lower()


@pytest.mark.unit
def test_inline_prompt_instruction_is_style_aware():
    """Whether it resolves via the skill or the fallback, the inline instruction
    carries the chosen art style + section subject and no funnel language."""
    out = _build_inline_prompt_instruction(
        "a liquid cooling loop", "GPU thermals", "isometric 3D illustration",
    )
    assert "isometric 3D illustration" in out
    assert "a liquid cooling loop" in out
    assert "feeling" not in out.lower()


@pytest.mark.unit
def test_image_render_params_are_db_seeded():
    """Every image render/prompt knob must be a seeded app_setting so it's
    tunable without a code edit and never a silent inline fallback. Guards
    against accidental removal. #image-zimage-and-variety."""
    # image_generation_model is seeded in 0000_baseline.seeds.sql (not DEFAULTS),
    # so it's excluded here; everything else lives in settings_defaults.
    from services.settings_defaults import DEFAULTS

    for key in (
        "image_prompt_temperature",
        "image_prompt_max_tokens",
        "image_prompt_timeout_seconds",
        "image_render_timeout_seconds",
        "image_style_dedup_window",
        "inline_image_styles",
        "image_base_style_prompt",
        "image_negative_prompt",
    ):
        assert key in DEFAULTS, f"{key} must be a seeded app_setting"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dedup_window_zero_short_circuits():
    """image_style_dedup_window=0 disables the cross-post filter without a DB
    round-trip (returns [] before querying)."""
    sc = SiteConfig(initial_config={"image_style_dedup_window": "0"})
    assert await _load_recent_published_styles(sc) == []
