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


RULE = "with no letters or words."


class TestPrompt:
    def test_names_each_picture(self):
        text = rf.compose_reference_prompt("at a desk", ["Dex", "Nova"], style_only=False, site_config=_sc())
        assert text.startswith("Picture 1 is Dex. Picture 2 is Nova.") and "at a desk Each character keeps" in text

    def test_style_only_keeps_the_characters_out(self):
        text = rf.compose_reference_prompt("an empty server aisle", [], style_only=True, site_config=_sc())
        assert "without the characters" in text and text.endswith(RULE)

    def test_templates_are_settings(self):
        sc = _sc(video_reference_name_template="#{n}={name}", video_reference_prompt_template="{references} | {prompt}",
                 video_reference_text_rule="")
        assert rf.compose_reference_prompt("x", ["Dex"], style_only=False, site_config=sc) == "#1=Dex | x"

    def test_every_picture_is_told_to_keep_writing_out(self):
        for style_only in (True, False):
            assert rf.compose_reference_prompt("a neon street", ["Dex"], style_only=style_only,
                                               site_config=_sc()).endswith(RULE)

    def test_characters_drawn_together_keep_their_own_marks(self):
        """Mara wore Cinder's grease mark in every shot they shared (2026-10-14)."""
        two = rf.compose_reference_prompt("at the dock", ["Cinder", "Mara"], style_only=False, site_config=_sc())
        one = rf.compose_reference_prompt("at the dock", ["Cinder"], style_only=False, site_config=_sc())
        assert "nothing on one character appears on another" in two
        assert "nothing on one character appears on another" not in one

    def test_the_place_is_the_picture_after_the_cast(self):
        text = rf.compose_reference_prompt("Dex talks", ["Dex"], style_only=False, site_config=_sc(), place=True)
        assert text.startswith("Picture 2 shows the place") and "Picture 1 is Dex" in text

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
    assert ctx.portrait_urls == {}
    styled = slr.reference_context_for(
        VideoShotList(**base, timing="exact", cast_portraits={"dex": "https://cdn/dex-ink.png"}))
    assert styled.portrait_urls == {"dex": "https://cdn/dex-ink.png"}


@pytest.mark.asyncio
async def test_a_per_piece_portrait_wins_over_the_persona_portrait(monkeypatch, tmp_path):
    """A reused character redrawn in this piece's style (story.cast_characters)
    is drawn from that redraw, not from the portrait made for another piece."""
    fetched: list[str] = []

    async def fetch(url, dest, factory):
        fetched.append(url)
        Path(dest).write_bytes(b"PNG")
        return str(dest)

    monkeypatch.setattr(slr, "_fetch_presenter_portrait", fetch)
    ctx = slr.ReferenceContext(cast=("dex", "nova"), portrait_urls={"dex": "https://cdn/dex-ink.png"})
    paths, names = await slr._reference_portraits(
        ["dex", "nova", "pip"], ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
    assert fetched == ["https://cdn/dex-ink.png", "https://cdn/nova.png"]
    assert names == ["Dex", "Nova"] and len(paths) == 2


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
    async def test_the_same_picture_in_one_pass_is_drawn_once(self, stub_refs, tmp_path):
        """A scene's master shot recurs between lines; it must be one picture,
        not a new draw each time. A re-roll (its own repair dir) draws fresh."""
        ctx = slr.ReferenceContext(cast=("dex", "nova"))
        args = dict(width=8, height=8, ctx=ctx, site_config=_sc(), http_client_factory=None)
        master = "anime, wide shot of Dex and Nova in a lab"
        for i in (3, 7):
            assert await slr._render_reference_still(
                _shot(i, characters=["dex", "nova"]), prompt=master,
                output_path=str(tmp_path / f"shot_{i}.png"), work_dir=tmp_path, **args)
        assert len(stub_refs["calls"]) == 1
        assert (tmp_path / "shot_7.png").read_bytes() == (tmp_path / "shot_3.png").read_bytes()
        repair = tmp_path / "repair1"
        repair.mkdir()
        await slr._render_reference_still(
            _shot(7, characters=["dex", "nova"]), prompt=master,
            output_path=str(repair / "shot_7.png"), work_dir=repair, **args)
        assert len(stub_refs["calls"]) == 2

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
    async def test_a_cartoon_never_falls_back_to_the_text_path(self, monkeypatch, tmp_path):
        """Matt, 2026-10-14: no fallbacks in the videos. A text-to-image still
        is another style; the shot fails instead, to be drawn again."""
        monkeypatch.setattr(slr, "_render_reference_still", AsyncMock(return_value=False))
        image_gen = AsyncMock(return_value=True)
        monkeypatch.setattr(slr, "_render_image_gen_image", image_gen)
        args = dict(prior_clip=None, work_dir=tmp_path, image_gen_url="http://x", http_client_factory=None,
                    reference_ctx=slr.ReferenceContext(cast=("dex",)))
        res = await slr._render_one_shot(_shot(characters=["dex"]), site_config=_sc(), **args)
        assert not res.success and image_gen.await_count == 0
        allowed = await slr._render_one_shot(_shot(characters=["dex"]), site_config=_sc(story_allow_fallbacks="true"), **args)
        assert allowed.success and image_gen.await_count == 1

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


class TestTalkingFrameCheck:
    """A talking frame with anyone but the speaker in it is redrawn (a19363a9:
    a stranger sat beside Mira through every line of the closing scene)."""

    async def _render(self, monkeypatch, tmp_path, verdicts, **sc):
        from poindexter.services.video_renderers import shot_vision_qa as svq

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

        check = AsyncMock(side_effect=verdicts)
        monkeypatch.setattr(svq, "check_talking_frame", check)
        monkeypatch.setattr(slr, "_cut_narration_window", cut)
        monkeypatch.setattr(slr, "_render_generative_clip", render)
        monkeypatch.setattr(slr, "_reclaim_card_for_presenter", AsyncMock())
        monkeypatch.setattr(slr, "_live_free_vram_gb", AsyncMock(return_value=30.0))
        monkeypatch.setattr(slr, "_comfyui_reserved_gb", AsyncMock(return_value=0.0))
        monkeypatch.setattr(slr, "emit_finding", lambda **kw: None)
        shot = _shot(0, "presenter", speaker="nova", frame_prompt="anime, Nova talking in a lab")
        ctx = slr.ReferenceContext(cast=("dex", "nova"), pool=object())
        res = await slr._render_one_shot(
            shot, prior_clip=None, work_dir=tmp_path, image_gen_url="", site_config=_sc(**sc),
            http_client_factory=None, narration_path=str(narration), reference_ctx=ctx)
        assert res.success
        return Path(seen["image_path"]).name, check

    @pytest.mark.asyncio
    async def test_a_rejected_draw_is_redrawn_and_the_good_one_used(self, stub_refs, monkeypatch, tmp_path):
        from poindexter.services.video_renderers.shot_vision_qa import TalkingFrameVerdict as V

        used, check = await self._render(
            monkeypatch, tmp_path, [V(ok=False, people=2, reason="a man beside her"), V(ok=True, people=1)])
        assert used.startswith("talkframe_nova_")
        assert len(stub_refs["calls"]) == 2
        assert check.await_args.kwargs["name"] == "Nova"

    @pytest.mark.asyncio
    async def test_every_draw_rejected_keeps_the_last_draw(self, stub_refs, monkeypatch, tmp_path):
        """A cartoon keeps its best scene frame rather than the bare portrait in
        front of a blank wall; with fallbacks allowed, the portrait stands in."""
        from poindexter.services.video_renderers.shot_vision_qa import TalkingFrameVerdict as V

        used, _ = await self._render(
            monkeypatch, tmp_path, [V(ok=False, people=2)] * 2,
            video_cast_talking_frame_attempts="2")
        assert used.startswith("talkframe_nova_")
        assert len(stub_refs["calls"]) == 2

    @pytest.mark.asyncio
    async def test_with_fallbacks_allowed_the_portrait_stands_in(self, stub_refs, monkeypatch, tmp_path):
        from poindexter.services.video_renderers.shot_vision_qa import TalkingFrameVerdict as V

        used, _ = await self._render(
            monkeypatch, tmp_path, [V(ok=False, people=2)] * 2,
            video_cast_talking_frame_attempts="2", story_allow_fallbacks="true")
        assert used == "presenter_nova.png"

    @pytest.mark.asyncio
    async def test_a_check_that_cannot_answer_keeps_the_frame(self, stub_refs, monkeypatch, tmp_path):
        from poindexter.services.video_renderers.shot_vision_qa import TalkingFrameVerdict as V

        used, _ = await self._render(monkeypatch, tmp_path, [V(ok=None, reason="vision call failed")])
        assert used.startswith("talkframe_nova_")
        assert len(stub_refs["calls"]) == 1


class TestTalkingFrameVerdictParse:
    def test_one_person_who_is_the_speaker_passes(self):
        from poindexter.services.video_renderers.shot_vision_qa import _parse_talking_frame

        v = _parse_talking_frame('{"people": 1, "is_character": true, "reason": "Mira alone"}')
        assert v.ok is True and v.people == 1

    @pytest.mark.parametrize("reply", [
        '{"people": 2, "is_character": true, "reason": "a man beside her"}',
        '{"people": 1, "is_character": false, "reason": "a different woman"}',
        '{"people": 0, "is_character": false}',
    ])
    def test_extra_people_or_the_wrong_character_fail(self, reply):
        from poindexter.services.video_renderers.shot_vision_qa import _parse_talking_frame

        assert _parse_talking_frame(reply).ok is False

    def test_the_speaker_alone_passes_whatever_they_are(self):
        """Bolt, a robot mouse, read as 0 people; the judge now counts others."""
        from poindexter.services.video_renderers.shot_vision_qa import _parse_talking_frame

        assert _parse_talking_frame('{"is_character": true, "others": 0, "reason": "Bolt alone"}').ok is True
        assert _parse_talking_frame('{"is_character": true, "others": 1, "reason": "a man behind"}').ok is False
        assert _parse_talking_frame('{"is_character": false, "others": 0}').ok is False

    def test_stray_hands_fail_a_frame(self):
        """Midnight Patch v3: human hands reached up from the bottom of Bolt's
        and Mara's talking frames."""
        from poindexter.services.video_renderers.shot_vision_qa import _parse_talking_frame

        v = _parse_talking_frame('{"is_character": true, "others": 0, "hands_ok": false, "reason": "hands at the bottom"}')
        assert v.ok is False
        assert _parse_talking_frame('{"is_character": true, "others": 0, "hands_ok": true}').ok is True

    def test_writing_is_reported_but_never_fails_a_frame(self):
        """The edit model draws signs into every neon street; three redraws
        never removed one, so the check only cost draws."""
        from poindexter.services.video_renderers.shot_vision_qa import (
            _parse_cast_frame,
            _parse_talking_frame,
        )

        v = _parse_talking_frame('{"is_character": true, "others": 0, "writing_ok": false, "reason": "a sign"}')
        assert v.ok is True and v.reason.startswith("scrambled writing")
        assert _parse_cast_frame(
            '{"people": 2, "strangers": 0, "all_present": true, "writing_ok": false, "reason": "x"}', 2).ok is True

    def test_the_judge_counts_characters_not_just_people(self):
        """Bolt, a robot mouse, was counted as 0 people, so every Bolt frame
        failed and he spoke from his white-background portrait."""
        from poindexter.services.prompt_manager import get_prompt_manager

        text = get_prompt_manager().get_prompt("qa.talking_frame", name="Bolt", appearance="a chrome mouse robot")
        assert "robots and creatures" in text and "besides Bolt, how many other characters" in text

    @pytest.mark.parametrize("reply", ["no idea", '{"is_character": true}', '{"people": "one"}'])
    def test_an_unreadable_reply_is_no_verdict(self, reply):
        from poindexter.services.video_renderers.shot_vision_qa import _parse_talking_frame

        assert _parse_talking_frame(reply).ok is None

    @pytest.mark.asyncio
    async def test_no_pool_is_no_verdict(self, tmp_path):
        from poindexter.services.video_renderers.shot_vision_qa import check_talking_frame

        v = await check_talking_frame(frame_path=str(tmp_path / "f.png"), name="Mira", appearance="",
                                      site_config=_sc(qa_vision_model="m"), pool=None)
        assert v.ok is None


def test_director_writes_a_talking_frame_per_cast_shot():
    shot = {"speaker": "nova", "delivery": "patient", "setting": "a garage at sunrise"}
    text = sd._talking_frame_prompt(shot, "flat 2D cartoon", _sc())
    # One frame per speaker per scene: the default leaves delivery out.
    assert text == ("flat 2D cartoon, head-and-shoulders close-up of Nova, facing the viewer, the picture "
                    "cropped just below the shoulders, alone in the frame and talking, in a garage at sunrise")
    # The crop is described, never the hands: naming them drew them.
    assert "hand" not in text
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


class TestStillCastCheck:
    """A still with characters must show them and no stranger (a19363a9's
    re-direct drew a man nobody wrote into a reaction shot)."""

    @pytest.mark.asyncio
    async def test_a_still_with_a_stranger_is_redrawn(self, stub_refs, monkeypatch, tmp_path):
        from poindexter.services.video_renderers import shot_vision_qa as svq
        from poindexter.services.video_renderers.shot_vision_qa import TalkingFrameVerdict as V

        check = AsyncMock(side_effect=[V(ok=False, people=3, reason="a man in a white coat"), V(ok=True, people=2)])
        monkeypatch.setattr(svq, "check_cast_frame", check)
        ctx = slr.ReferenceContext(cast=("dex", "nova"), pool=object())
        ok = await slr._render_reference_still(
            _shot(4, characters=["nova"]), prompt="nova listening", output_path=str(tmp_path / "s.png"),
            width=8, height=8, ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        assert ok and len(stub_refs["calls"]) == 2
        kw = check.await_args.kwargs
        assert kw["required"] == [("Nova", "")]
        assert [n for n, _ in kw["cast"]] == ["Dex", "Nova"]

    @pytest.mark.asyncio
    async def test_no_pool_or_no_characters_skips_the_check(self, stub_refs, monkeypatch, tmp_path):
        from poindexter.services.video_renderers import shot_vision_qa as svq

        check = AsyncMock()
        monkeypatch.setattr(svq, "check_cast_frame", check)
        args = dict(width=8, height=8, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        await slr._render_reference_still(
            _shot(1, characters=["nova"]), prompt="p", output_path=str(tmp_path / "a.png"),
            ctx=slr.ReferenceContext(cast=("nova",)), **args)
        await slr._render_reference_still(
            _shot(2), prompt="an empty lab", output_path=str(tmp_path / "b.png"),
            ctx=slr.ReferenceContext(cast=("nova",), pool=object()), **args)
        check.assert_not_awaited()


class TestCastFrameParse:
    @pytest.mark.parametrize("reply,ok", [
        ('{"people": 2, "strangers": 0, "all_present": true}', True),
        ('{"people": 1, "strangers": 0, "all_present": true}', True),
        ('{"people": 2, "strangers": 1, "all_present": true}', False),
        ('{"people": 3, "strangers": 0, "all_present": true}', False),
        ('{"people": 2, "strangers": 0, "all_present": false}', False),
        ('{"people": 2, "all_present": true}', None),
        ("no idea", None),
    ])
    def test_verdict(self, reply, ok):
        from poindexter.services.video_renderers.shot_vision_qa import _parse_cast_frame

        assert _parse_cast_frame(reply, cast_size=2).ok is ok


class TestScenePlace:
    """Backdrop continuity: a scene's establishing picture is the place
    reference for its other pictures, so close-ups keep one background."""

    @pytest.mark.asyncio
    async def test_a_close_up_is_drawn_in_its_scenes_place(self, stub_refs, tmp_path):
        est = Shot(**{**_shot(0, characters=["dex", "nova"]).model_dump(), "scene_id": "s1", "establishing": True,
                      "prompt": "wide shot of the lab"})
        close = Shot(**{**_shot(1, characters=["nova"]).model_dump(), "scene_id": "s1"})
        ctx = slr.ReferenceContext(cast=("dex", "nova"), establishing={"s1": est})
        await slr._render_reference_still(
            close, prompt="nova frowns", output_path=str(tmp_path / "c.png"), width=8, height=8,
            ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        first, second = stub_refs["calls"]
        # The establishing picture was drawn first, without a place of its own.
        assert "shows the place" not in first["prompt"] and first["prompt"].count("wide shot of the lab") == 1
        assert Path(second["reference_paths"][-1]).name == "place_s1.png"
        assert second["prompt"].startswith("Picture 2 shows the place")
        assert ctx.places == {"s1": str(tmp_path / "place_s1.png")}
        # The establishing shot's own turn reuses that picture instead of drawing again.
        await slr._render_reference_still(
            est, prompt=est.prompt, output_path=str(tmp_path / "e.png"), width=8, height=8,
            ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        assert len(stub_refs["calls"]) == 2 and (tmp_path / "e.png").exists()

    @pytest.mark.asyncio
    async def test_an_empty_plate_is_the_place_so_no_one_rides_along(self, stub_refs, tmp_path):
        """Mara's wings were stuck on the buildings behind Cinder and Bolt: the
        place reference was the wide shot with Mara in it (2026-10-14)."""
        est = Shot(**{**_shot(0, characters=["dex", "nova"]).model_dump(), "scene_id": "s1", "establishing": True,
                      "prompt": "wide shot of Dex and Nova on the dock",
                      "place_prompt": "ink, the empty place with nobody in it: a dock"})
        ctx = slr.ReferenceContext(cast=("dex", "nova"), establishing={"s1": est})
        await slr._render_reference_still(
            est, prompt=est.prompt, output_path=str(tmp_path / "e.png"), width=8, height=8,
            ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        plate, wide = stub_refs["calls"]
        assert "without the characters" in plate["prompt"] and "nobody in it: a dock" in plate["prompt"]
        # The establishing shot itself is drawn in the plate's place too.
        assert Path(wide["reference_paths"][-1]).name == "place_s1.png" and "shows the place" in wide["prompt"]

    @pytest.mark.asyncio
    async def test_no_scene_or_a_full_reference_set_means_no_place(self, stub_refs, tmp_path):
        ctx = slr.ReferenceContext(cast=("dex", "nova"))
        await slr._render_reference_still(
            _shot(characters=["nova"]), prompt="p", output_path=str(tmp_path / "s.png"), width=8, height=8,
            ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        assert "shows the place" not in stub_refs["calls"][0]["prompt"]

    def test_the_director_marks_one_establishing_picture_per_scene_master_first(self):
        shots = [{"source": "image_kenburns", "scene_id": "s1"}, {"source": "image_gen", "scene_id": "s1"},
                 {"source": "presenter", "scene_id": "s2"}, {"source": "image_kenburns", "scene_id": "s2"},
                 {"source": "image_gen", "scene_id": "s1"}]
        plan = [{"kind": "still"}, {"kind": "master"}, {"kind": "talk"}, {"kind": "still"}, {"kind": "master"}]
        sd._mark_establishing(shots, plan)
        assert [bool(s.get("establishing")) for s in shots] == [False, True, False, True, False]
