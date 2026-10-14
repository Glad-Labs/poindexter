"""Auto-fix slop shots (chat drafting phase 5).

Each finished cartoon talking clip is checked on a contact sheet of moments
across the take for melted hands, a stretched mouth, a warping face or a
stranger; a failing take is re-rendered within a budget and the take with
fewer problems is kept. A clip the operator already watched is never touched.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr
from poindexter.services.video_renderers import shot_vision_qa as qa


def _sc(**extra):
    return SiteConfig(initial_config={
        "persona.ruby.display_name": "Ruby", "persona.ruby.enabled": "true",
        "persona.ruby.appearance": "a girl in a red hood", "qa_vision_model": "vision", **extra,
    })


class TestParse:
    def test_a_clean_take_passes(self):
        v = qa._parse_talking_clip('{"hands_ok": true, "mouth_ok": true, "face_ok": true, "strangers": 0, "reason": "clean"}')
        assert v.ok is True and v.problems == 0

    def test_each_failed_check_is_a_problem(self):
        v = qa._parse_talking_clip('{"hands_ok": false, "mouth_ok": false, "face_ok": true, "strangers": 1, "reason": "x"}')
        assert v.ok is False and v.problems == 3

    def test_a_missing_check_is_no_verdict(self):
        assert qa._parse_talking_clip('{"hands_ok": true, "strangers": 0}').ok is None
        assert qa._parse_talking_clip("no json").ok is None


class TestSheet:
    @pytest.mark.asyncio
    async def test_moments_across_the_clip_are_tiled_in_order(self, monkeypatch, tmp_path):
        monkeypatch.setattr(qa, "_probe_seconds", AsyncMock(return_value=8.0))
        times = []

        async def frame_at(path, at_s, tag):
            times.append(round(at_s, 2))
            out = tmp_path / f"{tag}.png"
            out.write_bytes(b"png")
            return str(out)

        cmds = []

        class Proc:
            async def wait(self):
                Path(cmds[-1][-1]).write_bytes(b"sheet")

        async def run(*cmd, **kw):
            cmds.append(cmd)
            return Proc()

        monkeypatch.setattr(qa, "_frame_at", frame_at)
        monkeypatch.setattr(qa.asyncio, "create_subprocess_exec", run)
        out = await qa.talking_clip_sheet(str(tmp_path / "clip.mp4"), 4, "t")
        assert out and times == [1.0, 3.0, 5.0, 7.0]
        graph = cmds[0][cmds[0].index("-filter_complex") + 1]
        assert "xstack=inputs=4:layout=0_0|512_0|0_288|512_288" in graph


class TestCheck:
    @pytest.mark.asyncio
    async def test_the_sheet_goes_to_the_vision_model(self, monkeypatch):
        monkeypatch.setattr(qa, "talking_clip_sheet", AsyncMock(return_value="/sheet.png"))
        vision = AsyncMock(return_value=('{"hands_ok": false, "mouth_ok": true, "face_ok": true, "strangers": 0, "reason": "fused fingers"}', ""))
        monkeypatch.setattr(qa, "_vision_text", vision)
        v = await qa.check_talking_clip(clip_path="/c.mp4", name="Ruby", appearance="a girl", site_config=_sc(), pool=object())
        assert v.ok is False and v.reason == "fused fingers"
        assert "THE SPEAKER: Ruby, a girl" in vision.await_args.kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_no_model_no_verdict(self):
        v = await qa.check_talking_clip(clip_path="/c.mp4", name="R", appearance="", site_config=_sc(qa_vision_model=""), pool=object())
        assert v.ok is None


def _state(tmp_path, idx=0, rung="primary"):
    clip = tmp_path / f"presenter_{idx}.mp4"
    clip.write_bytes(b"take1")
    shot = Shot(idx=idx, duration_s=2.0, intent="Ruby speaks", source="presenter", narration_offset_s=0.0,
                speaker="ruby", characters=["ruby"], frame_prompt="ruby alone")
    return slr._ShotState(
        shot=shot, is_reused=False, rung=rung,
        result=slr.ShotRenderResult(idx=idx, source="presenter", success=True, clip_path=str(clip), duration_s=2.0),
    )


@pytest.fixture
def stage(monkeypatch, tmp_path):
    verdicts: list = []
    renders: list = []

    async def fake_check(*, clip_path, **kw):
        return verdicts.pop(0)

    async def fake_render(shot, *, narration_window, **kw):
        out = tmp_path / f"presenter_{shot.idx}.mp4"
        out.write_bytes(b"redo")
        renders.append(shot.idx)
        return slr.ShotRenderResult(idx=shot.idx, source="presenter", success=True, clip_path=str(out), duration_s=2.0)

    findings: list = []
    monkeypatch.setattr(qa, "check_talking_clip", fake_check)
    monkeypatch.setattr(slr, "_render_presenter_clip", fake_render)
    monkeypatch.setattr(slr, "emit_finding", lambda **kw: findings.append(kw))
    return verdicts, renders, findings


BAD = qa.TalkingClipVerdict(ok=False, problems=2, reason="melted hand, wide mouth")
WORSE = qa.TalkingClipVerdict(ok=False, problems=3, reason="worse")
CLEAN = qa.TalkingClipVerdict(ok=True, problems=0, reason="clean")


class TestPass:
    @pytest.mark.asyncio
    async def test_a_failing_take_is_redone_and_the_better_take_kept(self, stage, tmp_path):
        verdicts, renders, findings = stage
        verdicts += [BAD, CLEAN]
        st = _state(tmp_path)
        await slr._talking_clip_pass([st], render_kwargs={"work_dir": tmp_path}, site_config=_sc(), pool=object(), post_id="t")
        assert renders == [0] and Path(st.result.clip_path).read_bytes() == b"redo"
        assert (tmp_path / "presenter_0_take1.mp4").read_bytes() == b"take1"  # the first take, set aside
        assert findings == []

    @pytest.mark.asyncio
    async def test_a_worse_redo_keeps_the_first_take_and_reports_it(self, stage, tmp_path):
        verdicts, renders, findings = stage
        verdicts += [BAD, WORSE]
        st = _state(tmp_path)
        await slr._talking_clip_pass([st], render_kwargs={}, site_config=_sc(), pool=object(), post_id="t")
        assert Path(st.result.clip_path).read_bytes() == b"take1"
        assert findings and findings[0]["kind"] == "talking_clip_flawed" and findings[0]["extra"]["problems"] == 2

    @pytest.mark.asyncio
    async def test_the_redo_budget_is_per_render(self, stage, tmp_path):
        verdicts, renders, findings = stage
        verdicts += [BAD, BAD, BAD]
        states = [_state(tmp_path, 0), _state(tmp_path, 1)]
        await slr._talking_clip_pass(states, render_kwargs={}, site_config=_sc(video_cast_clip_redo_budget="1"),
                                     pool=object(), post_id="t")
        assert renders == [0]  # the second shot found the budget spent
        assert len(findings) == 2

    @pytest.mark.asyncio
    async def test_a_watched_clip_and_a_clean_one_are_left_alone(self, stage, tmp_path):
        verdicts, renders, findings = stage
        verdicts += [CLEAN]
        kept, clean = _state(tmp_path, 0, rung="kept"), _state(tmp_path, 1)
        await slr._talking_clip_pass([kept, clean], render_kwargs={}, site_config=_sc(), pool=object(), post_id="t")
        assert renders == [] and verdicts == []  # only the primary clip was checked

    @pytest.mark.asyncio
    async def test_it_can_be_switched_off(self, stage, tmp_path):
        verdicts, renders, findings = stage
        await slr._talking_clip_pass([_state(tmp_path)], render_kwargs={},
                                     site_config=_sc(video_cast_clip_check_enabled="false"), pool=object(), post_id="t")
        assert renders == []
