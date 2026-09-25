"""Custom YouTube thumbnails — ``services/video_thumbnail.py``.

Every upload before 2026-09-25 showed a frame YouTube picked at random. The
thumbnail is composed (real type over a text-free background, rendered by
chromium), so these tests pin the parts that decide what ships: the settings
map, the layout, the hook rules (length, title overlap, invented numbers), the
background fallback order, and how a stored thumbnail follows its video.
"""

from __future__ import annotations

import io
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import video_thumbnail as vt


class _SC:
    def __init__(self, **values: Any):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


# ---------------------------------------------------------------------------
# settings → look
# ---------------------------------------------------------------------------


def test_style_defaults_and_overrides_and_clamps():
    d = vt.style_from_settings(None)
    assert (d.width, d.height, d.font_family, d.person_layout) == (1280, 720, "JetBrains Mono", "right")
    s = vt.style_from_settings(_SC(
        video_thumbnail_font_family="Liberation Sans", video_thumbnail_text_position="nowhere",
        video_thumbnail_person_layout="cover", video_thumbnail_scrim_opacity="7",
        video_thumbnail_jpeg_quality="200", video_thumbnail_uppercase="false",
        site_name="Glad Labs",
    ))
    assert s.font_family == "Liberation Sans" and s.person_layout == "cover"
    assert s.text_position == "left"  # unknown value → default
    assert s.scrim_opacity == 1.0 and s.jpeg_quality == 95  # clamped
    assert s.uppercase is False and s.brand_mark == "Glad Labs"  # mark falls back to site_name
    assert vt.style_from_settings(_SC(video_thumbnail_brand_mark_enabled="false", site_name="X")).brand_mark == ""
    assert vt.style_from_settings(_SC(video_thumbnail_person_focus_y_pct="140")).person_focus_y_pct == 100


def test_every_colour_comes_from_a_setting():
    """Scrim, glow, grid and mark follow the colour settings, so changing the
    ground (say, to a light one) changes the scrim with it instead of leaving
    a hard-coded navy behind the text."""
    style = vt.ThumbnailStyle(
        background_color="white", accent_color="#ff0000", text_color="#112233",
        brand_mark="GL", brand_mark_color="#abcdef", person_focus_y_pct=40,
    )
    html = vt.render_thumbnail_html(hook="a b", background_uri=None, style=style)
    assert "color-mix(in srgb, white 78.0%, transparent)" in html     # scrim: the ground
    assert "color-mix(in srgb, #ff0000 16.0%, transparent)" in html   # glow: the accent
    assert "color-mix(in srgb, #112233 8.0%, transparent)" in html    # grid: the text colour
    assert "color:#abcdef" in html                                      # mark
    assert "text-shadow:0 4px 18px color-mix(in srgb, white 55.0%" in html  # shadow: the ground
    assert "rgba(" not in html  # no colour left that a setting cannot reach
    person = vt.render_thumbnail_html(
        hook="a b", background_uri="data:image/png;base64,AA", style=style, image_layout="right",
    )
    assert "object-position:center 40%" in person


def test_html_escapes_the_hook_and_accents_the_last_word():
    html = vt.render_thumbnail_html(
        hook="<b>Zero</b> API bill", background_uri=None, style=vt.ThumbnailStyle(),
    )
    assert "&lt;B&gt;ZERO&lt;/B&gt; API" in html and '<span class="accent">BILL</span>' in html
    assert 'data-fit' in html and "class=\"grid\"" in html  # no image → brand ground


def test_a_hyphenated_word_is_never_split_at_its_hyphen():
    """"BEAT THE ZERO-CLICK ERA" rendered "ZERO-" alone on a line."""
    html = vt.render_thumbnail_html(
        hook="Beat the zero-click era", background_uri=None, style=vt.ThumbnailStyle(),
    )
    assert '<span class="nw">ZERO-CLICK</span>' in html and ".hook .nw" in html
    accent = vt.render_thumbnail_html(
        hook="Real code, no drag-and-drop", background_uri=None, style=vt.ThumbnailStyle(),
    )
    assert '<span class="accent"><span class="nw">DRAG-AND-DROP</span></span>' in accent
    assert "<b>" not in vt.render_thumbnail_html(
        hook="<b>x</b>-y z", background_uri=None, style=vt.ThumbnailStyle(),
    )  # still escaped inside the span


def test_a_person_sits_right_with_the_text_beside_it():
    style = vt.ThumbnailStyle(person_width_pct=60)
    right = vt.render_thumbnail_html(hook="x y", background_uri="data:image/png;base64,AA", style=style, image_layout="right")
    cover = vt.render_thumbnail_html(hook="x y", background_uri="data:image/png;base64,AA", style=style, image_layout="cover")
    assert 'class="bg person"' in right and "width:60%" in right
    assert 'class="bg person"' not in cover and 'class="bg"' in cover


def test_jpeg_stays_under_the_byte_ceiling():
    from PIL import Image

    img = Image.effect_noise((640, 360), 90).convert("RGB")  # hard to compress
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    big = vt.jpeg_under(buf.getvalue(), quality=95, max_bytes=10_000_000)
    small = vt.jpeg_under(buf.getvalue(), quality=95, max_bytes=len(big) // 2)
    assert len(small) <= len(big) // 2 and small[:2] == b"\xff\xd8"


# ---------------------------------------------------------------------------
# hook rules
# ---------------------------------------------------------------------------

_TITLE = "Skip NCCL: How LoRA Adapter Syncing Makes GRPO Training Work"


@pytest.mark.parametrize(("raw", "ok"), [
    ("Sync LoRA through a bucket", True),
    ('"No NCCL needed."', True),               # quotes and full stop stripped
    ("Thumbnail: 3 jobs, one bucket", True),   # label stripped; 3 is in the source
    ("RAG: one bucket", True),                 # a subject before a colon is kept
    ("A really long thumbnail line that no one can read on a phone", False),
    ("Skip NCCL", False),                      # only title words
    ("40% faster training", False),            # 40 appears nowhere in the source
    ("Here is the text", False),               # instruction echo
    ("", False),
])
def test_hook_rules(raw, ok):
    hook, reason = vt.clean_thumbnail_hook(
        raw, title=_TITLE, source_text="the trainer and 3 vLLM jobs share one bucket", max_chars=32,
    )
    assert bool(hook) is ok, (hook, reason)
    assert (reason == "") is ok
    if raw.startswith("RAG:"):
        assert hook == "RAG: one bucket"


_RECENT = ["STOP THE CRASHES", "Stop silent failures", "REAL CODE, NO COURSES"]


@pytest.mark.parametrize(("raw", "max_repeats", "ok"), [
    ("Stop the freeze", 2, False),        # "stop" already opens 2 of the recent 3
    ('"STOP, VRAM tips"', 2, False),       # quoting and punctuation do not hide it
    ("Stop the freeze", 3, True),         # the threshold is a setting
    ("Stop the freeze", 0, True),         # 0 switches the rule off
    ("Real code, real kids", 2, True),    # one earlier "real" is under the bar
    ("VRAM budget, no freezes", 2, True),
])
def test_a_run_of_same_opener_thumbnails_is_sent_back(raw, max_repeats, ok):
    hook, reason = vt.clean_thumbnail_hook(
        raw, title="Single-GPU VRAM Budgeting", source_text="", max_chars=40,
        recent_hooks=_RECENT, max_opener_repeats=max_repeats,
    )
    assert bool(hook) is ok, (hook, reason)
    if not ok:
        assert "opens with 'stop'" in reason and "what this video is about" in reason


class _Completion:
    def __init__(self, text):
        self.text = text


@pytest.mark.asyncio
async def test_a_rejected_hook_gets_one_corrective_retry():
    replies = iter([_Completion("40% faster GRPO"), _Completion("One bucket, no NCCL")])
    dispatch = AsyncMock(side_effect=lambda *a, **k: next(replies))
    sc = _SC(video_director_model="ollama/gemma", video_thumbnail_hook_max_chars="32")
    with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
        hook, note = await vt.generate_thumbnail_hook(
            title=_TITLE, summary="s", source_text="s", site_config=sc, pool=object(),
        )
    assert hook == "One bucket, no NCCL" and note == "retry"
    fix = dispatch.await_args_list[1].args[1][-1]["content"]  # the SKILL's video.thumbnail_hook_fix
    assert fix.startswith("FIX:") and "not in the video" in fix and "at most 32 characters" in fix


@pytest.mark.asyncio
async def test_the_hook_call_takes_its_parameters_from_settings():
    dispatch = AsyncMock(return_value=_Completion("One bucket, no NCCL"))
    sc = _SC(
        video_director_model="ollama/gemma", video_thumbnail_hook_temperature="0.3",
        video_thumbnail_hook_max_tokens="900", video_thumbnail_hook_timeout_seconds="45",
    )
    with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
        hook, _ = await vt.generate_thumbnail_hook(
            title=_TITLE, summary="s", source_text="s", site_config=sc, pool=object(),
        )
    kw = dispatch.await_args.kwargs
    assert hook and (kw["temperature"], kw["max_tokens"], kw["timeout_s"]) == (0.3, 900, 45.0)


@pytest.mark.asyncio
async def test_an_opener_repeat_is_retried_with_its_reason():
    replies = iter([_Completion("Stop the freeze"), _Completion("VRAM budget, no freezes")])
    dispatch = AsyncMock(side_effect=lambda *a, **k: next(replies))
    sc = _SC(video_director_model="ollama/gemma", video_thumbnail_hook_opener_max_repeats="2")
    with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
        hook, note = await vt.generate_thumbnail_hook(
            title="Single-GPU VRAM Budgeting", summary="s", source_text="s",
            site_config=sc, pool=object(), recent_hooks=_RECENT,
        )
    assert (hook, note) == ("VRAM budget, no freezes", "retry")
    assert "opens with 'stop'" in dispatch.await_args_list[1].args[1][-1]["content"]


@pytest.mark.asyncio
async def test_twice_rejected_means_no_text_and_the_hook_model_wins_over_the_director():
    dispatch = AsyncMock(return_value=_Completion("Skip NCCL"))
    sc = _SC(video_director_model="ollama/gemma", video_thumbnail_hook_model="ollama/small")
    with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
        hook, note = await vt.generate_thumbnail_hook(
            title=_TITLE, summary="s", source_text="", site_config=sc, pool=object(),
        )
    assert hook == "" and "rejected twice" in note
    assert dispatch.await_args.args[2] == "ollama/small"


@pytest.mark.asyncio
async def test_no_model_means_no_call():
    hook, note = await vt.generate_thumbnail_hook(
        title="t", summary="", source_text="", site_config=_SC(), pool=object(),
    )
    assert hook == "" and "video_director_model" in note


# ---------------------------------------------------------------------------
# background
# ---------------------------------------------------------------------------


def test_presenter_window_is_scaled_to_the_rendered_length():
    plan = {"shots": [
        {"source": "image_kenburns", "duration_s": 10, "narration_offset_s": 0},
        {"source": "presenter", "duration_s": 10, "narration_offset_s": 10},
    ]}
    assert vt.presenter_window_start(plan, 30.0) == pytest.approx(15.0)  # 20 s plan → 30 s video
    assert vt.presenter_window_start({"shots": [{"source": "pexels"}]}, 30.0) is None


@pytest.mark.asyncio
async def test_the_first_source_that_yields_an_image_wins(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with patch.object(vt, "_download", AsyncMock(return_value=None)) as dl, \
         patch.object(vt, "_persona_portrait_url", return_value="https://cdn.test/p.webp"), \
         patch.object(vt, "_frame", AsyncMock(return_value=str(tmp_path / "f.png"))):
        # featured fails, portrait fails (download None), frame works
        path, source = await vt.resolve_background(
            order=["featured_image", "presenter_portrait", "video_frame", "brand"],
            featured_image_url="https://cdn.test/f.webp", video_path=str(video), shot_list=None,
            site_config=None, dest_dir=str(tmp_path),
        )
    assert (source, path) == ("video_frame", str(tmp_path / "f.png"))
    assert [c.args[0] for c in dl.await_args_list] == ["https://cdn.test/f.webp", "https://cdn.test/p.webp"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://cdn.test/images/featured/brand-abc.webp",
    "https://cdn.test/images/featured/poindexter-brand-hero-fec475dd.webp",
    "https://cdn.test/images/charts/c1.png",
    "https://cdn.test/images/screenshots/s1.png",
])
async def test_a_featured_image_with_its_own_type_is_passed_over(tmp_path, url):
    """A composed brand card as the background put the hook on top of the
    card's own words (the first backfill, 2026-09-25)."""
    with patch.object(vt, "_download", AsyncMock(return_value=str(tmp_path / "p.webp"))) as dl, \
         patch.object(vt, "_persona_portrait_url", return_value="https://cdn.test/p.webp"):
        path, source = await vt.resolve_background(
            order=["featured_image", "presenter_portrait", "brand"], featured_image_url=url,
            video_path="", shot_list=None, site_config=None, dest_dir=str(tmp_path),
        )
    assert source == "presenter_portrait"
    assert [c.args[0] for c in dl.await_args_list] == ["https://cdn.test/p.webp"]


@pytest.mark.asyncio
async def test_recent_hooks_are_the_other_thumbnails_newest_first():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"hook": "STOP THE CRASHES"}, {"hook": "Real code"}])
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    assert await vt.load_recent_hooks(pool, "t1", 12) == ["STOP THE CRASHES", "Real code"]
    sql, task_id, window = conn.fetch.await_args.args
    assert "task_id::text <> $1" in sql and (task_id, window) == ("t1", 12)
    assert await vt.load_recent_hooks(pool, "t1", 0) == []   # window 0 = rule off, no query
    assert conn.fetch.await_count == 1


@pytest.mark.asyncio
async def test_a_failed_recent_hook_lookup_only_skips_the_variety_rule():
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=RuntimeError("db gone"))
    assert await vt.load_recent_hooks(pool, "t1", 12) == []


@pytest.mark.asyncio
async def test_compose_hands_the_recent_hooks_to_the_hook_writer(tmp_path):
    ctx = {"title": "T", "summary": "s", "featured_image_url": "", "slug": "t"}
    gen = AsyncMock(return_value=("New words", ""))
    with patch.object(vt, "load_post_context", AsyncMock(return_value=ctx)), \
         patch.object(vt, "load_recent_hooks", AsyncMock(return_value=["STOP X"])) as recent, \
         patch.object(vt, "generate_thumbnail_hook", gen), \
         patch.object(vt, "render_thumbnail_jpeg", AsyncMock(return_value=b"\xff\xd8j")):
        await vt.compose_video_thumbnail(
            task_id="t", pool=object(), site_config=_SC(video_thumbnail_hook_opener_window="5"),
            out_path=str(tmp_path / "t.jpg"),
        )
    assert recent.await_args.args[1:] == ("t", 5)
    assert gen.await_args.kwargs["recent_hooks"] == ["STOP X"]


@pytest.mark.asyncio
async def test_running_out_of_sources_is_the_brand_ground(tmp_path):
    path, source = await vt.resolve_background(
        order=["featured_image", "video_frame"], featured_image_url="", video_path="",
        shot_list=None, site_config=None, dest_dir=str(tmp_path),
    )
    assert (path, source) == (None, "brand")


# ---------------------------------------------------------------------------
# compose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compose_is_off_when_switched_off():
    assert await vt.compose_video_thumbnail(
        task_id="t", pool=None, site_config=_SC(video_thumbnail_enabled="false"),
    ) is None


@pytest.mark.asyncio
async def test_compose_degrades_to_image_only_and_writes_the_jpeg(tmp_path):
    out = tmp_path / "t.jpg"
    ctx = {"title": "T", "summary": "s", "featured_image_url": "", "slug": "t"}
    render = AsyncMock(return_value=b"\xff\xd8jpeg")
    with patch.object(vt, "load_post_context", AsyncMock(return_value=ctx)), \
         patch.object(vt, "generate_thumbnail_hook", AsyncMock(return_value=("", "rejected twice"))), \
         patch.object(vt, "render_thumbnail_jpeg", render):
        result = await vt.compose_video_thumbnail(
            task_id="t", pool=object(), site_config=_SC(), out_path=str(out),
        )
    assert result.path == str(out) and out.read_bytes() == b"\xff\xd8jpeg"
    assert result.hook == "" and result.background == "brand"
    assert render.await_args.kwargs["hook"] == ""


# ---------------------------------------------------------------------------
# durable storage — a thumbnail follows its video
# ---------------------------------------------------------------------------


def _pool(existing: int):
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=existing)
    conn.execute = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


@pytest.mark.asyncio
@pytest.mark.parametrize(("existing", "video_now", "stored", "deleted"), [
    (0, False, True, False),   # first thumbnail for this task
    (1, True, True, True),     # a re-render replaces the old one
    (1, False, False, False),  # replay without its video: keep what may be uploaded
])
async def test_a_thumbnail_follows_its_video(tmp_path, existing, video_now, stored, deleted):
    from PIL import Image

    src = tmp_path / "tmp.jpg"
    Image.new("RGB", (64, 36)).save(src, format="JPEG")
    pool, conn = _pool(existing)
    record = AsyncMock(return_value="asset-1")
    with patch("poindexter.services.media_asset_recorder.record_media_asset", record):
        got = await vt.store_thumbnail_asset(
            pool, task_id="t1", src_path=str(src), meta={"hook": "h"}, post_id=None,
            video_recorded_now=video_now, video_dir=tmp_path / "video",
        )
    assert (got == "asset-1") is stored
    assert any("DELETE FROM media_assets" in c.args[0] for c in conn.execute.await_args_list) is deleted
    if stored:
        kw = record.await_args.kwargs
        assert kw["asset_type"] == "video_thumbnail" and kw["mime_type"] == "image/jpeg"
        assert kw["metadata"] == {"hook": "h"} and (kw["width"], kw["height"]) == (64, 36)
        assert os.path.exists(tmp_path / "video" / "t1_thumbnail.jpg")
