"""A cast character's lip-sync prompt names their looks and asks for every syllable.

Midnight Patch (2026-10-14): Bolt, a robot mouse, barely moved his tiny mouth
("small natural mouth movements") and his lens eyes grew pupils that came and
went. A/B on two of his lines: naming his looks held them steady.
"""

from __future__ import annotations

from types import SimpleNamespace

from poindexter.schemas.video_shot_list import Shot
from poindexter.services.settings_defaults import DEFAULTS
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr

BOLT = SimpleNamespace(slug="bolt", display_name="Bolt", render_prompt_suffix="",
                       appearance="A small chrome mouse robot with big round orange lens eyes")


def _shot(speaker="bolt"):
    return Shot(idx=2, duration_s=3.0, intent="x", source="presenter", narration_offset_s=0.0,
                speaker=speaker, characters=[speaker], prompt="anxious", scene_id="s1")


def test_a_cast_shot_names_the_characters_looks():
    prompt = slr._compose_presenter_prompt(_shot(), BOLT, SiteConfig(initial_config={}))
    assert "follow every syllable" in prompt and "small natural mouth" not in prompt
    assert "Bolt looks exactly as in the picture: A small chrome mouse robot with big round orange lens eyes" in prompt
    assert prompt.endswith("anxious.")


def test_the_looks_clause_is_a_setting_and_can_be_emptied():
    sc = SiteConfig(initial_config={"video_cast_appearance_template": ""})
    assert "looks exactly" not in slr._compose_presenter_prompt(_shot(), BOLT, sc)
    custom = SiteConfig(initial_config={"video_cast_appearance_template": "{display_name} is {appearance}"})
    assert "Bolt is A small chrome mouse robot" in slr._compose_presenter_prompt(_shot(), BOLT, custom)


def test_the_code_default_matches_the_registered_one():
    assert DEFAULTS["video_cast_render_prompt"].startswith("{display_name} talks in the scene")
    assert "{appearance}" in DEFAULTS["video_cast_appearance_template"]


def test_the_migration_moves_only_the_old_default():
    import importlib
    import pathlib

    path = next(pathlib.Path(slr.__file__).parents[1].joinpath("migrations").glob("*cartoon_lip_sync_follows*.py"))
    mod = importlib.import_module(f"poindexter.services.migrations.{path.stem}")
    assert mod.NEW == DEFAULTS["video_cast_render_prompt"] and mod.OLD != mod.NEW
    assert "small natural mouth" in mod.OLD
