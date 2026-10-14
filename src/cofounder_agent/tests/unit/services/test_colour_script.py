"""Colour script: each scene painted in a palette planned across the whole story.

A studio paints a colour script, one small key per scene, so the colour moves
with the story. The outline gives every scene a ``palette``; every picture of
the scene is painted in it; a location seen again in another palette has its
plate relit from the first plate rather than drawn again (it keeps its
buildings); and the storyboard shows the strip of plates in story order.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services import reference_frames as rf
from poindexter.services import screenplay as sp
from poindexter.services import screenplay_director as sd
from poindexter.services import storyboard as sb
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr

DUSK = "warm orange dusk with long pink shadows"
NIGHT = "cold blue midnight lit by cyan neon"


def _outline(*palettes):
    scenes = [{"scene_id": f"s{i + 1}", "location": "dock", "setting": "the dock", "summary": "a",
               "turn": "b", "characters": [], **({"palette": p} if p else {})} for i, p in enumerate(palettes)]
    return {"title": "t", "spine": dict.fromkeys(sp.SPINE_PARTS, "x"),
            "locations": [{"id": "dock", "description": "a rooftop dock"}], "scenes": scenes}


def _sc(**extra):
    return SiteConfig(initial_config={
        "persona.dex.display_name": "Dex", "persona.dex.enabled": "true",
        "persona.dex.portrait_url": "https://cdn/dex.png", "video_reference_frames_enabled": "true", **extra,
    })


class TestOutline:
    def test_each_scene_keeps_its_palette(self):
        out = sp.normalize_outline(_outline(DUSK, NIGHT))
        assert [s["palette"] for s in out["scenes"]] == [DUSK, NIGHT]

    def test_the_script_is_all_or_nothing(self):
        request = sp.build_request({"topic": "t"}, SiteConfig(initial_config={}))
        errors = sp.check_outline(sp.normalize_outline(_outline(DUSK, "")), request)
        assert any("'s2'" in e and '"palette"' in e for e in errors)
        assert not [e for e in sp.check_outline(sp.normalize_outline(_outline("", "")), request) if "palette" in e]

    def test_a_revision_sees_the_palettes(self):
        core = sp._core({"scenes": [{"scene_id": "s1", "palette": DUSK}]})
        assert core["scenes"][0]["palette"] == DUSK


class TestDirector:
    def test_the_palette_goes_last_and_always_fits(self):
        text = sd._with_palette("x" * 1600, DUSK, _sc(), 1500)
        assert len(text) <= 1500 and text.endswith(f"Colour and light: {DUSK}.")
        assert sd._with_palette("a dock", "", _sc(), 1500) == "a dock"
        custom = sd._with_palette("a dock", DUSK, _sc(story_palette_template="Lit as {palette}"), 1500)
        assert custom == f"a dock Lit as {DUSK}"

    def test_every_picture_of_a_scene_is_painted_in_its_palette(self):
        base = {"scene_id": "s1", "setting": "the dock", "location": "dock", "location_desc": "a rooftop dock",
                "palette": NIGHT, "beat_text": "", "delivery": "", "scene_cast": ["dex"], "line_speaker": "dex",
                "characters": ["dex"], "start_s": 0.0, "length": 2.0}
        plan = [{**base, "kind": "talk", "source": "presenter", "speaker": "dex"},
                {**base, "kind": "still", "source": "image_gen", "start_s": 2.0}]
        listing = sd.build_shot_list(plan, {1: {"prompt": "dex on the dock"}}, style="ink",
                                     director_model="m", site_config=_sc())
        talk, still = listing["shots"]
        assert talk["palette"] == still["palette"] == NIGHT
        assert talk["frame_prompt"].endswith(f"Colour and light: {NIGHT}.")
        assert still["prompt"].endswith(f"Colour and light: {NIGHT}.")


def _shot(idx, scene, palette, *, establishing=False):
    return Shot(idx=idx, duration_s=2.0, intent="x", source="image_gen", prompt=f"dex on the dock {idx}",
                narration_offset_s=float(idx), characters=["dex"], scene_id=scene, location_id="dock",
                palette=palette, establishing=establishing,
                place_prompt="ink, the empty place with nobody in it: a dock" if establishing else None)


@pytest.fixture
def drawn(monkeypatch):
    calls: list = []

    async def fetch(url, dest, factory):
        Path(dest).write_bytes(b"PNG")
        return str(dest)

    async def draw(**kw):
        calls.append(kw)
        Path(kw["output_path"]).write_bytes(b"PNG")
        return True, ""

    monkeypatch.setattr(slr, "_fetch_presenter_portrait", fetch)
    monkeypatch.setattr(rf, "render_reference_frame", draw)
    monkeypatch.setattr(slr, "_clear_image_gen_for_hero", AsyncMock())
    return calls


class TestPlates:
    def test_a_plate_is_a_location_in_a_palette(self):
        dusk, night = _shot(0, "s1", DUSK), _shot(1, "s2", NIGHT)
        assert slr._plate_key(dusk) != slr._plate_key(night)
        assert slr._plate_key(dusk) == slr._plate_key(_shot(2, "s3", DUSK.upper()))
        assert slr._plate_key(_shot(3, "s4", None)) == "dock"

    @pytest.mark.asyncio
    async def test_a_returning_location_is_relit_not_redrawn(self, drawn, tmp_path):
        ctx = slr.ReferenceContext(cast=("dex",), establishing={"dock": _shot(0, "s1", DUSK, establishing=True)})
        for s in (_shot(1, "s1", DUSK), _shot(2, "s2", NIGHT), _shot(3, "s3", DUSK)):
            await slr._render_reference_still(
                s, prompt=s.prompt, output_path=str(tmp_path / f"{s.idx}.png"), width=8, height=8,
                ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        fresh = [c for c in drawn if "nobody in it" in c["prompt"] and not c["prompt"].startswith("Picture 1 is a place")]
        relit = [c for c in drawn if c["prompt"].startswith("Picture 1 is a place")]
        assert len(fresh) == 1 and fresh[0]["prompt"].count(f"Colour and light: {DUSK}.") == 1
        assert len(relit) == 1 and NIGHT in relit[0]["prompt"]
        assert relit[0]["reference_paths"] == [ctx.places[slr._plate_key(_shot(0, "s1", DUSK))]]
        night_still = drawn[-2]
        assert night_still["reference_paths"][-1] == ctx.places[slr._plate_key(_shot(2, "s2", NIGHT))]
        assert drawn[-1]["reference_paths"][-1] == fresh[0]["output_path"]  # dusk again: the first plate

    @pytest.mark.asyncio
    async def test_a_failed_relight_draws_the_plate_afresh(self, monkeypatch, drawn, tmp_path):
        first = tmp_path / "first.png"
        first.write_bytes(b"PNG")
        ctx = slr.ReferenceContext(cast=("dex",), establishing={"dock": _shot(0, "s1", DUSK, establishing=True)})
        ctx.places[slr._plate_key(_shot(0, "s1", DUSK))] = str(first)
        monkeypatch.setattr(slr, "_relight_plate", AsyncMock(return_value=False))
        s = _shot(2, "s2", NIGHT)
        plate = await slr._place_for(s, ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        assert plate and ctx.places[slr._plate_key(s)] == plate
        assert drawn[-1]["prompt"].count(f"Colour and light: {NIGHT}.") == 1


class TestEmptyPlate:
    @pytest.mark.asyncio
    async def test_someone_in_the_plate_is_cleared_out(self, monkeypatch, drawn, tmp_path):
        from poindexter.services.video_renderers import shot_vision_qa as qa

        verdicts = [qa.TalkingFrameVerdict(ok=False, people=1, reason="a person"),
                    qa.TalkingFrameVerdict(ok=True, people=0)]
        judge = AsyncMock(side_effect=verdicts)
        monkeypatch.setattr(qa, "check_empty_plate", judge)
        ctx = slr.ReferenceContext(cast=("dex",), establishing={"dock": _shot(0, "s1", DUSK, establishing=True)})
        ctx.pool = object()
        plate = await slr._place_for(_shot(1, "s1", DUSK), ctx, work_dir=tmp_path, site_config=_sc(),
                                     http_client_factory=None)
        clears = [c for c in drawn if c["prompt"].startswith("Picture 1 is a place. Remove every")]
        assert judge.await_count == 2 and len(clears) == 1
        assert clears[0]["reference_paths"] == [plate] and Path(plate).exists()

    @pytest.mark.asyncio
    async def test_no_judge_keeps_the_plate_as_drawn(self, monkeypatch, drawn, tmp_path):
        from poindexter.services.video_renderers import shot_vision_qa as qa

        judge = AsyncMock(return_value=qa.TalkingFrameVerdict(ok=None, reason="no vision model"))
        monkeypatch.setattr(qa, "check_empty_plate", judge)
        ctx = slr.ReferenceContext(cast=("dex",), establishing={"dock": _shot(0, "s1", DUSK, establishing=True)})
        ctx.pool = object()
        await slr._place_for(_shot(1, "s1", DUSK), ctx, work_dir=tmp_path, site_config=_sc(),
                             http_client_factory=None)
        assert judge.await_count == 1 and not [c for c in drawn if "Remove every" in c["prompt"]]

    def test_the_judge_reads_a_count(self):
        from poindexter.services.video_renderers import shot_vision_qa as qa

        assert qa._parse_empty_plate('{"characters": 0, "reason": "empty"}').ok is True
        assert qa._parse_empty_plate('{"characters": 2, "reason": "two people"}').ok is False
        assert qa._parse_empty_plate('{"characters": true}').ok is None
        assert qa._parse_empty_plate("no idea").ok is None


class TestStrip:
    def test_one_key_per_scene_in_story_order(self):
        shots = [_shot(0, "s1", DUSK).model_dump(), _shot(1, "s1", DUSK).model_dump(),
                 _shot(2, "s2", NIGHT).model_dump(), _shot(3, "s3", None).model_dump()]
        shots[2]["source"] = "image_kenburns"  # no long run of one source
        listing = {"aspect": "16:9", "timing": "exact", "total_duration_s": 8.0, "shots": shots,
                   "director_model": "m", "director_prompt_version": "v",
                   "director_decided_at": "2026-10-14T00:00:00Z"}
        plates = {slr._plate_key(_shot(0, "s1", DUSK)): "https://cdn/dusk.png"}
        strip = sb.colour_script(listing, plates)
        assert [(k["scene_id"], k["palette"], k["plate_url"]) for k in strip] == [
            ("s1", DUSK, "https://cdn/dusk.png"), ("s2", NIGHT, None)]

    def test_the_chat_reads_the_colour_script(self):
        from poindexter.services.creative_drafts import describe

        draft = {"id": "b" * 36, "kind": "storyboard", "version": 1, "status": "ready", "payload": {
            "title": "t", "colour_script": [{"scene_id": "s1", "location": "dock", "palette": DUSK}]}}
        assert f"colour script: s1 (dock): {DUSK}" in describe(draft)
