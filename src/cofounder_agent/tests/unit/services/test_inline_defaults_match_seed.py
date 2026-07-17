"""Inline call-site defaults must agree with the seeded default.

The 2026-07-17 audit found `app_settings` defaults living in FIVE places:
`settings_defaults.py`, `0000_baseline.seeds.sql`, `brain/seed_app_settings.json`,
and — the one no lint can see — inline `site_config.get(key, default)` fallbacks
scattered through the code.

`scripts/ci/settings_seed_value_drift_lint.py` locks the first three together.
It structurally cannot see the fourth: an inline literal is an expression, not a
seed row. These tests are that half of the guard, for the two keys where the
drift was actively harmful:

* `podcast_tts_format` — the stage defaulted to `wav`, the synth call to `mp3`,
  so an unconfigured install wrote mp3 bytes into a `.wav` file. And `wav` is
  the *unrecoverable* Speaches failure (#1696/#1706): only segment 1's RIFF
  header survives, and `ffmpeg -c copy` remux truncates rather than repairs.
* `image_model` — the inline default said `sdxl_lightning` after #2386's
  bake-off moved the seeded default to `z_image_turbo`.

Asserting against `DEFAULTS` rather than a literal is deliberate: a hardcoded
expectation here would just be a *sixth* copy free to drift. This ties the
call site to the seed, so changing the seeded default either updates the call
site or fails loudly.

Scope note: `image_service.py` carries a SECOND `get_default_image_model` over a
parallel `ImageModel` enum + registry that describes the retired local-diffusers
render path (`pipeline_class="diffusers.*"`, `lora_repo`, `vram_gb`; the live
path is the HTTP image-gen server via `_image_models.py` — CLAUDE.md #603). Its
enum has no `z_image_turbo` member, so it can't return the prod default and is
excluded here; consolidating the duplicate is logged separately. These tests
pin the LIVE path.
"""

from __future__ import annotations

from services import tts_service
from services.image_providers._image_models import get_default_image_model
from services.settings_defaults import DEFAULTS


class _UnsetCfg:
    """A SiteConfig that has no rows — every read takes the caller's default."""

    def get(self, key: str, default=None):
        return default


def test_image_model_inline_default_matches_the_seeded_default() -> None:
    """get_default_image_model's fallback must track settings_defaults."""
    assert get_default_image_model(_UnsetCfg()).value == DEFAULTS["image_model"]


def test_image_model_default_with_no_site_config_matches_the_seed() -> None:
    """The dispatcher may not have seeded site_config yet; the None path must
    not diverge from the configured path."""
    assert get_default_image_model(None).value == DEFAULTS["image_model"]


def test_image_model_unknown_value_falls_back_to_the_seeded_default() -> None:
    """A typo'd app_settings value must land on the seeded default, not on a
    stale hardcoded one."""

    class _BadCfg:
        def get(self, key: str, default=None):
            return "not_a_real_model" if key == "image_model" else default

    assert get_default_image_model(_BadCfg()).value == DEFAULTS["image_model"]


def test_tts_format_inline_default_matches_the_seeded_default() -> None:
    """resolve_tts_format's fallback must track settings_defaults."""
    assert tts_service.resolve_tts_format(_UnsetCfg()) == DEFAULTS["podcast_tts_format"]


def test_tts_format_default_is_never_wav() -> None:
    """Belt-and-braces on the one value that corrupts audio irrecoverably —
    this must fail even if someone 'fixes' the drift by moving both to wav."""
    assert tts_service.resolve_tts_format(_UnsetCfg()) != "wav"
    assert DEFAULTS["podcast_tts_format"] != "wav"
