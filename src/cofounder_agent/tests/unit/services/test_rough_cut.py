"""Rough cuts (services/rough_cut.py, chat drafting phase 9).

Pins what the storyboard card promises: the cut is the stills timed to the
voices with the score under them and the captions burned in; a revision
keeps the score it was approved with; the music in the rough cut is the
music in the render; and a cut that fails never costs the storyboard.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from poindexter.services import rough_cut as rc
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    return SiteConfig(initial_config=extra)


TIMELINE = [
    {"kind": "line", "speaker": "cinder", "text": "Twelve  minutes.", "start_s": 0.5, "end_s": 1.75},
    {"kind": "action", "text": "A clock chimes.", "start_s": 2.0, "end_s": 3.0},
    {"kind": "line", "speaker": "kai", "text": "Run.", "start_s": 3061.2, "end_s": 3062.0},
]
ROWS = [
    {"idx": 0, "start_s": 0.0, "duration_s": 2.0, "image_url": "https://cdn/s0.webp"},
    {"idx": 1, "start_s": 2.0, "duration_s": 1.0, "image_url": None},
    {"idx": 2, "start_s": 3.0, "duration_s": 2.0, "image_url": "https://cdn/s2.webp"},
]
BOARD = {"title": "Cinder", "style": "neon ink", "duration_s": 7.5, "audio_master_url": "https://cdn/d.wav",
         "shots": ROWS, "timeline": TIMELINE[:2], "screenplay": {"title": "Cinder", "scenes": []},
         "shot_list": {"shots": []}}


class TestParts:
    def test_captions_are_the_spoken_lines_at_their_times(self):
        srt = rc.captions_srt(TIMELINE)
        assert srt.startswith("1\n00:00:00,500 --> 00:00:01,750\nTwelve minutes.\n")
        assert "2\n00:51:01,200 --> 00:51:02,000\nRun." in srt and "chimes" not in srt

    def test_a_shot_without_a_picture_holds_the_one_before_and_the_last_runs_to_the_end(self):
        assert rc.scene_plan(ROWS, 7.5) == [
            ("https://cdn/s0.webp", 2.0, 0), ("https://cdn/s0.webp", 1.0, 1), ("https://cdn/s2.webp", 4.5, 2),
        ]


@pytest.fixture
def studio(monkeypatch, tmp_path):
    import poindexter.services.director_notes as dn
    import poindexter.services.media_compositors.ffmpeg_local as ff
    import poindexter.services.score as score
    import poindexter.services.storyboard as sb
    from poindexter.services.video_renderers import shot_vision_qa as sq

    seen = SimpleNamespace(downloads=[], uploads=[], requests=[])

    async def download(url, dest):
        seen.downloads.append(url)
        Path(dest).write_bytes(b"x")
        return dest

    async def upload(local, key, mime, site_config):
        seen.uploads.append((key, mime))
        return f"https://cdn/{key}"

    class Compositor:
        def __init__(self, site_config=None):
            pass

        async def compose(self, request):
            seen.requests.append(request)
            Path(request.output_path).write_bytes(b"mp4")
            return SimpleNamespace(success=True, output_path=request.output_path, error=None)

    async def make_score(*a, output_dir, **kw):
        path = Path(output_dir) / "s.wav"
        path.write_bytes(b"wav")
        return {"path": str(path)}

    seen.score = AsyncMock(side_effect=make_score)
    monkeypatch.setattr(rc, "_download", download)
    monkeypatch.setattr(sb, "_upload", upload)
    monkeypatch.setattr(ff, "FFmpegLocalCompositor", Compositor)
    monkeypatch.setattr(score, "score_screenplay", seen.score)
    monkeypatch.setattr(sq, "_probe_seconds", AsyncMock(return_value=7.5))
    seen.review = AsyncMock(return_value={"verdict": "ok", "notes": [], "cut": "rough"})
    monkeypatch.setattr(dn, "review_cut", seen.review)
    return seen


class TestBuild:
    @pytest.mark.asyncio
    async def test_the_cut_is_stills_voices_score_and_captions(self, studio):
        out = await rc.build_rough_cut(BOARD, site_config=_sc(), pool=None, upload_prefix="drafts/d1")
        req = studio.requests[0]
        assert [round(s.duration_s, 1) for s in req.scenes] == [2.0, 1.0, 4.5]
        assert req.scenes[0].clip_path == req.scenes[1].clip_path  # the picture-less shot holds
        assert req.narration_track_path.endswith("dialogue.wav") and req.soundtrack_path.endswith("s.wav")
        assert Path(req.caption_track_path).read_text().startswith("1\n00:00:00,500")
        assert (req.width, req.height) == (1280, 720)
        assert out["rough_cut_url"] == "https://cdn/drafts/d1/rough_cut-v1.mp4"
        assert out["score_url"] == "https://cdn/drafts/d1/score.wav" and out["rough_cut_seconds"] == 7.5
        assert out["rough_cut_notes"]["status"] == "ready"
        # BOARD's shot list carries no scenes: hard cuts, as before.
        assert all(not sc.transition_in for sc in req.scenes)
        assert studio.review.await_args.kwargs["cut"] == "rough"

    @pytest.mark.asyncio
    async def test_scenes_dissolve_into_each_other_and_shots_soften(self, studio):
        board = {**BOARD, "shot_list": {"shots": [{"idx": 0, "scene_id": "s1"}, {"idx": 1, "scene_id": "s1"},
                                                  {"idx": 2, "scene_id": "s2"}]}}
        await rc.build_rough_cut(board, site_config=_sc(rough_cut_notes_enabled="false"), pool=None,
                                 upload_prefix="drafts/d3")
        got = [(sc.transition_in, sc.transition_s) for sc in studio.requests[0].scenes]
        assert got == [("", 0.0), ("dissolve", 0.2), ("dissolve", 0.6)]

    @pytest.mark.asyncio
    async def test_a_revision_keeps_the_approved_score(self, studio):
        board = {**BOARD, "revisions": [{"note": "n"}]}
        out = await rc.build_rough_cut(board, site_config=_sc(rough_cut_notes_enabled="false"), pool=None,
                                       upload_prefix="drafts/d2", previous={"score_url": "https://cdn/old.wav"})
        assert studio.score.await_count == 0 and "https://cdn/old.wav" in studio.downloads
        assert out["score_url"] == "https://cdn/old.wav" and out["rough_cut_url"].endswith("rough_cut-v2.mp4")
        assert "rough_cut_notes" not in out

    @pytest.mark.asyncio
    async def test_a_failed_cut_keeps_the_storyboard(self, studio):
        board = {**BOARD, "audio_master_url": None}
        out = await rc.add_rough_cut(board, site_config=_sc(), pool=None, upload_prefix="p")
        assert "full-quality dialogue track" in out["rough_cut_error"] and out["shots"] == ROWS

    @pytest.mark.asyncio
    async def test_off_leaves_the_storyboard_alone(self, studio):
        out = await rc.add_rough_cut({**BOARD, "rough_cut_url": "stale"},
                                     site_config=_sc(storyboard_rough_cut_enabled="false"), pool=None,
                                     upload_prefix="p")
        assert "rough_cut_url" not in out and studio.requests == []


class TestIntoTheRender:
    @pytest.mark.asyncio
    async def test_rendering_a_storyboard_uses_its_score(self, monkeypatch):
        from poindexter.services import creative_drafts as cd

        draft = {"id": "d1-0000", "kind": "storyboard", "status": "ready", "payload": {
            **BOARD, "score_url": "https://cdn/score.wav", "screenplay": {"title": "Cinder"},
            "shot_list": {"shots": [], "cast_portraits": {}}}}
        monkeypatch.setattr(cd, "get_draft", AsyncMock(return_value=draft))
        got = []

        async def download(url, dest):
            got.append(url)
            return dest

        monkeypatch.setattr(cd, "_download", download)
        out = await cd.load_for_render(None, "d1", site_config=_sc())
        assert out["video_ambient_audio_path"].endswith("score.wav") and "https://cdn/score.wav" in got

    @pytest.mark.asyncio
    async def test_the_score_step_keeps_a_soundtrack_already_set(self, monkeypatch):
        import poindexter.services.score as score
        from poindexter.modules.content.atoms import media_score_screenplay as atom

        make = AsyncMock()
        monkeypatch.setattr(score, "score_screenplay", make)
        assert await atom.run({"video_ambient_audio_path": "/v/score.wav", "screenplay": {}}) == {}
        assert make.await_count == 0

    def test_the_load_step_declares_the_soundtrack(self):
        from poindexter.modules.content.atoms import story_load_draft as atom
        from poindexter.services.template_runner import PipelineState

        assert "video_ambient_audio_path" in atom.ATOM_META.produces
        assert set(atom.ATOM_META.produces) <= set(PipelineState.__annotations__)


class TestStoryboardDraft:
    @pytest.mark.asyncio
    async def test_a_revision_recuts_with_the_parents_score(self, monkeypatch):
        from poindexter.services import creative_drafts as cd
        from poindexter.services import storyboard as sb

        monkeypatch.setattr(cd, "_wait_for_gpu", AsyncMock())
        monkeypatch.setattr(cd, "_note", AsyncMock())
        monkeypatch.setattr(sb, "revise_storyboard", AsyncMock(return_value={"shots": [], "revised": True}))
        add = AsyncMock(return_value={"cut": True})
        monkeypatch.setattr(rc, "add_rough_cut", add)
        parent = {"payload": {"score_url": "https://cdn/old.wav"}}
        out = await cd._build(None, {"id": "d2", "kind": "storyboard", "request": {"instruction": "x"}},
                              parent, _sc())
        assert out == {"cut": True} and add.await_args.kwargs["previous"] is parent["payload"]


class TestRoughNotes:
    def test_a_still_is_not_faulted_for_not_moving(self):
        from poindexter.services import director_notes as dn

        reply = '{"hands_ok": true, "face_ok": true, "mouth_ok": true, "strangers": 0, ' \
                '"text_ok": true, "moves_ok": false, "matches": true, "note": "a still"}'
        assert dn.parse_shot_look(reply, rough=True)["flaws"] == []
        assert dn.parse_shot_look(reply)["flaws"] == ["nothing moves"]
