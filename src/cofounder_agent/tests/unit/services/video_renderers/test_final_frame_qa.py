"""The frame the compositor holds is the one that gets judged (2026-09-25).

A hero clip is ~5 s in an ~11 s scene, and ``_scenes_for_plan`` continues it on
its FINAL frame for the rest of the scene. Shot QA judged one frame ~1 s in, so
the frame on screen longest was never seen: on f555bedc a hero panned until its
cloud sat cut off at the bottom of an empty frame, and another grew a large
garbled banner mid-animation. Both were held for seconds after passing at 92.

Three mechanisms, each pinned here:

* the final frame of a hero clip is judged too, and the worse frame wins;
* the judge labels large lettering ``none`` / ``readable`` / ``garbled``, and
  ``garbled`` on the FULL frame caps the score under the threshold (the banner
  came back 65 while the judge's own reason said "garbled text");
* detail collapse: a final frame keeping under
  ``video_shot_qa_detail_collapse_ratio`` of the opening's edge density scores
  low without a model call (the judge scored the empty frame 87 and 92).

And the repair path those verdicts feed, which had never run for a hero: its
candidates overwrote the incumbent's files, and it re-rolled heroes onto a card
the presenter phase had filled. Its first live re-roll of a collapse asked for
the same pull-back again ("slow zoom out ... to the horizon") and collapsed
again, so a collapse is now re-rolled with the camera held, and when its
opening passed, from the same still, copied, instead of a new one.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services.settings_defaults import DEFAULTS
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr
from poindexter.services.video_renderers import shot_vision_qa as sq
from poindexter.services.video_renderers.shot_vision_qa import ShotQAResult

_MODEL = "ollama/qwen3-vl:30b-a3b-instruct"


@pytest.fixture(autouse=True)
def _inert_reclaim_rungs(monkeypatch):
    """No test here reaches a GPU sidecar. The reclaim rungs are real POSTs to
    compose service names, which on the CI runner are the production
    containers (tests/unit/_gpu_isolation.py), and a repair-pass test whose
    stubs miss a path would evict the live models. Returns the rung mocks."""
    from tests.unit._gpu_isolation import make_reclaim_rungs_inert

    return make_reclaim_rungs_inert(monkeypatch)


def _sc(**over) -> SiteConfig:
    cfg = {"qa_vision_model": _MODEL, "video_shot_qa_crop_enabled": "false"}
    cfg.update(over)
    return SiteConfig(initial_config=cfg)


def _hero(source: str = "generative", idx: int = 4) -> Shot:
    return Shot(
        idx=idx, duration_s=5.0, intent="shrinking the sync payload", source=source,
        prompt="a massive monolith of data collapsing into a small glowing cube",
        narration_offset_s=0.0,
    )


def _detailed(path: Path) -> str:
    """A frame full of edges: a fine grid, like a busy illustrated scene."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (704, 400), (10, 26, 47))
    d = ImageDraw.Draw(img)
    for x in range(0, 704, 12):
        d.line((x, 0, x, 400), fill=(34, 211, 238))
    for y in range(0, 400, 12):
        d.line((0, y, 704, y), fill=(34, 211, 238))
    img.save(path)
    return str(path)


def _empty(path: Path) -> str:
    """The v4 shot-15 ending: dark navy, one small shape near the bottom."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (704, 400), (12, 28, 45))
    ImageDraw.Draw(img).ellipse((330, 360, 380, 395), fill=(120, 200, 220))
    img.save(path)
    return str(path)


def _patch_frames(opening: str, final: str | None):
    return (
        patch.object(sq, "_extract_video_frame", AsyncMock(return_value=opening)),
        patch.object(sq, "_final_frame", AsyncMock(return_value=final)),
    )


def _scorer(scores: dict[str, ShotQAResult]):
    """A fake judge keyed on the image path; records every call."""
    calls: list[str] = []

    async def _score(image_path, *, prompt, model, pool, shot_idx):
        calls.append(image_path)
        return scores[image_path]

    return _score, calls


# ---------------------------------------------------------------------------
# the final frame
# ---------------------------------------------------------------------------


async def test_a_worse_final_frame_decides_and_keeps_the_opening_verdict(tmp_path):
    opening = _detailed(tmp_path / "open.png")
    final = _detailed(tmp_path / "final.png")
    score, calls = _scorer({
        opening: ShotQAResult(score=92.0, reason="clean"),
        final: ShotQAResult(score=48.0, reason="warped"),
    })
    ex, fin = _patch_frames(opening, final)
    with ex, fin, patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path="/w/shot_04.mp4", shot=_hero(), site_config=_sc(), pool=object(),
        )
    assert r.score == 48.0
    assert r.frame == "final" and r.opening_score == 92.0
    assert r.reason.startswith("final frame: ")
    assert calls == [opening, final]
    assert not r.detail_collapse, "the judge's verdict, not the collapse rule's"


async def test_a_final_frame_that_is_fine_leaves_the_opening_verdict(tmp_path):
    opening = _detailed(tmp_path / "open.png")
    final = _detailed(tmp_path / "final.png")
    score, _ = _scorer({
        opening: ShotQAResult(score=85.0, reason="ok"),
        final: ShotQAResult(score=92.0, reason="better"),
    })
    ex, fin = _patch_frames(opening, final)
    with ex, fin, patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path="/w/shot_04.mp4", shot=_hero(), site_config=_sc(), pool=object(),
        )
    assert (r.score, r.frame, r.opening_score) == (85.0, "opening", 85.0)


@pytest.mark.parametrize("source", ["generative", "wan21"])
async def test_every_hero_source_is_judged_at_its_end(tmp_path, source):
    opening = _detailed(tmp_path / "open.png")
    final = _detailed(tmp_path / "final.png")
    score, calls = _scorer({
        opening: ShotQAResult(score=92.0), final: ShotQAResult(score=40.0),
    })
    ex, fin = _patch_frames(opening, final)
    with ex, fin, patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path="/w/clip.mp4", shot=_hero(source), site_config=_sc(), pool=object(),
        )
    assert r.score == 40.0 and len(calls) == 2


async def test_a_still_has_no_final_frame(tmp_path):
    still = _detailed(tmp_path / "shot_04.png")
    final = AsyncMock()
    score, calls = _scorer({still: ShotQAResult(score=92.0)})
    with patch.object(sq, "_final_frame", final), patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path=still, shot=_hero("image_kenburns"), site_config=_sc(), pool=object(),
        )
    assert r.score == 92.0 and calls == [still]
    final.assert_not_awaited()


async def test_a_presenter_is_not_judged_at_its_end(tmp_path):
    """A presenter cannot be re-rolled, so a failing final frame would drop the
    face and its lip-synced line for the previous shot: worse than the hold."""
    opening = _detailed(tmp_path / "open.png")
    final = AsyncMock()
    score, _ = _scorer({opening: ShotQAResult(score=92.0)})
    with patch.object(sq, "_extract_video_frame", AsyncMock(return_value=opening)), \
         patch.object(sq, "_final_frame", final), patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path="/w/presenter_8.mp4", shot=_hero("presenter", idx=8),
            site_config=_sc(), pool=object(),
        )
    assert r.score == 92.0
    final.assert_not_awaited()


async def test_a_demo_recording_is_not_judged_at_its_end(tmp_path):
    """cli_demo is a real terminal recording, not a generated clip."""
    opening = _detailed(tmp_path / "open.png")
    final = AsyncMock()
    score, _ = _scorer({opening: ShotQAResult(score=90.0)})
    shot = Shot(idx=2, duration_s=6.0, intent="show the CLI", source="cli_demo",
                demo_id="tasks-list", narration_offset_s=0.0)
    with patch.object(sq, "_extract_video_frame", AsyncMock(return_value=opening)), \
         patch.object(sq, "_final_frame", final), patch.object(sq, "_score_image", score):
        await sq.score_shot_frame(
            frame_path="/w/demo.mp4", shot=shot, site_config=_sc(), pool=object(),
        )
    final.assert_not_awaited()


async def test_the_final_frame_pass_is_settings_gated(tmp_path):
    opening = _detailed(tmp_path / "open.png")
    final = AsyncMock()
    score, _ = _scorer({opening: ShotQAResult(score=92.0)})
    with patch.object(sq, "_extract_video_frame", AsyncMock(return_value=opening)), \
         patch.object(sq, "_final_frame", final), patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path="/w/shot_04.mp4", shot=_hero(),
            site_config=_sc(video_shot_qa_final_frame_enabled="false"), pool=object(),
        )
    assert r.score == 92.0
    final.assert_not_awaited()


async def test_no_final_pass_after_an_infra_miss(tmp_path):
    """An opening the judge could not score is not a verdict to compare with."""
    opening = _detailed(tmp_path / "open.png")
    final = AsyncMock()
    score, _ = _scorer({opening: ShotQAResult(score=None, reason="vision call failed")})
    with patch.object(sq, "_extract_video_frame", AsyncMock(return_value=opening)), \
         patch.object(sq, "_final_frame", final), patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path="/w/shot_04.mp4", shot=_hero(), site_config=_sc(), pool=object(),
        )
    assert r.score is None
    final.assert_not_awaited()


async def test_a_missing_final_frame_leaves_the_opening_verdict(tmp_path):
    opening = _detailed(tmp_path / "open.png")
    score, _ = _scorer({opening: ShotQAResult(score=88.0)})
    ex, fin = _patch_frames(opening, None)
    with ex, fin, patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path="/w/shot_04.mp4", shot=_hero(), site_config=_sc(), pool=object(),
        )
    assert (r.score, r.frame) == (88.0, "opening")


# ---------------------------------------------------------------------------
# the text label
# ---------------------------------------------------------------------------


def test_the_text_label_is_parsed_and_an_unknown_one_is_dropped():
    r = sq._parse_score('{"text": "Garbled", "score": 65, "reason": "banner"}')
    assert r.text == "garbled"
    assert sq._parse_score('{"text": "unverifiable", "score": 80}').text == ""
    assert sq._parse_score('{"score": 80}').text == ""


async def test_garbled_on_the_full_frame_caps_the_score(tmp_path):
    """The f555bedc v5 banner: 65 every time, over the 60 threshold."""
    still = _detailed(tmp_path / "shot_04.png")
    score, _ = _scorer({still: ShotQAResult(score=65.0, reason="banner", text="garbled")})
    with patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path=still, shot=_hero("image_gen"), site_config=_sc(), pool=object(),
        )
    assert r.score == 45.0 and r.text == "garbled"
    assert r.reason.startswith("garbled text: ")


async def test_the_cap_is_db_tunable_and_never_raises_a_score(tmp_path):
    still = _detailed(tmp_path / "shot_04.png")
    for raw, cap, want in ((65.0, "30", 30.0), (20.0, "45", 20.0)):
        score, _ = _scorer({still: ShotQAResult(score=raw, reason="r", text="garbled")})
        with patch.object(sq, "_score_image", score):
            r = await sq.score_shot_frame(
                frame_path=still, shot=_hero("image_gen"), pool=object(),
                site_config=_sc(video_shot_qa_garbled_text_cap=cap),
            )
        assert r.score == want


@pytest.mark.parametrize("label", ["none", "readable", ""])
async def test_other_labels_never_cap(tmp_path, label):
    still = _detailed(tmp_path / "shot_04.png")
    score, _ = _scorer({still: ShotQAResult(score=85.0, reason="r", text=label)})
    with patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path=still, shot=_hero("image_gen"), site_config=_sc(), pool=object(),
        )
    assert r.score == 85.0


async def test_garbled_in_the_crop_alone_does_not_cap(tmp_path):
    """The 2x crop magnifies a row of small marks on the subject into what the
    judge calls large lettering (f555bedc hero 10: "none" at full frame,
    "garbled" cropped, on both calibration runs). A viewer never sees the crop."""
    still = _detailed(tmp_path / "shot_04.png")
    crop = _detailed(tmp_path / "crop.png")
    score, _ = _scorer({
        still: ShotQAResult(score=92.0, reason="clean", text="none"),
        crop: ShotQAResult(score=85.0, reason="marks on the cube", text="garbled"),
    })
    with patch.object(sq, "_score_image", score), \
         patch.object(sq, "_crop_frame", AsyncMock(return_value=crop)), \
         patch.object(sq, "_remove_quietly", lambda p: None):
        r = await sq.score_shot_frame(
            frame_path=still, shot=_hero("image_gen"), pool=object(),
            site_config=_sc(video_shot_qa_crop_enabled="true"),
        )
    assert r.score == 85.0, "the crop still counts as a view"
    assert r.text == "none", "but its label is not the viewer's"


def test_the_prompt_asks_for_exactly_the_labels_the_parser_accepts():
    """Derived, not hand-listed: if the prompt's output spec and ``_TEXT_LABELS``
    drift apart, a renamed label silently stops capping anything."""
    from poindexter.services.prompt_manager import UnifiedPromptManager

    template = UnifiedPromptManager().prompts["qa.video_shot_quality"]["template"]
    m = re.search(r'"text": "([a-z|]+)"', template)
    assert m, "qa.video_shot_quality no longer asks for a text label"
    assert set(m.group(1).split("|")) == sq._TEXT_LABELS
    for placeholder in ("{intent}", "{visual}", "{source}"):
        assert placeholder in template


# ---------------------------------------------------------------------------
# detail collapse
# ---------------------------------------------------------------------------


async def test_a_collapsed_final_frame_scores_low_without_a_model_call(tmp_path):
    opening = _detailed(tmp_path / "open.png")
    final = _empty(tmp_path / "final.png")
    score, calls = _scorer({opening: ShotQAResult(score=92.0, reason="clean")})
    ex, fin = _patch_frames(opening, final)
    with ex, fin, patch.object(sq, "_score_image", score):
        r = await sq.score_shot_frame(
            frame_path="/w/shot_15.mp4", shot=_hero(idx=15), site_config=_sc(), pool=object(),
        )
    assert r.score == 30.0 and r.frame == "final" and r.opening_score == 92.0
    assert "lost its detail" in r.reason
    assert calls == [opening], "the empty frame needs no judge"
    assert r.detail_collapse, "the repair pass holds the camera on this flag"


async def test_collapse_settings_are_db_tunable(tmp_path):
    opening = _detailed(tmp_path / "open.png")
    final = _empty(tmp_path / "final.png")
    score, calls = _scorer({
        opening: ShotQAResult(score=92.0), final: ShotQAResult(score=87.0),
    })
    ex, fin = _patch_frames(opening, final)
    with ex, fin, patch.object(sq, "_score_image", score):
        tuned = await sq.score_shot_frame(
            frame_path="/w/shot_15.mp4", shot=_hero(idx=15), pool=object(),
            site_config=_sc(video_shot_qa_detail_collapse_score="12"),
        )
        off = await sq.score_shot_frame(
            frame_path="/w/shot_15.mp4", shot=_hero(idx=15), pool=object(),
            site_config=_sc(video_shot_qa_detail_collapse_ratio="0"),
        )
    assert tuned.score == 12.0
    # Ratio 0 disables the rule, and the judge sees the frame instead: the
    # 87 it gave the real one is exactly why the rule exists.
    assert off.score == 87.0 and calls[-1] == final


def test_a_sparse_opening_has_no_detail_to_lose(tmp_path):
    """A minimal shot (one light in a void) is sparse at both ends; the ratio of
    two near-zero numbers must not read as collapse."""
    sparse = _empty(tmp_path / "a.png")
    emptier = tmp_path / "b.png"
    from PIL import Image

    Image.new("RGB", (704, 400), (12, 28, 45)).save(emptier)
    assert sq._edge_density(sparse) < sq._COLLAPSE_MIN_OPENING_EDGE
    assert sq._detail_collapse(sparse, str(emptier), ratio=0.3) is None
    # A floor tuned to 0 must still never divide by a zero-detail opening.
    assert sq._detail_collapse(str(emptier), str(emptier), ratio=0.3, min_opening=0.0) is None


async def test_the_opening_floor_is_db_tunable(tmp_path):
    """A minimalist house style can lower the floor so its sparse clips are
    covered too."""
    from PIL import Image, ImageDraw

    sparse_open = tmp_path / "open.png"
    img = Image.new("RGB", (704, 400), (12, 28, 45))
    ImageDraw.Draw(img).ellipse((320, 170, 380, 230), outline=(200, 230, 240), width=1)
    img.save(sparse_open)
    final = tmp_path / "final.png"
    Image.new("RGB", (704, 400), (12, 28, 45)).save(final)
    opening_edge = sq._edge_density(str(sparse_open))
    assert 0 < opening_edge < sq._COLLAPSE_MIN_OPENING_EDGE
    score, calls = _scorer({
        str(sparse_open): ShotQAResult(score=92.0), str(final): ShotQAResult(score=90.0),
    })
    ex, fin = _patch_frames(str(sparse_open), str(final))
    with ex, fin, patch.object(sq, "_score_image", score):
        default = await sq.score_shot_frame(
            frame_path="/w/shot_02.mp4", shot=_hero(idx=2), site_config=_sc(), pool=object(),
        )
        lowered = await sq.score_shot_frame(
            frame_path="/w/shot_02.mp4", shot=_hero(idx=2), pool=object(),
            site_config=_sc(video_shot_qa_detail_collapse_min_opening_edge="0.1"),
        )
    assert default.score == 90.0, "under the default floor the judge decides"
    assert lowered.score == 30.0 and "lost its detail" in lowered.reason


def test_an_unreadable_frame_is_no_proof_of_collapse(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    assert sq._edge_density(str(bad)) is None
    assert sq._detail_collapse(_detailed(tmp_path / "a.png"), str(bad), ratio=0.3) is None


@pytest.mark.parametrize("luma", [0, 40, 128, 250])
def test_a_flat_frame_has_no_edges_at_any_brightness(tmp_path, luma):
    """PIL's FIND_EDGES leaves its 1 px border at the raw pixel values, so an
    unfiltered mean read a blank white frame as 2.4 "edges": enough to hide a
    blown-out ending from the ratio and to pass a flat frame off as detail."""
    from PIL import Image

    flat = tmp_path / "flat.png"
    Image.new("RGB", (704, 400), (luma, luma, luma)).save(flat)
    assert sq._edge_density(str(flat)) == pytest.approx(0.0, abs=1e-6)


def test_edge_density_is_resolution_independent(tmp_path):
    """Clips come at 704x400, 832x480 and 480x832; the same content must
    measure alike so one ratio fits them all."""
    from PIL import Image

    small = Image.open(_detailed(tmp_path / "s.png"))
    big = tmp_path / "b.png"
    small.resize((1408, 800), Image.NEAREST).save(big)
    a, b = sq._edge_density(str(tmp_path / "s.png")), sq._edge_density(str(big))
    assert a and b and abs(a - b) / a < 0.35


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_the_final_frame_is_the_clips_last_frame(tmp_path):
    """The frame the compositor holds: a clip that ends black yields a black
    final frame, while the 1 s sample still sees the colour."""
    clip = tmp_path / "shot_15.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=0x22d3ee:s=320x180:d=2,format=yuv420p", "-f", "lavfi", "-i",
         "color=c=black:s=320x180:d=1,format=yuv420p", "-filter_complex",
         "[0][1]concat=n=2:v=1", str(clip)],
        check=True,
    )
    from PIL import Image

    last = await sq._final_frame(str(clip))
    first = await sq._extract_video_frame(str(clip))
    assert last and first
    assert max(Image.open(last).convert("L").getextrema()) < 20
    assert max(Image.open(first).convert("L").getextrema()) > 100


# ---------------------------------------------------------------------------
# the repair path a low verdict feeds
# ---------------------------------------------------------------------------


def _state(tmp_path: Path, *, source: str = "generative", score: float = 30.0,
           frame: str = "final", opening: float | None = 92.0) -> slr._ShotState:
    shot = _hero(source, idx=15)
    still = tmp_path / "shot_15.png"
    clip = tmp_path / "shot_15.mp4"
    still.write_bytes(b"incumbent-still")
    clip.write_bytes(b"incumbent-clip")
    return slr._ShotState(
        shot=shot,
        result=slr.ShotRenderResult(
            idx=15, source=source, success=True, clip_path=str(clip),
            duration_s=5.0, still_path=str(still),
        ),
        is_reused=False,
        qa=ShotQAResult(score=score, reason="final frame lost its detail",
                        frame=frame, opening_score=opening),
    )


async def test_repair_candidates_never_overwrite_the_incumbent(tmp_path, monkeypatch):
    """Keep-best on disk: before the fix every candidate wrote shot_15.mp4, so a
    losing re-roll replaced the clip that had beaten it."""
    st = _state(tmp_path)
    work_dirs: list[Path] = []

    async def _render(shot, *, prior_clip, attempt, work_dir, **kw):
        work_dirs.append(Path(work_dir))
        clip = Path(work_dir) / "shot_15.mp4"
        clip.write_bytes(f"candidate-{attempt}".encode())
        return slr.ShotRenderResult(idx=15, source=shot.source, success=True,
                                    clip_path=str(clip), duration_s=5.0)

    monkeypatch.setattr(slr, "_render_one_shot", _render)
    monkeypatch.setattr(slr, "score_shot_frame",
                        AsyncMock(return_value=ShotQAResult(score=20.0, reason="worse")))
    ready = AsyncMock()
    monkeypatch.setattr(slr, "_ready_card_for_escalation", ready)
    await slr._repair_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=2),
        site_config=None, render_kwargs={"work_dir": tmp_path}, pool=object(),
    )
    assert work_dirs == [tmp_path / "repair1", tmp_path / "repair2"]
    assert st.result.clip_path == str(tmp_path / "shot_15.mp4")
    assert (tmp_path / "shot_15.mp4").read_bytes() == b"incumbent-clip"
    assert st.qa.score == 30.0 and st.attempts == 2
    assert ready.await_count == 2, "the card is cleared before each hero re-roll"


async def test_a_better_candidate_replaces_the_incumbent(tmp_path, monkeypatch):
    st = _state(tmp_path)

    async def _render(shot, *, prior_clip, attempt, work_dir, **kw):
        clip = Path(work_dir) / "shot_15.mp4"
        clip.write_bytes(b"better")
        return slr.ShotRenderResult(idx=15, source=shot.source, success=True,
                                    clip_path=str(clip), duration_s=5.0)

    monkeypatch.setattr(slr, "_render_one_shot", _render)
    monkeypatch.setattr(slr, "score_shot_frame",
                        AsyncMock(return_value=ShotQAResult(score=92.0, reason="clean")))
    monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
    await slr._repair_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=2),
        site_config=None, render_kwargs={"work_dir": tmp_path}, pool=object(),
    )
    assert st.result.clip_path == str(tmp_path / "repair1" / "shot_15.mp4")
    assert st.qa.score == 92.0 and st.attempts == 1  # left the batch at once


async def test_stock_re_rolls_do_not_clear_the_card(tmp_path, monkeypatch):
    """Only image-gen-family re-rolls need image-gen; a stock re-roll is a download."""
    shot = Shot(idx=2, duration_s=5.0, intent="i", source="pexels", query="server racks",
                narration_offset_s=0.0)
    st = slr._ShotState(
        shot=shot, is_reused=False, qa=ShotQAResult(score=30.0),
        result=slr.ShotRenderResult(idx=2, source="pexels", success=True,
                                    clip_path=str(tmp_path / "shot_02_pexels.mp4")),
    )
    monkeypatch.setattr(slr, "_render_one_shot", AsyncMock(
        return_value=slr.ShotRenderResult(idx=2, source="pexels", success=False)))
    ready = AsyncMock()
    monkeypatch.setattr(slr, "_ready_card_for_escalation", ready)
    await slr._repair_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=1),
        site_config=None, render_kwargs={"work_dir": tmp_path}, pool=object(),
    )
    ready.assert_not_awaited()


async def test_the_stock_re_query_renders_beside_the_incumbent_not_over_it(tmp_path, monkeypatch):
    shot = Shot(idx=2, duration_s=5.0, intent="the sync process", source="pexels",
                query="code scrolling", narration_offset_s=0.0)
    st = slr._ShotState(
        shot=shot, is_reused=False, qa=ShotQAResult(score=30.0),
        result=slr.ShotRenderResult(idx=2, source="pexels", success=True,
                                    clip_path=str(tmp_path / "shot_02_pexels.mp4")),
    )
    seen: list[Path] = []

    async def _render(shot, *, work_dir, **kw):
        seen.append(Path(work_dir))
        return slr.ShotRenderResult(idx=2, source=shot.source, success=False)

    monkeypatch.setattr(slr, "_render_one_shot", _render)
    monkeypatch.setattr(slr, "_llm_restock_query", AsyncMock(return_value="server racks"))
    monkeypatch.setattr(slr, "_llm_image_subject", AsyncMock(return_value=""))
    monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
    await slr._escalate_offtopic_stock(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=2),
        site_config=None, render_kwargs={"work_dir": tmp_path}, pool=object(),
        post_id="p1",
    )
    assert seen[0] == tmp_path / "requery"
    assert all(d.parent == tmp_path and d.name in ("requery", "escalate") for d in seen)


async def test_a_hero_that_went_wrong_at_its_end_falls_back_to_its_still(tmp_path, monkeypatch):
    st = _state(tmp_path)
    findings: list[dict] = []
    monkeypatch.setattr(slr, "emit_finding", lambda **kw: findings.append(kw))
    out = await slr._finalize_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=2),
        pool=None, post_id="p1",
    )
    assert out[0].clip_path == str(tmp_path / "shot_15.png")
    f = next(f for f in findings if f["kind"] == "shot_quality_fallback")
    assert "fell back to its still" in f["title"] and "92" in f["body"]


@pytest.mark.parametrize(("frame", "opening", "source"), [
    ("final", 55.0, "generative"),   # the opening failed too: the still is suspect
    ("opening", 30.0, "generative"),  # wrong from the start
    ("final", 92.0, "image_kenburns"),  # not a hero: no still behind it
])
async def test_other_below_threshold_shots_keep_the_holdover(
    tmp_path, monkeypatch, frame, opening, source,
):
    prior = slr._ShotState(
        shot=_hero("image_kenburns", idx=14), is_reused=False,
        qa=ShotQAResult(score=90.0),
        result=slr.ShotRenderResult(idx=14, source="image_kenburns", success=True,
                                    clip_path=str(tmp_path / "shot_14.png")),
    )
    st = _state(tmp_path, source=source, frame=frame, opening=opening)
    monkeypatch.setattr(slr, "emit_finding", lambda **kw: None)
    out = await slr._finalize_pass(
        [prior, st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=2),
        pool=None, post_id="p1",
    )
    assert out[1].clip_path == str(tmp_path / "shot_14.png")


async def test_a_missing_still_keeps_the_holdover(tmp_path, monkeypatch):
    st = _state(tmp_path)
    os.remove(st.result.still_path)
    monkeypatch.setattr(slr, "emit_finding", lambda **kw: None)
    out = await slr._finalize_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=2),
        pool=None, post_id="p1",
    )
    # idx-0-style: nothing to hold over, so the best attempt ships (kept_below).
    assert out[0].clip_path == str(tmp_path / "shot_15.mp4")


async def test_a_hero_re_roll_carries_the_heartbeat_to_comfyui(tmp_path, monkeypatch):
    """A re-roll runs minutes of ComfyUI; without the heartbeat the task reads
    as stalled for all of it."""
    beat = AsyncMock()
    animate = AsyncMock(return_value=slr.ShotRenderResult(
        idx=15, source="generative", success=True, clip_path="c.mp4"))
    monkeypatch.setattr(slr, "_render_hero_still", AsyncMock(return_value=slr.ShotRenderResult(
        idx=15, source="generative", success=True, clip_path=str(tmp_path / "shot_15.png"))))
    monkeypatch.setattr(slr, "_animate_hero", animate)
    await slr._render_one_shot(
        _hero(idx=15), prior_clip=None, work_dir=tmp_path, image_gen_url="",
        site_config=None, http_client_factory=None, heartbeat_cb=beat,
    )
    assert animate.await_args.kwargs["heartbeat_cb"] is beat


async def test_an_animated_hero_remembers_its_still(tmp_path, monkeypatch):
    still = tmp_path / "shot_15.png"
    still.write_bytes(b"png")
    monkeypatch.setattr(slr, "_clear_image_gen_for_hero", AsyncMock())
    monkeypatch.setattr(slr, "_fit_hero_dims_to_free_vram", AsyncMock(return_value=(704, 400)))

    async def _clip(**kw):
        Path(kw["output_path"]).write_bytes(b"mp4")
        return True, ""

    monkeypatch.setattr(slr, "_render_generative_clip", _clip)
    monkeypatch.setattr(slr, "_clip_has_motion", AsyncMock(return_value=True))
    r = await slr._animate_hero(
        _hero(idx=15), still_path=str(still), site_config=None,
        orientation="landscape", post_id="p1",
    )
    assert r.clip_path == str(tmp_path / "shot_15.mp4")
    assert r.still_path == str(still)


# ---------------------------------------------------------------------------
# a collapse is re-rolled with the camera held
# ---------------------------------------------------------------------------

# f555bedc long shot 15, as stored: every render of it pulled the camera back,
# and the re-roll that #4028 made possible asked for the same pull-back again.
_DIRECTOR_MOTION = (
    "slow zoom out from the screen to reveal the person's focused expression; "
    "glowing lines connect the desk to the horizon"
)
_HELD = DEFAULTS["video_hero_collapse_reroll_motion"]


def _collapsed_state(tmp_path: Path, **qa_over) -> slr._ShotState:
    """The re-roll's incumbent: the opening passed, the final frame collapsed."""
    st = _state(tmp_path)
    st.shot = st.shot.model_copy(update={
        "prompt": ("a person at a small desk with multiple holographic screens "
                   "connected to a distant cloud"),
        "motion": _DIRECTOR_MOTION,
    })
    qa = {"score": 30.0, "reason": "final frame lost its detail", "frame": "final",
          "opening_score": 92.0, "detail_collapse": True}
    qa.update(qa_over)
    st.qa = ShotQAResult(**qa)
    return st


def _stub_re_roll_paths(monkeypatch) -> SimpleNamespace:
    """Stub both ways a hero re-roll renders and record them in order.

    ``("fresh", shot, None)``: a new still, through ``_render_one_shot``.
    ``("again", shot, still)``: the vetted still again, through
    ``_animate_hero``. Each writes its clip where the real path would, and the
    card clears are mocks the test can inspect.
    """
    calls: list[tuple[str, Shot, str | None]] = []

    async def _render(shot, *, prior_clip, attempt, work_dir, **kw):
        calls.append(("fresh", shot, None))
        clip = Path(work_dir) / "shot_15.mp4"
        clip.write_bytes(f"candidate-{attempt}".encode())
        return slr.ShotRenderResult(idx=15, source=shot.source, success=True,
                                    clip_path=str(clip), duration_s=5.0)

    async def _animate(shot, *, still_path, **kw):
        calls.append(("again", shot, still_path))
        clip = Path(still_path).with_suffix(".mp4")
        clip.write_bytes(b"re-animated")
        return slr.ShotRenderResult(idx=15, source=shot.source, success=True,
                                    clip_path=str(clip), duration_s=5.0,
                                    still_path=still_path)

    paths = SimpleNamespace(calls=calls, ready=AsyncMock(), soft=AsyncMock())
    monkeypatch.setattr(slr, "_render_one_shot", _render)
    monkeypatch.setattr(slr, "_animate_hero", _animate)
    monkeypatch.setattr(slr, "_ready_card_for_escalation", paths.ready)
    monkeypatch.setattr(slr, "_soft_clear_card", paths.soft)
    return paths


def _judge(monkeypatch, *verdicts: ShotQAResult) -> None:
    """The judge's verdicts on the candidates, in order; the last repeats."""
    queue = list(verdicts)

    async def _score(**kw):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(slr, "score_shot_frame", _score)


def _collapse() -> ShotQAResult:
    """A candidate that collapsed again: 30, the same as the incumbent's."""
    return ShotQAResult(score=30.0, reason="final frame lost its detail", frame="final",
                        opening_score=92.0, detail_collapse=True)


async def _repair(st, *, site_config=None, max_retries=2, tmp_path):
    await slr._repair_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=max_retries),
        site_config=_sc() if site_config is None else site_config,
        render_kwargs={"work_dir": tmp_path}, pool=object(),
    )


async def test_a_collapsed_hero_is_re_rolled_with_its_camera_held(tmp_path, monkeypatch):
    """The shot's own motion is what emptied the frame, so a re-roll with it
    spends ~5 min of GPU asking for the same pull-back."""
    st = _collapsed_state(tmp_path)
    director = st.shot
    paths = _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, ShotQAResult(score=20.0, reason="worse"))
    await _repair(st, tmp_path=tmp_path)
    assert [shot.motion for _, shot, _ in paths.calls] == [_HELD, _HELD]
    first = paths.calls[0][1]
    assert first.model_dump(exclude={"motion"}) == director.model_dump(exclude={"motion"}), (
        "only the camera changes: the subject is still the director's"
    )
    assert st.shot is director and st.shot.motion == _DIRECTOR_MOTION
    # Keep-best and the per-round directories are unchanged by the swap.
    assert (tmp_path / "shot_15.mp4").read_bytes() == b"incumbent-clip"
    assert (tmp_path / "repair1" / "shot_15.mp4").exists()
    assert (tmp_path / "repair2" / "shot_15.mp4").exists()
    assert st.qa.score == 30.0 and st.attempts == 2


async def test_a_held_camera_that_wins_replaces_the_clip_not_the_shot(tmp_path, monkeypatch):
    st = _collapsed_state(tmp_path)
    director = st.shot
    _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, ShotQAResult(score=92.0, reason="clean", frame="opening",
                                     opening_score=92.0))
    await _repair(st, tmp_path=tmp_path)
    assert st.result.clip_path == str(tmp_path / "repair1" / "shot_15.mp4")
    assert st.qa.score == 92.0 and st.attempts == 1
    assert st.shot is director, "the shot list keeps the director's motion"


@pytest.mark.parametrize(("setting", "motion"), [
    ("locked-off camera on the desk, the person centred", "locked-off camera on the desk, the person centred"),
    ("", _DIRECTOR_MOTION),  # emptied: re-roll with the shot's own motion
])
async def test_the_held_camera_is_db_tunable(tmp_path, monkeypatch, setting, motion):
    st = _collapsed_state(tmp_path)
    paths = _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, ShotQAResult(score=20.0, reason="worse"))
    await _repair(st, site_config=_sc(video_hero_collapse_reroll_motion=setting),
                  max_retries=1, tmp_path=tmp_path)
    assert [shot.motion for _, shot, _ in paths.calls] == [motion]


async def test_a_judged_low_ending_keeps_the_directors_motion(tmp_path, monkeypatch):
    """Garbled text or a warped ending is not the camera's doing."""
    st = _collapsed_state(tmp_path, score=45.0, reason="garbled text: banner",
                          detail_collapse=False)
    paths = _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, ShotQAResult(score=20.0, reason="worse"))
    await _repair(st, max_retries=1, tmp_path=tmp_path)
    assert [(how, shot.motion) for how, shot, _ in paths.calls] == [("fresh", _DIRECTOR_MOTION)]


async def test_a_collapse_on_a_losing_candidate_holds_the_next_re_roll(tmp_path, monkeypatch):
    """A candidate that collapses loses keep-best to a 45, but it has shown
    what the shot's motion does: the next re-roll holds the camera."""
    st = _collapsed_state(tmp_path, score=45.0, reason="garbled text: banner",
                          detail_collapse=False)
    paths = _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, _collapse(), ShotQAResult(score=40.0, reason="final frame: soft"))
    await _repair(st, tmp_path=tmp_path)
    # A new still both times: the verdict being repaired is the judge's, which
    # does not vouch for the still the way a passed opening before a collapse does.
    assert [(how, shot.motion) for how, shot, _ in paths.calls] == [
        ("fresh", _DIRECTOR_MOTION), ("fresh", _HELD),
    ]
    assert st.qa.score == 45.0, "neither candidate beat the incumbent"


async def test_a_collapse_the_winner_replaced_still_holds_the_next_re_roll(tmp_path, monkeypatch):
    """A held re-roll can beat the collapse and still fall short (garbled at
    its end, 45). The collapse it replaced was the director's motion at work,
    so the next re-roll keeps the camera held. It used to be forgotten with the
    verdict: the second round went back to the pull-back."""
    st = _collapsed_state(tmp_path)
    paths = _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch,
           ShotQAResult(score=45.0, reason="garbled text: banner", frame="final",
                        opening_score=92.0, text="garbled"),
           ShotQAResult(score=40.0, reason="final frame: soft"))
    await _repair(st, tmp_path=tmp_path)
    # Round 2 repairs the garbled ending, the judge's verdict: a new still.
    assert [(how, shot.motion) for how, shot, _ in paths.calls] == [
        ("again", _HELD), ("fresh", _HELD),
    ]
    assert st.collapse_seen


@pytest.mark.parametrize("source", ["image_kenburns", "image_gen"])
def test_only_a_hero_has_a_camera_to_hold(tmp_path, source):
    st = _collapsed_state(tmp_path)
    st.shot = st.shot.model_copy(update={"source": source})
    assert slr._candidate_shot(st, _sc()) is st.shot


# ---------------------------------------------------------------------------
# a collapse whose opening passed re-animates its vetted still
# ---------------------------------------------------------------------------


async def test_a_collapse_re_roll_re_animates_the_vetted_still(tmp_path, monkeypatch):
    """The opening passed at 92, so the still is sound and the motion emptied
    the frame. The held camera replaces the motion; a new still would spend
    image-gen's cold start and a render (2 min 42 s of a 9 min round on
    f555bedc) on a question the opening already answered."""
    st = _collapsed_state(tmp_path)
    paths = _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, ShotQAResult(score=92.0, reason="clean", frame="opening",
                                     opening_score=92.0))
    await _repair(st, max_retries=1, tmp_path=tmp_path)

    (how, shot, still), = paths.calls
    assert how == "again" and shot.motion == _HELD
    assert still == str(tmp_path / "repair1" / "shot_15.png")
    assert Path(still).read_bytes() == b"incumbent-still", "the incumbent's own still, copied"
    paths.ready.assert_not_awaited()  # image-gen is not needed: nothing to wait for
    paths.soft.assert_awaited_once_with("re-animation")
    assert st.reanimations == 1
    assert st.result.clip_path == str(tmp_path / "repair1" / "shot_15.mp4")
    assert st.result.still_path == still
    # The incumbent's files are where they were, untouched.
    assert (tmp_path / "shot_15.mp4").read_bytes() == b"incumbent-clip"
    assert (tmp_path / "shot_15.png").read_bytes() == b"incumbent-still"


async def test_a_losing_re_animation_leaves_the_incumbent_and_its_fallback(tmp_path, monkeypatch):
    """Keep-best and ``fallback_still`` are the same as for a new still: the
    loser is discarded, and the incumbent ships the still its opening vetted."""
    st = _collapsed_state(tmp_path)
    incumbent = st.result
    _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, _collapse())
    await _repair(st, max_retries=1, tmp_path=tmp_path)
    assert st.result is incumbent and st.qa.score == 30.0
    assert (tmp_path / "shot_15.mp4").read_bytes() == b"incumbent-clip"
    assert (tmp_path / "repair1" / "shot_15.mp4").read_bytes() == b"re-animated"

    monkeypatch.setattr(slr, "emit_finding", lambda **kw: None)
    out = await slr._finalize_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=1),
        pool=None, post_id="p1",
    )
    assert out[0].clip_path == str(tmp_path / "shot_15.png")


async def test_a_winning_re_animation_still_short_falls_back_to_its_copy(tmp_path, monkeypatch):
    st = _collapsed_state(tmp_path)
    _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, ShotQAResult(score=45.0, reason="final frame: garbled text",
                                     frame="final", opening_score=92.0, text="garbled"))
    await _repair(st, max_retries=1, tmp_path=tmp_path)
    assert st.qa.score == 45.0, "45 beats the collapse's 30"

    monkeypatch.setattr(slr, "emit_finding", lambda **kw: None)
    out = await slr._finalize_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=1),
        pool=None, post_id="p1",
    )
    assert out[0].clip_path == str(tmp_path / "repair1" / "shot_15.png")
    assert Path(out[0].clip_path).read_bytes() == b"incumbent-still"


async def test_the_second_round_draws_a_fresh_still(tmp_path, monkeypatch):
    """The decision: the held camera kept the frame on all 4 measured renders
    of the collapsed shot, so when a held re-animation of this still collapses
    too, the still is the suspect. Round 2 renders a new one, with the camera
    still held, and only that round wakes image-gen."""
    st = _collapsed_state(tmp_path)
    paths = _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, _collapse(), ShotQAResult(score=20.0, reason="worse"))
    await _repair(st, max_retries=2, tmp_path=tmp_path)
    assert [(how, shot.motion) for how, shot, _ in paths.calls] == [
        ("again", _HELD), ("fresh", _HELD),
    ]
    paths.ready.assert_awaited_once()
    assert st.reanimations == 1 and st.attempts == 2
    # Round 2 lost as well: the incumbent keeps the still its opening vetted.
    assert st.result.still_path == str(tmp_path / "shot_15.png")


@pytest.mark.parametrize(("cap", "expected"), [
    ("0", ["fresh", "fresh"]),   # the pre-2026-09-25 re-roll
    ("-1", ["fresh", "fresh"]),  # nonsense reads as off, not as unlimited
    ("2", ["again", "again"]),
])
async def test_the_re_animation_cap_is_db_tunable(tmp_path, monkeypatch, cap, expected):
    st = _collapsed_state(tmp_path)
    paths = _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, _collapse())
    await _repair(st, site_config=_sc(video_hero_collapse_reroll_reanimate_max=cap),
                  max_retries=2, tmp_path=tmp_path)
    assert [how for how, _, _ in paths.calls] == expected
    assert all(shot.motion == _HELD for _, shot, _ in paths.calls)
    for n, (how, _, still) in enumerate(paths.calls, start=1):
        if how == "again":  # each round's copy lives in that round's directory
            assert still == str(tmp_path / f"repair{n}" / "shot_15.png")


def _unvetted(tmp_path: Path, case: str) -> tuple[slr._ShotState, SiteConfig]:
    st, sc = _collapsed_state(tmp_path), _sc()
    if case == "opening_failed":  # the still itself is suspect
        st.qa.opening_score = 55.0
    elif case == "judged_ending":  # garbled text may be the still's own doing
        st.qa = ShotQAResult(score=45.0, reason="garbled", frame="final",
                             opening_score=92.0, text="garbled")
        st.collapse_seen = True  # a collapse elsewhere holds the camera anyway
    elif case == "camera_not_held":  # same motion again: only the seed changes
        sc = _sc(video_hero_collapse_reroll_motion="")
    elif case == "still_missing":
        os.remove(st.result.still_path)
    elif case == "already_a_still":  # the incumbent shipped its still, no clip
        st.result.clip_path = st.result.still_path
    elif case == "not_a_hero":
        st.shot = st.shot.model_copy(update={"source": "image_kenburns"})
    return st, sc


def test_a_vetted_collapse_names_its_still(tmp_path):
    st, sc = _unvetted(tmp_path, "vetted")
    qa = slr._QAConfig(enabled=True, threshold=60.0, max_retries=2)
    assert slr._reanimation_still(st, slr._candidate_shot(st, sc), qa=qa, site_config=sc) == (
        str(tmp_path / "shot_15.png")
    )


@pytest.mark.parametrize("case", [
    "opening_failed", "judged_ending", "camera_not_held", "still_missing",
    "already_a_still", "not_a_hero",
])
def test_only_a_vetted_collapse_is_re_animated(tmp_path, case):
    st, sc = _unvetted(tmp_path, case)
    qa = slr._QAConfig(enabled=True, threshold=60.0, max_retries=2)
    assert slr._reanimation_still(st, slr._candidate_shot(st, sc), qa=qa, site_config=sc) is None


async def test_a_re_animation_never_writes_beside_the_incumbent(tmp_path, monkeypatch):
    """Handed the incumbent's own directory instead of the round's, the copy
    would be the incumbent's still and the clip would land on the incumbent's
    clip: the overwrite #4028 fixed. It declines instead."""
    st = _collapsed_state(tmp_path)
    paths = _stub_re_roll_paths(monkeypatch)
    got = await slr._reanimate_hero(st.shot, still=st.result.still_path,
                                    render_kwargs={"work_dir": tmp_path})
    assert got is None and paths.calls == []
    paths.soft.assert_not_awaited()
    assert (tmp_path / "shot_15.mp4").read_bytes() == b"incumbent-clip"


async def test_a_still_that_cannot_be_copied_is_rendered_anew(tmp_path, monkeypatch):
    st = _collapsed_state(tmp_path)
    paths = _stub_re_roll_paths(monkeypatch)
    _judge(monkeypatch, ShotQAResult(score=20.0, reason="worse"))

    def _full_disk(src, dst, *a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(slr.shutil, "copy2", _full_disk)
    await _repair(st, max_retries=1, tmp_path=tmp_path)
    assert [(how, shot.motion) for how, shot, _ in paths.calls] == [("fresh", _HELD)]
    paths.ready.assert_awaited_once()
    assert st.reanimations == 0


async def test_the_re_animation_frees_the_card_softly_and_skips_image_gen(
    tmp_path, monkeypatch, _inert_reclaim_rungs,
):
    """No still renders, so no wait for image-gen. The soft levers still run,
    and before the animation: its own clear opens with image-gen's hard rung,
    which on a card still holding ComfyUI's last pool reads an idle image-gen
    as a squat and queues its restart (11.6 GB free, 2026-09-25)."""
    order: list[str] = []
    for name, rung in _inert_reclaim_rungs.items():
        rung.side_effect = lambda *a, _n=name, **kw: order.append(_n)
    wait = AsyncMock()
    monkeypatch.setattr(slr, "_wait_image_gen_ready", wait)
    seen: dict = {}

    async def _animate(shot, *, still_path, **kw):
        order.append("animate")
        seen.update(kw, still_path=still_path)
        return slr.ShotRenderResult(idx=15, source=shot.source, success=True,
                                    clip_path=str(Path(still_path).with_suffix(".mp4")))

    monkeypatch.setattr(slr, "_animate_hero", _animate)
    st = _collapsed_state(tmp_path)
    beat, sc = AsyncMock(), _sc()
    work = tmp_path / "repair1"
    work.mkdir()
    with patch.object(slr.asyncio, "sleep", AsyncMock()):
        got = await slr._reanimate_hero(
            st.shot, still=st.result.still_path,
            render_kwargs={"work_dir": work, "site_config": sc, "orientation": "portrait",
                           "post_id": "p1", "heartbeat_cb": beat, "image_gen_url": "http://x"},
        )
    assert order == ["_unload_comfyui", "_unload_ollama_models", "_unload_chatterbox",
                     "_unload_rife", "animate"]
    _inert_reclaim_rungs["_unload_comfyui"].assert_awaited_once_with(hard=False)
    wait.assert_not_awaited()
    assert seen == {"still_path": str(work / "shot_15.png"), "site_config": sc,
                    "orientation": "portrait", "post_id": "p1", "heartbeat_cb": beat}
    assert got.clip_path == str(work / "shot_15.mp4")


async def test_the_re_animated_still_reaches_the_animator(tmp_path, monkeypatch):
    """The whole seam, from the verdict to what ComfyUI is sent: the incumbent's
    still (a copy, in the round's directory), its own description, then the
    held camera in place of the pull-back. No still is rendered."""
    st = _collapsed_state(tmp_path)
    sent: list[dict] = []

    async def _clip(**kw):
        sent.append(kw)
        Path(kw["output_path"]).write_bytes(b"mp4")
        return True, ""

    new_still = AsyncMock()
    monkeypatch.setattr(slr, "_render_hero_still", new_still)
    monkeypatch.setattr(slr, "_clear_image_gen_for_hero", AsyncMock())
    monkeypatch.setattr(slr, "_fit_hero_dims_to_free_vram", AsyncMock(return_value=(832, 480)))
    monkeypatch.setattr(slr, "_render_generative_clip", _clip)
    monkeypatch.setattr(slr, "_clip_has_motion", AsyncMock(return_value=True))
    monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
    monkeypatch.setattr(slr, "_soft_clear_card", AsyncMock())
    _judge(monkeypatch, ShotQAResult(score=92.0, reason="clean", frame="opening",
                                     opening_score=92.0))
    await slr._repair_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=2),
        site_config=_sc(), pool=object(),
        render_kwargs={"work_dir": tmp_path, "image_gen_url": "", "site_config": _sc(),
                       "http_client_factory": None, "orientation": "landscape",
                       "post_id": "p1"},
    )
    new_still.assert_not_awaited()
    (kw,) = sent
    assert kw["prompt"] == f"{st.shot.prompt}. Camera and motion: {_HELD}"
    assert kw["image_path"] == str(tmp_path / "repair1" / "shot_15.png")
    assert Path(kw["image_path"]).read_bytes() == b"incumbent-still"
    assert kw["output_path"] == str(tmp_path / "repair1" / "shot_15.mp4")
    assert st.result.clip_path == str(tmp_path / "repair1" / "shot_15.mp4")
    assert st.result.still_path == str(tmp_path / "repair1" / "shot_15.png"), (
        "the winner's own still, which a later fallback_still would ship"
    )
    assert (tmp_path / "shot_15.mp4").read_bytes() == b"incumbent-clip"


async def test_with_re_animation_off_the_held_camera_gets_a_new_still(tmp_path, monkeypatch):
    """``video_hero_collapse_reroll_reanimate_max=0``: the #4045 seam, a fresh
    still animated with the held camera."""
    st = _collapsed_state(tmp_path)
    prompts: list[str] = []

    async def _still(shot, *, work_dir, **kw):
        path = Path(work_dir) / f"shot_{shot.idx:02d}.png"
        path.write_bytes(b"png")
        return slr.ShotRenderResult(idx=shot.idx, source=shot.source, success=True,
                                    clip_path=str(path), duration_s=shot.duration_s)

    async def _clip(**kw):
        prompts.append(kw["prompt"])
        Path(kw["output_path"]).write_bytes(b"mp4")
        return True, ""

    monkeypatch.setattr(slr, "_render_hero_still", _still)
    monkeypatch.setattr(slr, "_clear_image_gen_for_hero", AsyncMock())
    monkeypatch.setattr(slr, "_fit_hero_dims_to_free_vram", AsyncMock(return_value=(832, 480)))
    monkeypatch.setattr(slr, "_render_generative_clip", _clip)
    monkeypatch.setattr(slr, "_clip_has_motion", AsyncMock(return_value=True))
    ready = AsyncMock()
    monkeypatch.setattr(slr, "_ready_card_for_escalation", ready)
    _judge(monkeypatch, ShotQAResult(score=92.0, reason="clean", frame="opening",
                                     opening_score=92.0))
    sc = _sc(video_hero_collapse_reroll_reanimate_max="0")
    await slr._repair_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=2),
        site_config=sc, pool=object(),
        render_kwargs={"work_dir": tmp_path, "image_gen_url": "", "site_config": sc,
                       "http_client_factory": None, "orientation": "landscape",
                       "post_id": "p1"},
    )
    assert prompts == [f"{st.shot.prompt}. Camera and motion: {_HELD}"]
    ready.assert_awaited_once()
    assert st.result.still_path == str(tmp_path / "repair1" / "shot_15.png")
    assert (tmp_path / "repair1" / "shot_15.png").read_bytes() == b"png", "a new still"
