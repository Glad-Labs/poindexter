"""``video_providers.configured_animator`` — the one reading of
``video_generative_provider``.

The shot-list renderer builds its hero-clip provider from it and the Stage-2
dispatch gate picks its animator probe from it (2026-09-25), so the two can
never disagree about which server a hero clip needs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from poindexter.services.site_config import SiteConfig
from poindexter.services.video_providers import DEFAULT_ANIMATOR, configured_animator


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("comfyui", "comfyui"),
        ("  ComfyUI\n", "comfyui"),
        ("wan21", "wan21"),
        ("", "wan21"),
        # No provider by that name: the renderer builds Wan21Provider for
        # anything that is not comfyui, so that is what it resolves to.
        ("ltx", "wan21"),
    ],
)
def test_resolves_the_setting(value, expected):
    sc = SiteConfig(initial_config={"video_generative_provider": value})
    assert configured_animator(sc) == expected


@pytest.mark.unit
def test_unset_is_the_default_animator():
    assert DEFAULT_ANIMATOR == "wan21"
    assert configured_animator(SiteConfig(initial_config={})) == "wan21"


@pytest.mark.unit
def test_no_site_config_is_the_default_animator():
    assert configured_animator(None) == "wan21"


@pytest.mark.unit
def test_unreadable_setting_is_the_default_animator():
    sc = MagicMock()
    sc.get.side_effect = RuntimeError("settings cache not loaded")
    assert configured_animator(sc) == "wan21"
