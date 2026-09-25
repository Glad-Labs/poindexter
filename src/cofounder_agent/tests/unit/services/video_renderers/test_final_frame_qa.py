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
again, so a collapse is now re-rolled with the camera held.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services.settings_defaults import DEFAULTS
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr
from poindexter.services.video_renderers import shot_vision_qa as sq
from poindexter.services.video_renderers.shot_vision_qa import ShotQAResult

_MODEL = "ollama/qwen3-vl:30b-a3b-instruct"


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


def _recording_render(rendered: list[Shot]):
    """A ``_render_one_shot`` that writes a candidate clip and records its shot."""

    async def _render(shot, *, prior_clip, attempt, work_dir, **kw):
        rendered.append(shot)
        clip = Path(work_dir) / "shot_15.mp4"
        clip.write_bytes(f"candidate-{attempt}".encode())
        return slr.ShotRenderResult(idx=15, source=shot.source, success=True,
                                    clip_path=str(clip), duration_s=5.0)

    return _render


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
    rendered: list[Shot] = []
    monkeypatch.setattr(slr, "_render_one_shot", _recording_render(rendered))
    monkeypatch.setattr(slr, "score_shot_frame",
                        AsyncMock(return_value=ShotQAResult(score=20.0, reason="worse")))
    monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
    await _repair(st, tmp_path=tmp_path)
    assert [s.motion for s in rendered] == [_HELD, _HELD]
    assert rendered[0].model_dump(exclude={"motion"}) == director.model_dump(exclude={"motion"}), (
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
    rendered: list[Shot] = []
    monkeypatch.setattr(slr, "_render_one_shot", _recording_render(rendered))
    monkeypatch.setattr(slr, "score_shot_frame", AsyncMock(return_value=ShotQAResult(
        score=92.0, reason="clean", frame="opening", opening_score=92.0)))
    monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
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
    rendered: list[Shot] = []
    monkeypatch.setattr(slr, "_render_one_shot", _recording_render(rendered))
    monkeypatch.setattr(slr, "score_shot_frame",
                        AsyncMock(return_value=ShotQAResult(score=20.0, reason="worse")))
    monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
    await _repair(st, site_config=_sc(video_hero_collapse_reroll_motion=setting),
                  max_retries=1, tmp_path=tmp_path)
    assert [s.motion for s in rendered] == [motion]


async def test_a_judged_low_ending_keeps_the_directors_motion(tmp_path, monkeypatch):
    """Garbled text or a warped ending is not the camera's doing."""
    st = _collapsed_state(tmp_path, score=45.0, reason="garbled text: banner",
                          detail_collapse=False)
    rendered: list[Shot] = []
    monkeypatch.setattr(slr, "_render_one_shot", _recording_render(rendered))
    monkeypatch.setattr(slr, "score_shot_frame",
                        AsyncMock(return_value=ShotQAResult(score=20.0, reason="worse")))
    monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
    await _repair(st, max_retries=1, tmp_path=tmp_path)
    assert [s.motion for s in rendered] == [_DIRECTOR_MOTION]


async def test_a_collapse_on_a_losing_candidate_holds_the_next_re_roll(tmp_path, monkeypatch):
    """A candidate that collapses loses keep-best to a 45, but it has shown
    what the shot's motion does: the next re-roll holds the camera."""
    st = _collapsed_state(tmp_path, score=45.0, reason="garbled text: banner",
                          detail_collapse=False)
    rendered: list[Shot] = []
    monkeypatch.setattr(slr, "_render_one_shot", _recording_render(rendered))
    monkeypatch.setattr(slr, "score_shot_frame", AsyncMock(side_effect=[
        ShotQAResult(score=30.0, reason="final frame lost its detail", frame="final",
                     opening_score=92.0, detail_collapse=True),
        ShotQAResult(score=40.0, reason="final frame: soft"),
    ]))
    monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
    await _repair(st, tmp_path=tmp_path)
    assert [s.motion for s in rendered] == [_DIRECTOR_MOTION, _HELD]
    assert st.qa.score == 45.0, "neither candidate beat the incumbent"


@pytest.mark.parametrize("source", ["image_kenburns", "image_gen"])
def test_only_a_hero_has_a_camera_to_hold(tmp_path, source):
    st = _collapsed_state(tmp_path)
    st.shot = st.shot.model_copy(update={"source": source})
    assert slr._candidate_shot(st, _sc()) is st.shot


async def test_the_held_camera_reaches_the_animator_prompt(tmp_path, monkeypatch):
    """The whole seam, from the verdict to the text ComfyUI is sent: the
    still's own description, then the held camera in place of the pull-back."""
    from tests.unit._gpu_isolation import make_reclaim_rungs_inert

    make_reclaim_rungs_inert(monkeypatch)
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
    monkeypatch.setattr(slr, "_ready_card_for_escalation", AsyncMock())
    monkeypatch.setattr(slr, "score_shot_frame", AsyncMock(return_value=ShotQAResult(
        score=92.0, reason="clean", frame="opening", opening_score=92.0)))
    await slr._repair_pass(
        [st], qa=slr._QAConfig(enabled=True, threshold=60.0, max_retries=2),
        site_config=_sc(), pool=object(),
        render_kwargs={"work_dir": tmp_path, "image_gen_url": "", "site_config": _sc(),
                       "http_client_factory": None, "orientation": "landscape",
                       "post_id": "p1"},
    )
    assert prompts == [f"{st.shot.prompt}. Camera and motion: {_HELD}"]
    assert st.result.clip_path == str(tmp_path / "repair1" / "shot_15.mp4")
    assert st.result.still_path == str(tmp_path / "repair1" / "shot_15.png"), (
        "the winner's own still, which a later fallback_still would ship"
    )
