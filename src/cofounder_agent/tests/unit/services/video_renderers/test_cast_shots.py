"""Creative studio phase 1 (poindexter#1135): character sheets + cast-aware shots.

A cast shot is a ``presenter`` shot that names its ``speaker``. These pin:
the schema accepts and constrains the cast fields; a persona carries an
appearance that every still naming it inherits, in code; a cast shot lip-syncs
its own speaker; and the blog-format presenter rules (cap, no back-to-back,
niche availability) leave a dialogue alone.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from poindexter.schemas import video_shot_list as schema
from poindexter.schemas.video_shot_list import MAX_SHOTS, Shot, VideoShotList
from poindexter.services import character_prompt as cp
from poindexter.services import persona_service as ps
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr

DEX_LOOKS = "a cheerful young fox engineer, orange fur, round amber goggles, teal lab jacket"
NOVA_LOOKS = "a calm robot owl with a glowing blue visor"


def _shot(idx=0, source="presenter", **over):
    base = dict(idx=idx, duration_s=5.0, intent="a line", source=source,
                narration_offset_s=float(idx) * 5.0)
    if source in ("image_gen", "image_kenburns", "generative"):
        base["prompt"] = "a night-time server room"
    elif source == "pexels":
        base["query"] = "server room"
    base.update(over)
    return Shot(**base)


def _list(shots):
    return VideoShotList(
        total_duration_s=sum(s.duration_s for s in shots), shots=shots,
        director_model="test", director_prompt_version="v1",
        director_decided_at=datetime.now(timezone.utc),
    )


def _sc(**extra):
    base = {
        "persona.dex.display_name": "Dex",
        "persona.dex.appearance": DEX_LOOKS,
        "persona.dex.portrait_url": "https://cdn/personas/dex.png",
        "persona.dex.style_policy": "stylized",
        "persona.dex.enabled": "true",
        "persona.nova.display_name": "Nova",
        "persona.nova.description": NOVA_LOOKS,
        "persona.nova.portrait_url": "https://cdn/personas/nova.png",
        "persona.nova.enabled": "true",
        # The niche's own presenter is unavailable on purpose: cast shots
        # must not depend on it.
        "media_human_subjects": "none",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


# --- schema ---------------------------------------------------------------


class TestSchema:
    def test_cast_fields_are_optional_so_frozen_lists_still_validate(self):
        s = _shot(0, "pexels")
        assert s.characters == [] and s.speaker is None

    def test_characters_normalize_and_dedupe(self):
        s = _shot(0, "generative", characters=["Dex", "dex", "nova"])
        assert s.characters == ["dex", "nova"]

    @pytest.mark.parametrize("bad", ["../etc", "Dex Fox", "", "x" * 65])
    def test_characters_reject_non_slugs(self, bad):
        with pytest.raises(ValidationError):
            _shot(0, "generative", characters=[bad])

    def test_speaker_is_a_slug_and_blank_means_unset(self):
        assert _shot(0, speaker="  ").speaker is None
        assert _shot(0, speaker="Dex").speaker == "dex"
        with pytest.raises(ValidationError):
            _shot(0, speaker="dex/../x")

    def test_slug_shape_matches_persona_service(self):
        # Two copies of one rule: the schema stays dependency-free, so a test
        # holds them together.
        assert schema._PERSONA_SLUG_RE.pattern.lstrip("^").rstrip("$") == ps._SLUG_RE.pattern.lstrip("^").rstrip("$")

    def test_shot_cap_is_120(self):
        shots = [_shot(i, "image_kenburns" if i % 2 else "pexels") for i in range(MAX_SHOTS)]
        assert len(_list(shots).shots) == 120
        with pytest.raises(ValidationError):
            _list([*shots, _shot(MAX_SHOTS, "pexels")])

    def test_dialogue_may_cut_between_speakers_back_to_back(self):
        shots = [_shot(i, speaker=sp) for i, sp in enumerate(["dex", "nova", "dex", "nova"])]
        assert len(_list(shots).shots) == 4

    def test_one_speaker_three_times_running_still_drags(self):
        with pytest.raises(ValidationError, match="consecutive"):
            _list([_shot(i, speaker="dex") for i in range(3)])

    def test_untouched_rule_for_niche_presenter_shots(self):
        with pytest.raises(ValidationError, match="consecutive"):
            _list([_shot(i) for i in range(3)])


# --- persona character sheet ------------------------------------------------


class TestPersonaSheet:
    def test_fields_are_validated_persona_fields(self):
        assert {"appearance", "personality"} <= set(ps.FIELDS)
        assert ps.validate_field("appearance", "  a fox  ") == "a fox"

    def test_appearance_wins_and_description_is_the_fallback(self):
        sc = _sc()
        assert ps.get_persona(sc, "dex").visual_description == DEX_LOOKS
        assert ps.get_persona(sc, "nova").visual_description == NOVA_LOOKS

    def test_default_portrait_prompt_uses_the_appearance(self):
        assert DEX_LOOKS in ps.default_portrait_prompt(ps.get_persona(_sc(), "dex"))

    def test_personality_parses(self):
        p = ps.get_persona(_sc(**{"persona.dex.personality": "eager, punny"}), "dex")
        assert p.personality == "eager, punny"


# --- character prompts --------------------------------------------------------


class TestCharacterPrompt:
    def test_appearance_goes_in_front_of_the_scene(self):
        out = cp.apply_character_sheets([_shot(0, "generative", characters=["dex"])], _sc())
        assert out[0].prompt == f"Dex: {DEX_LOOKS}. a night-time server room"

    def test_two_characters_both_named(self):
        out = cp.apply_character_sheets([_shot(0, "image_kenburns", characters=["dex", "nova"])], _sc())
        assert out[0].prompt.startswith(f"Dex: {DEX_LOOKS}; Nova: {NOVA_LOOKS}. ")

    def test_composing_twice_is_a_no_op(self):
        once = cp.apply_character_sheets([_shot(0, "generative", characters=["dex"])], _sc())
        twice = cp.apply_character_sheets(once, _sc())
        assert twice[0].prompt == once[0].prompt

    def test_unknown_character_is_skipped_not_fatal(self, caplog):
        out = cp.apply_character_sheets([_shot(0, "generative", characters=["ghost"])], _sc())
        assert out[0].prompt == "a night-time server room"

    def test_sources_without_prompts_and_shots_without_characters_untouched(self):
        shots = [_shot(0, "pexels", characters=["dex"]), _shot(1, "generative")]
        out = cp.apply_character_sheets(shots, _sc())
        assert out[0] is shots[0] and out[1] is shots[1]

    def test_composed_prompt_stays_valid_length(self):
        sc = _sc(**{"persona.dex.appearance": "x" * 1990})
        out = cp.apply_character_sheets([_shot(0, "generative", characters=["dex"])], sc)
        assert len(out[0].prompt) <= cp.MAX_PROMPT_CHARS


# --- renderer -----------------------------------------------------------------


class TestPresenterRulesSkipCastShots:
    def test_cap_neither_counts_nor_downgrades_cast_shots(self):
        shots = [_shot(0, speaker="dex"), _shot(1, speaker="nova"), _shot(2)]
        out = slr._cap_presenter_shots(shots, 0, available=False)
        assert [s.source for s in out] == ["presenter", "presenter", "image_kenburns"]

    def test_tidy_leaves_a_dialogue_alone(self):
        shots = [_shot(i, speaker=sp) for i, sp in enumerate(["dex", "nova", "dex"])]
        # policy=None: the dialogue check returns before the policy is read.
        assert slr._tidy_presenter_sequence(shots, None) is shots


@pytest.fixture
def quiet_gpu(monkeypatch):
    monkeypatch.setattr(slr, "_reclaim_card_for_presenter", AsyncMock())
    monkeypatch.setattr(slr, "_live_free_vram_gb", AsyncMock(return_value=30.0))
    monkeypatch.setattr(slr, "_comfyui_reserved_gb", AsyncMock(return_value=0.0))
    monkeypatch.setattr(slr.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(slr, "emit_finding", lambda **kw: None)


def _stub_render(monkeypatch, seen):
    async def fake_fetch(url, dest, factory):
        seen["portrait_url"] = url
        dest.write_bytes(b"PNG")
        return str(dest)

    async def fake_cut(src, dst, **kw):
        open(dst, "wb").write(b"WAV")
        return True

    async def fake_render(**kw):
        seen.update(kw)
        open(kw["output_path"], "wb").write(b"MP4")
        return True, ""

    monkeypatch.setattr(slr, "_fetch_presenter_portrait", fake_fetch)
    monkeypatch.setattr(slr, "_cut_narration_window", fake_cut)
    monkeypatch.setattr(slr, "_render_generative_clip", fake_render)


class TestCastShotRender:
    @pytest.mark.asyncio
    async def test_speaker_lip_syncs_their_own_portrait(self, tmp_path, quiet_gpu, monkeypatch):
        narration = tmp_path / "dialogue.wav"
        narration.write_bytes(b"WAV")
        seen: dict = {}
        _stub_render(monkeypatch, seen)
        result = await slr._render_one_shot(
            _shot(1, speaker="nova"), prior_clip=None, work_dir=tmp_path, image_gen_url="",
            site_config=_sc(), http_client_factory=None, orientation="landscape", post_id="t1",
            narration_path=str(narration), niche_slug="glad-labs",
        )
        assert result.success is True
        assert seen["portrait_url"] == "https://cdn/personas/nova.png"
        assert seen["image_path"].endswith("presenter_nova.png")
        assert re.match(r"Nova ", seen["prompt"])

    @pytest.mark.asyncio
    async def test_unknown_speaker_falls_back_with_a_reason(self, tmp_path, quiet_gpu):
        result = await slr._render_one_shot(
            _shot(0, speaker="ghost"), prior_clip=None, work_dir=tmp_path, image_gen_url="",
            site_config=_sc(), http_client_factory=None, narration_path=None, niche_slug=None,
        )
        assert result.success is False and "ghost" in (result.error or "")

    @pytest.mark.asyncio
    async def test_speaker_without_portrait_names_the_fix(self, tmp_path, quiet_gpu):
        sc = _sc(**{"persona.nova.portrait_url": ""})
        result = await slr._render_one_shot(
            _shot(0, speaker="nova"), prior_clip=None, work_dir=tmp_path, image_gen_url="",
            site_config=sc, http_client_factory=None, narration_path=None, niche_slug=None,
        )
        assert result.success is False and "personas portrait nova" in (result.error or "")
