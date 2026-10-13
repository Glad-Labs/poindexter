"""Creative studio phase 7 (poindexter#1141): frames drawn from the cast's portraits.

The art style switched whenever a character spoke because talking shots used a
fixed portrait from another model. These pin the edit-model graph, how the
model is told which picture is whom, that screenplay stills and talking frames
go through it with the right references, that the card is cleared once per
render, and that every failure falls back to the old path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services import reference_frames as rf
from poindexter.services import screenplay_director as sd
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr


def _sc(**extra):
    base = {
        "persona.dex.display_name": "Dex", "persona.dex.enabled": "true",
        "persona.dex.portrait_url": "https://cdn/dex.png",
        "persona.nova.display_name": "Nova", "persona.nova.enabled": "true",
        "persona.nova.portrait_url": "https://cdn/nova.png",
        "persona.pip.display_name": "Pip", "persona.pip.enabled": "true",  # no portrait
        "video_reference_lora": "lightning.safetensors",
        "video_reference_latent_method": "index_timestep_zero",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


def _shot(idx=0, source="image_kenburns", **over):
    base = dict(idx=idx, duration_s=3.0, intent="x", source=source, narration_offset_s=0.0)
    if source != "presenter":
        base["prompt"] = "a garage at sunrise"
    base.update(over)
    return Shot(**base)


class TestGraph:
    def test_shape_with_lora_and_reference_latents(self):
        g = rf.build_reference_graph(prompt="p", references=["a.png", "b.png"], width=1024,
                                     height=576, seed=7, site_config=_sc())
        assert g["2"]["inputs"]["lora_name"] == "lightning.safetensors"
        assert g["30"]["inputs"]["image1"] == ["20", 0] and g["30"]["inputs"]["image2"] == ["21", 0]
        assert "image3" not in g["30"]["inputs"]
        assert g["32"]["inputs"]["reference_latents_method"] == "index_timestep_zero"
        assert g["50"]["inputs"]["positive"] == ["32", 0] and g["50"]["inputs"]["seed"] == 7
        assert g["40"]["inputs"]["width"] == 1024 and g["40"]["inputs"]["height"] == 576

    def test_optional_nodes_drop_out(self):
        g = rf.build_reference_graph(prompt="p", references=["a.png"], width=8, height=8, seed=1,
                                     site_config=_sc(video_reference_lora="", video_reference_latent_method=""))
        assert "2" not in g and "32" not in g
        assert g["3"]["inputs"]["model"] == ["1", 0] and g["50"]["inputs"]["positive"] == ["30", 0]

    def test_at_most_three_references_and_at_least_one(self):
        g = rf.build_reference_graph(prompt="p", references=list("abcd"), width=8, height=8, seed=1, site_config=_sc())
        assert "13" not in g
        with pytest.raises(ValueError):
            rf.build_reference_graph(prompt="p", references=[], width=8, height=8, seed=1, site_config=_sc())


class TestPrompt:
    def test_names_each_picture(self):
        text = rf.compose_reference_prompt("at a desk", ["Dex", "Nova"], style_only=False, site_config=_sc())
        assert text.startswith("Picture 1 is Dex. Picture 2 is Nova.") and text.endswith("at a desk")

    def test_style_only_keeps_the_characters_out(self):
        text = rf.compose_reference_prompt("an empty server aisle", [], style_only=True, site_config=_sc())
        assert "without the characters" in text and text.endswith("an empty server aisle")

    def test_templates_are_settings(self):
        sc = _sc(video_reference_name_template="#{n}={name}", video_reference_prompt_template="{references} | {prompt}")
        assert rf.compose_reference_prompt("x", ["Dex"], style_only=False, site_config=sc) == "#1=Dex | x"

    @pytest.mark.asyncio
    async def test_no_reference_on_disk_is_a_clean_miss(self, tmp_path):
        ok, why = await rf.render_reference_frame(
            prompt="p", reference_paths=[str(tmp_path / "missing.png")], output_path=str(tmp_path / "o.png"),
            width=8, height=8, site_config=_sc())
        assert not ok and "no reference" in why


@pytest.fixture
def stub_refs(monkeypatch, tmp_path):
    seen: dict = {"calls": []}

    async def fetch(url, dest, factory):
        Path(dest).write_bytes(b"PNG")
        return str(dest)

    async def draw(**kw):
        seen["calls"].append(kw)
        Path(kw["output_path"]).write_bytes(b"PNG")
        return True, ""

    clear = AsyncMock()
    monkeypatch.setattr(slr, "_fetch_presenter_portrait", fetch)
    monkeypatch.setattr(rf, "render_reference_frame", draw)
    monkeypatch.setattr(slr, "_clear_image_gen_for_hero", clear)
    seen["clear"] = clear
    return seen


def test_reference_context_only_for_exact_lists_with_the_whole_cast():
    from datetime import datetime, timezone

    from poindexter.schemas.video_shot_list import VideoShotList

    shots = [_shot(0, characters=["nova"]), _shot(1, "presenter", speaker="dex"), _shot(2, characters=["dex", "pip"])]
    base = dict(total_duration_s=9.0, shots=shots, director_model="m", director_prompt_version="v",
                director_decided_at=datetime.now(timezone.utc))
    assert slr.reference_context_for(VideoShotList(**base)) is None  # timing defaults to "fit"
    ctx = slr.reference_context_for(VideoShotList(**base, timing="exact"))
    assert ctx.cast == ("nova", "dex", "pip")


class TestReferenceStill:
    @pytest.mark.asyncio
    async def test_characters_are_the_references(self, stub_refs, tmp_path):
        ctx = slr.ReferenceContext(cast=("dex", "nova"))
        ok = await slr._render_reference_still(
            _shot(characters=["nova", "pip"]), prompt="a garage", output_path=str(tmp_path / "s.png"),
            width=1024, height=576, ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        assert ok
        call = stub_refs["calls"][0]
        assert [Path(p).name for p in call["reference_paths"]] == ["reference_nova.png"]  # pip has no portrait
        assert call["prompt"].startswith("Picture 1 is Nova.")

    @pytest.mark.asyncio
    async def test_a_character_named_in_the_prompt_is_a_reference_too(self, stub_refs, tmp_path):
        ctx = slr.ReferenceContext(cast=("dex", "nova"))
        await slr._render_reference_still(
            _shot(characters=["nova"]), prompt="close-up of nova explaining to Dex", output_path=str(tmp_path / "s.png"),
            width=8, height=8, ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        names = [Path(p).name for p in stub_refs["calls"][0]["reference_paths"]]
        assert names == ["reference_nova.png", "reference_dex.png"]

    @pytest.mark.asyncio
    async def test_a_shot_with_nobody_uses_the_cast_for_style_only(self, stub_refs, tmp_path):
        ctx = slr.ReferenceContext(cast=("dex", "nova"))
        await slr._render_reference_still(
            _shot(), prompt="an empty aisle", output_path=str(tmp_path / "s.png"), width=8, height=8,
            ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        call = stub_refs["calls"][0]
        assert len(call["reference_paths"]) == 2 and "without the characters" in call["prompt"]

    @pytest.mark.asyncio
    async def test_card_cleared_once_and_portraits_cached(self, stub_refs, tmp_path):
        ctx = slr.ReferenceContext(cast=("dex",))
        for i in range(3):
            await slr._render_reference_still(
                _shot(i, characters=["dex"]), prompt="p", output_path=str(tmp_path / f"{i}.png"),
                width=8, height=8, ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        assert stub_refs["clear"].await_count == 1 and list(ctx.portraits) == ["dex"]

    @pytest.mark.asyncio
    async def test_no_context_or_disabled_means_text_path(self, stub_refs, tmp_path):
        args = dict(prompt="p", output_path=str(tmp_path / "s.png"), width=8, height=8,
                    work_dir=tmp_path, http_client_factory=None)
        assert not await slr._render_reference_still(_shot(characters=["dex"]), ctx=None, site_config=_sc(), **args)
        ctx = slr.ReferenceContext(cast=("dex",))
        off = _sc(video_reference_frames_enabled="false")
        assert not await slr._render_reference_still(_shot(characters=["dex"]), ctx=ctx, site_config=off, **args)
        assert stub_refs["calls"] == []


class TestStillBranch:
    @pytest.mark.asyncio
    async def test_reference_failure_falls_back_to_image_gen(self, monkeypatch, tmp_path):
        monkeypatch.setattr(slr, "_render_reference_still", AsyncMock(return_value=False))
        image_gen = AsyncMock(return_value=True)
        monkeypatch.setattr(slr, "_render_image_gen_image", image_gen)
        res = await slr._render_one_shot(
            _shot(characters=["dex"]), prior_clip=None, work_dir=tmp_path, image_gen_url="http://x",
            site_config=_sc(), http_client_factory=None, reference_ctx=slr.ReferenceContext(cast=("dex",)))
        assert res.success and image_gen.await_count == 1

    @pytest.mark.asyncio
    async def test_reference_success_skips_image_gen(self, monkeypatch, tmp_path):
        monkeypatch.setattr(slr, "_render_reference_still", AsyncMock(return_value=True))
        image_gen = AsyncMock(return_value=True)
        monkeypatch.setattr(slr, "_render_image_gen_image", image_gen)
        res = await slr._render_one_shot(
            _shot(characters=["dex"]), prior_clip=None, work_dir=tmp_path, image_gen_url="http://x",
            site_config=_sc(), http_client_factory=None, reference_ctx=slr.ReferenceContext(cast=("dex",)))
        assert res.success and image_gen.await_count == 0


class TestTalkingFrame:
    @pytest.mark.asyncio
    async def test_cast_shot_lip_syncs_its_scene_frame(self, stub_refs, monkeypatch, tmp_path):
        narration = tmp_path / "d.wav"
        narration.write_bytes(b"WAV")
        seen = {}

        async def cut(src, dst, **kw):
            Path(dst).write_bytes(b"WAV")
            return True

        async def render(**kw):
            seen.update(kw)
            Path(kw["output_path"]).write_bytes(b"MP4")
            return True, ""

        monkeypatch.setattr(slr, "_cut_narration_window", cut)
        monkeypatch.setattr(slr, "_render_generative_clip", render)
        monkeypatch.setattr(slr, "_reclaim_card_for_presenter", AsyncMock())
        monkeypatch.setattr(slr, "_live_free_vram_gb", AsyncMock(return_value=30.0))
        monkeypatch.setattr(slr, "_comfyui_reserved_gb", AsyncMock(return_value=0.0))
        monkeypatch.setattr(slr, "emit_finding", lambda **kw: None)
        shot = _shot(0, "presenter", speaker="nova", frame_prompt="anime, Nova talking in a garage")
        res = await slr._render_one_shot(
            shot, prior_clip=None, work_dir=tmp_path, image_gen_url="", site_config=_sc(),
            http_client_factory=None, narration_path=str(narration),
            reference_ctx=slr.ReferenceContext(cast=("dex", "nova")))
        assert res.success
        assert Path(seen["image_path"]).name.startswith("talkframe_nova_")
        assert [Path(p).name for p in stub_refs["calls"][0]["reference_paths"]] == ["reference_nova.png"]


def test_director_writes_a_talking_frame_per_cast_shot():
    shot = {"speaker": "nova", "delivery": "patient", "setting": "a garage at sunrise"}
    text = sd._talking_frame_prompt(shot, "flat 2D cartoon", _sc())
    # One frame per speaker per scene: the default leaves delivery out.
    assert text == ("flat 2D cartoon, medium close-up of Nova alone in the frame, facing the viewer "
                    "and talking, in a garage at sunrise")
    custom = sd._talking_frame_prompt(shot, "x", _sc(story_talking_frame_template="{name}/{delivery}"))
    assert custom == "Nova/patient"


def test_unseeded_settings_fall_back_to_the_registered_defaults():
    """A database that predates these keys (any install before the deploy that
    seeds them) must still get the Lightning LoRA and its 4-step regime: a ""
    fallback drew mush on the 2026-10-13 verification render."""
    from poindexter.services.settings_defaults import DEFAULTS

    g = rf.build_reference_graph(prompt="p", references=["a.png"], width=8, height=8, seed=1,
                                 site_config=SiteConfig(initial_config={}))
    assert g["2"]["inputs"]["lora_name"] == DEFAULTS["video_reference_lora"]
    assert g["50"]["inputs"]["steps"] == int(DEFAULTS["video_reference_steps"])
    assert g["32"]["inputs"]["reference_latents_method"] == DEFAULTS["video_reference_latent_method"]
    assert rf.enabled(SiteConfig(initial_config={})) is True
