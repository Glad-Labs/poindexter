"""A cartoon ships no stand-in for any of its shots.

Matt, 2026-10-14: "There should be no fallbacks in the videos." The Midnight
Patch render replaced five wide shots with the close-up before them held
over, and earlier cartoons shipped bare portraits, a still in another style
and stock footage. A cartoon now draws a failed shot again and fails the
render, naming it, if it still cannot be made; and a shot under the quality
bar keeps its own best attempt. ``story_allow_fallbacks`` restores the old
ladder, and a blog video keeps it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr
from poindexter.services.video_renderers.shot_vision_qa import ShotQAResult


def _sc(**extra):
    return SiteConfig(initial_config={"video_reference_frames_enabled": "true", **extra})


def _shot(idx=1):
    return Shot(idx=idx, duration_s=2.0, intent="x", source="image_kenburns", prompt="a roof",
                narration_offset_s=float(idx), scene_id="s1")


def _failed(idx=1):
    return slr._ShotState(shot=_shot(idx), is_reused=False,
                          result=slr.ShotRenderResult(idx=idx, source="image_kenburns", success=False, error="oom"))


def _kwargs(tmp_path, ctx):
    return {"work_dir": tmp_path, "reference_ctx": ctx, "pexels_key": "", "orientation": "landscape",
            "http_client_factory": None, "niche_slug": None}


def test_only_a_cartoon_takes_no_fallbacks():
    ctx = slr.ReferenceContext(cast=("dex",))
    assert slr._no_fallbacks(ctx, _sc())
    assert not slr._no_fallbacks(None, _sc())
    assert not slr._no_fallbacks(ctx, _sc(story_allow_fallbacks="true"))
    assert not slr._no_fallbacks(ctx, _sc(video_reference_frames_enabled="false"))


class TestBackfill:
    @pytest.mark.asyncio
    async def test_a_failed_shot_is_drawn_again_not_stood_in_for(self, monkeypatch, tmp_path):
        clip = tmp_path / "ok.png"
        clip.write_bytes(b"png")
        render = AsyncMock(side_effect=[
            slr.ShotRenderResult(idx=1, source="image_kenburns", success=False, error="oom again"),
            slr.ShotRenderResult(idx=1, source="image_kenburns", success=True, clip_path=str(clip), duration_s=2.0),
        ])
        held = AsyncMock(return_value="/held.png")
        monkeypatch.setattr(slr, "_render_one_shot", render)
        monkeypatch.setattr(slr, "_held_picture", held)
        monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
        st = _failed()
        await slr._backfill_pass([st], render_kwargs=_kwargs(tmp_path, slr.ReferenceContext(cast=("dex",))),
                                 site_config=_sc(), post_id="p", width=8, height=8, wordmark="")
        assert st.rung == "retried" and st.result.clip_path == str(clip)
        assert render.await_count == 2 and held.await_count == 0

    @pytest.mark.asyncio
    async def test_a_shot_that_cannot_be_made_is_marked_failed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(slr, "_render_one_shot", AsyncMock(return_value=slr.ShotRenderResult(
            idx=1, source="image_kenburns", success=False, error="card wedged")))
        monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
        st = _failed()
        await slr._backfill_pass([st], render_kwargs=_kwargs(tmp_path, slr.ReferenceContext(cast=("dex",))),
                                 site_config=_sc(story_shot_render_attempts="3"), post_id="p",
                                 width=8, height=8, wordmark="")
        assert st.rung == "failed" and st.result.error == "card wedged"

    @pytest.mark.asyncio
    async def test_with_fallbacks_allowed_the_held_picture_stands_in(self, monkeypatch, tmp_path):
        monkeypatch.setattr(slr, "_held_picture", AsyncMock(return_value="/held.png"))
        monkeypatch.setattr(slr, "_emit_shot_fallback_finding", lambda **kw: None)
        st = _failed()
        await slr._backfill_pass([st], render_kwargs=_kwargs(tmp_path, slr.ReferenceContext(cast=("dex",))),
                                 site_config=_sc(story_allow_fallbacks="true"), post_id="p",
                                 width=8, height=8, wordmark="")
        assert st.rung == "hold" and st.result.clip_path == "/held.png"


class TestFinalize:
    def _states(self, tmp_path):
        prior = tmp_path / "prior.mp4"
        prior.write_bytes(b"x")
        own = tmp_path / "own.png"
        own.write_bytes(b"x")
        first = slr._ShotState(shot=_shot(0), is_reused=False, qa=ShotQAResult(score=90.0),
                               result=slr.ShotRenderResult(idx=0, source="image_kenburns", success=True,
                                                           clip_path=str(prior), duration_s=2.0))
        low = slr._ShotState(shot=_shot(1), is_reused=False, qa=ShotQAResult(score=45.0, reason="garbled text"),
                             result=slr.ShotRenderResult(idx=1, source="image_kenburns", success=True,
                                                         clip_path=str(own), duration_s=2.0))
        return [first, low], str(own), str(prior)

    @pytest.mark.asyncio
    async def test_a_cartoon_keeps_its_own_shot_under_the_bar(self, monkeypatch, tmp_path):
        monkeypatch.setattr(slr, "_emit_fallback_finding", lambda **kw: None)
        states, own, _ = self._states(tmp_path)
        qa = slr._QAConfig(enabled=True, threshold=60.0, max_retries=1)
        out = await slr._finalize_pass(states, qa=qa, pool=None, post_id="t", keep_own=True)
        assert out[1].clip_path == own

    @pytest.mark.asyncio
    async def test_otherwise_the_prior_clip_holds_over(self, monkeypatch, tmp_path):
        monkeypatch.setattr(slr, "_emit_fallback_finding", lambda **kw: None)
        states, _, prior = self._states(tmp_path)
        qa = slr._QAConfig(enabled=True, threshold=60.0, max_retries=1)
        out = await slr._finalize_pass(states, qa=qa, pool=None, post_id="t")
        assert out[1].clip_path == prior


def test_a_cartoon_still_never_reaches_the_text_path(tmp_path):
    """Guarded at the call: the text-path renderer is behind _no_fallbacks."""
    source = Path(slr.__file__).read_text(encoding="utf-8")
    assert source.count("not _no_fallbacks(reference_ctx, site_config) and await _render_image_gen_image(") == 2
