"""Reusable locations: a place the story returns to keeps its background.

Matt, 2026-10-14: "I wouldn't want the background to change scene to scene";
a studio paints each location once and reuses it. The outline names the
story's locations, every scene points at one, and the background plate is
drawn per location, so the rooftop in scene 4 is the rooftop of scene 1.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services import screenplay as sp
from poindexter.services import screenplay_director as sd
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr


def _outline(**over):
    raw = {"title": "t", "spine": dict.fromkeys(sp.SPINE_PARTS, "x"),
           "locations": [{"id": "Rooftop Dock", "description": "a rain-slicked rooftop repair dock"}],
           "scenes": [{"scene_id": "s1", "location": "rooftop-dock", "setting": "the dock at night",
                       "summary": "a", "turn": "b", "characters": []},
                      {"scene_id": "s2", "location": "rooftop-dock", "setting": "the dock at dawn",
                       "summary": "c", "turn": "d", "characters": []}]}
    raw.update(over)
    return raw


class TestOutline:
    def test_locations_are_named_once_and_scenes_point_at_them(self):
        out = sp.normalize_outline(_outline())
        assert out["locations"] == [{"id": "rooftop-dock", "description": "a rain-slicked rooftop repair dock"}]
        assert [s["location"] for s in out["scenes"]] == ["rooftop-dock", "rooftop-dock"]

    def test_an_older_script_gets_one_location_per_scene(self):
        out = sp.normalize_outline(_outline(locations=[]))
        assert [loc["id"] for loc in out["locations"]] == ["s1", "s2"]
        assert out["locations"][1]["description"] == "the dock at dawn"

    def test_a_scene_at_an_unlisted_location_is_sent_back(self):
        raw = _outline()
        raw["scenes"][1]["location"] = "palace"
        errors = sp.check_outline(sp.normalize_outline(raw), sp.build_request({"topic": "t"}, SiteConfig(initial_config={})))
        assert any('set "location" to one of' in e and "'s2'" in e for e in errors)


class TestDirector:
    def test_scenes_at_one_location_share_one_plate(self):
        shots = [{"source": "image_gen", "scene_id": "s1", "location_id": "dock"},
                 {"source": "image_gen", "scene_id": "s2", "location_id": "dock"},
                 {"source": "image_gen", "scene_id": "s3", "location_id": "palace"}]
        plan = [{"kind": "master", "location_desc": "a rooftop dock"}, {"kind": "master", "location_desc": "a rooftop dock"},
                {"kind": "still", "location_desc": "a glass ballroom"}]
        sd._mark_establishing(shots, plan, lambda d: f"plate: {d}")
        assert [bool(s.get("establishing")) for s in shots] == [True, False, True]
        assert shots[0]["place_prompt"] == "plate: a rooftop dock" and shots[2]["place_prompt"] == "plate: a glass ballroom"


@pytest.fixture
def drawn(monkeypatch, tmp_path):
    from poindexter.services import reference_frames as rf

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


def _sc():
    return SiteConfig(initial_config={"persona.dex.display_name": "Dex", "persona.dex.enabled": "true",
                                      "persona.dex.portrait_url": "https://cdn/dex.png",
                                      "video_reference_frames_enabled": "true"})


class TestRenderer:
    @pytest.mark.asyncio
    async def test_the_rooftop_in_scene_two_is_drawn_in_scene_ones_plate(self, drawn, tmp_path):
        def shot(idx, scene, establishing=False):
            return Shot(idx=idx, duration_s=2.0, intent="x", source="image_gen", prompt=f"dex on the dock {idx}",
                        narration_offset_s=float(idx), characters=["dex"], scene_id=scene, location_id="dock",
                        establishing=establishing,
                        place_prompt="ink, the empty place with nobody in it: a dock" if establishing else None)

        est = shot(0, "s1", establishing=True)
        ctx = slr.ReferenceContext(cast=("dex",), establishing={"dock": est})
        for s in (shot(1, "s1"), shot(2, "s2")):
            await slr._render_reference_still(
                s, prompt=s.prompt, output_path=str(tmp_path / f"{s.idx}.png"), width=8, height=8,
                ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        plates = [c for c in drawn if "nobody in it" in c["prompt"]]
        assert len(plates) == 1  # one plate for the location, not one per scene
        assert all(Path(c["reference_paths"][-1]).name == "place_dock.png" for c in drawn if c not in plates)
