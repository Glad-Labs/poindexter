"""A storyboard renders from its own pictures (2026-10-14).

Matt: "we should reuse the storyboard frames for consistent backgrounds, I
wouldn't want the background to change scene to scene". The render used to
draw every shot again from its description, so the video's pictures (and
backgrounds) drifted from the storyboard the operator approved. Pinned here:
each drawn storyboard shot carries its picture as ``frame_url``; the renderer
starts from it (fitted to the frame, never stretched) instead of drawing; a
revised shot drops it; and a storyboard's stills animate when it is rendered.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from poindexter.schemas.video_shot_list import Shot
from poindexter.services import screenplay_director as sd
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr

LISTING = {"shots": [
    {"idx": 0, "source": "presenter", "speaker": "cinder", "frame_prompt": "Cinder talking", "duration_s": 3.4},
    {"idx": 1, "source": "image_kenburns", "prompt": "the dock at night", "duration_s": 1.8},
    {"idx": 2, "source": "image_gen", "prompt": "Mara arrives", "duration_s": 3.3},
    {"idx": 3, "source": "image_kenburns", "prompt": "a held beat", "duration_s": 1.5},
]}
ROWS = [{"idx": 0, "image_url": "https://cdn/s0.webp"}, {"idx": 1, "image_url": "https://cdn/s1.webp"},
        {"idx": 2, "image_url": "https://cdn/s2.webp"}, {"idx": 3, "image_url": None}]


class TestPrepare:
    def test_each_drawn_shot_carries_its_picture_and_stills_animate(self):
        out = sd.prepare_storyboard_render(LISTING, ROWS, SiteConfig(initial_config={"story_action_motion": "animate"}))
        shots = out["shots"]
        assert [s.get("frame_url") for s in shots] == [
            "https://cdn/s0.webp", "https://cdn/s1.webp", "https://cdn/s2.webp", None]
        assert [s["source"] for s in shots] == ["presenter", "generative", "generative", "generative"]
        assert shots[1]["motion"] == sd.DEFAULT_MOTION
        assert LISTING["shots"][1]["source"] == "image_kenburns"  # the stored listing is untouched

    def test_the_cartoon_budget_still_applies(self):
        sc = SiteConfig(initial_config={"story_action_motion": "animate", "story_max_animated_shots": "1", "story_min_animated_shot_seconds": "2"})
        out = sd.prepare_storyboard_render(LISTING, ROWS, sc)
        assert [s["source"] for s in out["shots"]] == ["presenter", "image_kenburns", "generative", "image_kenburns"]


def _png(path: Path, size=(1024, 576)) -> str:
    Image.new("RGB", size, (200, 30, 90)).save(path)
    return str(path)


@pytest.fixture
def fetched(monkeypatch, tmp_path):
    src = _png(tmp_path / "board.png")
    got: list[str] = []

    async def fetch(url, dest, factory):
        got.append(url)
        Path(dest).write_bytes(Path(src).read_bytes())
        return str(dest)

    monkeypatch.setattr(slr, "_fetch_presenter_portrait", fetch)
    return got


class TestRender:
    @pytest.mark.asyncio
    async def test_the_approved_frame_is_fitted_not_stretched(self, fetched, tmp_path):
        shot = Shot(idx=1, duration_s=2.0, intent="x", source="generative", prompt="p",
                    narration_offset_s=0.0, frame_url="https://cdn/s1.webp")
        out = await slr._approved_frame(shot, tmp_path / "f.png", None, width=1280, height=720)
        assert fetched == ["https://cdn/s1.webp"] and Image.open(out).size == (1280, 720)

    @pytest.mark.asyncio
    async def test_a_still_with_an_approved_frame_is_not_drawn(self, fetched, monkeypatch, tmp_path):
        from poindexter.services import reference_frames as rf

        async def draw(**kw):
            raise AssertionError("an approved frame must not be drawn again")

        monkeypatch.setattr(rf, "render_reference_frame", draw)
        shot = Shot(idx=2, duration_s=3.0, intent="x", source="generative", prompt="p",
                    narration_offset_s=0.0, frame_url="https://cdn/s2.webp", characters=["cinder"])
        ok = await slr._render_reference_still(
            shot, prompt="p", output_path=str(tmp_path / "s.png"), width=1024, height=576,
            ctx=slr.ReferenceContext(cast=("cinder",)), work_dir=tmp_path,
            site_config=SiteConfig(initial_config={}), http_client_factory=None)
        assert ok and (tmp_path / "s.png").exists()

    @pytest.mark.asyncio
    async def test_no_frame_or_a_failed_fetch_draws_as_before(self, monkeypatch, tmp_path):
        async def missing(url, dest, factory):
            return None

        monkeypatch.setattr(slr, "_fetch_presenter_portrait", missing)
        shot = Shot(idx=1, duration_s=2.0, intent="x", source="generative", prompt="p",
                    narration_offset_s=0.0, frame_url="https://cdn/gone.webp")
        assert await slr._approved_frame(shot, tmp_path / "f.png", None, width=8, height=8) is None


@pytest.mark.asyncio
async def test_a_revised_shot_drops_its_storyboard_frame(monkeypatch, tmp_path):
    """A note redraws a shot: that one is drawn anew; the rest keep their frames."""
    import json
    from unittest.mock import AsyncMock

    import poindexter.services.llm_text as llm_text
    import poindexter.services.screenplay as sp
    from poindexter.services import render_revision as rr
    from poindexter.services.video_renderers import clip_cache

    listing = {"timing": "exact", "total_duration_s": 4.0, "director_model": "m", "director_prompt_version": "v",
               "director_decided_at": "2026-10-14T00:00:00Z", "shots": [
        {"idx": 0, "source": "generative", "prompt": "ink, the dock", "duration_s": 2.0, "intent": "x",
         "narration_offset_s": 0.0, "frame_url": "https://cdn/s0.webp"},
        {"idx": 1, "source": "generative", "prompt": "ink, the crow", "duration_s": 2.0, "intent": "x",
         "narration_offset_s": 2.0, "frame_url": "https://cdn/s1.webp"}]}
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"wav")
    root = tmp_path / "clips"
    clip_cache.save_inputs(str(root / "t1"), audio_path=str(audio), caption_path=None, shot_list=listing,
                           timeline=[], screenplay={"title": "Fable"})
    monkeypatch.setattr(llm_text, "ollama_chat_text",
                        AsyncMock(return_value=json.dumps({"shots": [{"id": 0, "prompt": "ink, the dock at dawn"}]})))
    monkeypatch.setattr(sp, "_resolve_model", lambda sc: "ollama/test-writer")
    plan = await rr.plan_revision(None, task_id="t1", instruction="make it dawn",
                                  site_config=SiteConfig(initial_config={"video_clip_cache_dir": str(root)}))
    revised = json.loads((root / "t1" / plan["shot_list_file"]).read_text())
    assert "frame_url" not in revised["shots"][0] and revised["shots"][0]["prompt"] == "ink, the dock at dawn"
    assert revised["shots"][1]["frame_url"] == "https://cdn/s1.webp"
