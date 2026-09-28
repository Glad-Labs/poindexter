"""Inline call-site defaults must agree with the seeded default.

The 2026-07-17 audit found `app_settings` defaults living in FIVE places:
`settings_defaults.py`, `0000_baseline.seeds.sql`, `brain/seed_app_settings.json`,
and — the one no lint can see — inline `site_config.get(key, default)` fallbacks
scattered through the code.

`scripts/ci/settings_seed_value_drift_lint.py` locks the first three together.
It structurally cannot see the fourth: an inline literal is an expression, not a
seed row. These tests are that half of the guard, for the key where the drift
was actively harmful:

* `podcast_tts_format` — the stage defaulted to `wav`, the synth call to `mp3`,
  so an unconfigured install wrote mp3 bytes into a `.wav` file. And `wav` is
  the *unrecoverable* Speaches failure (#1696/#1706): only segment 1's RIFF
  header survives, and `ffmpeg -c copy` remux truncates rather than repairs.

Asserting against `DEFAULTS` rather than a literal is deliberate: a hardcoded
expectation here would just be a *sixth* copy free to drift. This ties the
call site to the seed, so changing the seeded default either updates the call
site or fails loudly.

`image_model` was the second key guarded here: its resolver's inline fallback
said `sdxl_lightning` after #2386's bake-off moved the seed to `z_image_turbo`.
The key, and the worker-side registry and resolver behind it, were retired on
2026-09-28 because nothing chose a model from them; the image-gen server reads
`image_generation_model`. Its seeds are held to the server's REGISTRY by
`tests/unit/services/test_image_generation_model_seed.py`.
"""

from __future__ import annotations

from poindexter.services import tts_service
from poindexter.services.settings_defaults import DEFAULTS


class _UnsetCfg:
    """A SiteConfig that has no rows — every read takes the caller's default."""

    def get(self, key: str, default=None):
        return default


def test_tts_format_inline_default_matches_the_seeded_default() -> None:
    """resolve_tts_format's fallback must track settings_defaults."""
    assert tts_service.resolve_tts_format(_UnsetCfg()) == DEFAULTS["podcast_tts_format"]


def test_tts_format_default_is_never_wav() -> None:
    """Belt-and-braces on the one value that corrupts audio irrecoverably —
    this must fail even if someone 'fixes' the drift by moving both to wav."""
    assert tts_service.resolve_tts_format(_UnsetCfg()) != "wav"
    assert DEFAULTS["podcast_tts_format"] != "wav"
