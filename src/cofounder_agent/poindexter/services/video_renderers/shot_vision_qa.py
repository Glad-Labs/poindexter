"""Per-shot vision-QA frame scorer (video-quality Piece 2, spec §3.2).

Substrate twin of the blog-image vision gate (``MultiModelQA._check_image_relevance``).
It reuses the SAME vision model (``qa_vision_model``, default qwen3-vl:30b-a3b-instruct) and the
same Ollama ``/api/chat`` images shape, but scores a SINGLE rendered shot frame
against its ``Shot`` instead of inline blog-image URLs. Lives in ``services/``
(not ``modules/content``) so ``shot_list_renderer`` can call it without crossing
the module-purity boundary — the only shared surface is the prompt-manager key.

Fail-soft (spec §6): any miss — no model configured, call error, unparseable
response, unreadable frame — returns ``ShotQAResult(score=None)``. The caller
treats ``None`` as "could not score, accept the shot" so vision-QA infra being
down never blocks a render.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any

from poindexter.schemas.video_shot_list import Shot

logger = logging.getLogger(__name__)

_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv")

# Stock-footage fit (2026-09-24). The general shot judge compared a stock clip
# with its own SEARCH WORDS, so footage matching the words passed whatever it
# showed: blockchain node logs for "code scrolling", street bokeh for "blurred
# lights", a mostly black glitch clip for "screen noise", all 92. The stock
# judge sees the video's topic and the narration under the shot instead.
# Calibrated on those three plus a clip that fits (server racks under a line
# about clusters): its LABELS were right 4/4 while its numbers were not ("loose"
# came back 65, over the 60 threshold), so the label decides the ceiling.
_STOCK_FIT_CAPS: dict[str, float] = {"off": 20.0, "loose": 45.0}
_STOCK_FRAMES_DEFAULT = 3
# A frame this dark and this flat is black or blank; no model call needed.
_BLANK_MEAN_LUMA = 10.0
_BLANK_LUMA_STDDEV = 6.0

# The final frame of an AI clip (2026-09-25). The compositor holds a clip's
# last frame for the rest of its scene (a ~5 s hero in an ~11 s scene holds it
# ~6 s), so the frame on screen longest is the one a 1 s sample never sees. On
# f555bedc a hero panned until its subject sat cut off at the bottom of an
# empty frame, and another grew a large pseudo-text banner mid-animation; both
# were held for seconds. Stills have no final frame, and stock has its own
# judge that samples across the played part. Presenters are left out on
# purpose: they cannot be re-rolled, so a failing verdict would drop the face
# and its lip-synced line for the previous shot, which is worse than a short
# hold (51 talking-head clips all ended with 0.87-1.23 of their opening's
# detail).
_FINAL_FRAME_SOURCES = frozenset({"generative", "wan21"})
# Same lesson as _STOCK_FIT_CAPS: the garbled banner scored 65 (over the 60
# threshold) while the judge's own reason named "garbled text", so the label
# caps the number. Read from the FULL frame only: the 2x crop magnifies small
# marks on an object into what the judge then calls large lettering.
_GARBLED_TEXT_CAP_DEFAULT = 45.0
_TEXT_LABELS = frozenset({"none", "readable", "garbled"})
# Detail collapse, the empty-frame case the judge passes (the cloud frame came
# back 87 and 92). Final-frame edge density over the 1 s frame's, measured on
# 145 hero clips (30 ComfyUI Wan 2.2, 115 wan21): the ComfyUI endings that
# collapsed to a near-empty frame read 0.10-0.17, the wan21 endings that went
# black, grey, blown out or faded 0.08-0.24, and the lowest acceptable
# ending 0.38.
# 51 talking heads read 0.87-1.23. See docs/architecture/video-composition.md.
_COLLAPSE_RATIO_DEFAULT = 0.30
_COLLAPSE_SCORE_DEFAULT = 30.0
# Below this the 1 s frame has no detail to lose (a deliberately minimal
# shot), and a ratio of two near-zero numbers means nothing.
_COLLAPSE_MIN_OPENING_EDGE = 1.0
_EDGE_SAMPLE_WIDTH = 640


@dataclass
class ShotQAResult:
    """Outcome of scoring one rendered shot frame.

    ``score`` is 0-100; ``None`` means the frame could not be scored
    (no model / call failed / unparseable / unreadable frame) — callers
    accept the shot rather than penalising it.
    """

    score: float | None
    reason: str = ""
    # Stock-footage judge only: "fits" | "loose" | "off" (empty elsewhere).
    fit: str = ""
    # AI-shot judge only: "none" | "readable" | "garbled" (empty elsewhere).
    text: str = ""
    # Which frame decided the score: "opening" (≈1 s in, or the still itself)
    # or "final" (the clip's last frame, the one the compositor holds).
    frame: str = ""
    # The opening frame's own verdict, kept when the final frame decides, so
    # the renderer can tell "the whole clip is off" from "it went wrong at the
    # end" (a hero that only went wrong at the end can fall back to its still).
    opening_score: float | None = None
    # True when the score is the detail-collapse rule's, not the judge's: the
    # clip ended on a near-empty frame. Usually the camera left its subject
    # because the shot's motion asked it to, so the repair pass re-rolls it
    # with a held camera instead (``shot_list_renderer._candidate_shot``).
    detail_collapse: bool = False


async def _extract_video_frame(video_path: str) -> str | None:
    """Pull a representative (≈1s-in) still from a video clip via ffmpeg.

    Returns the PNG path, or ``None`` on failure. Vision models score
    images, not clips, so wan21/generative shots need a frame pulled first.
    ffmpeg is baked into the worker image (#1449).
    """
    out = os.path.join(
        tempfile.gettempdir(), f"shotqa_{os.path.basename(video_path)}.png",
    )
    # The name is keyed on the basename, and every render names its clips
    # shot_NN.mp4: a failed extract must not leave the previous render's frame
    # in place to be judged (the final-frame pass compares against this one).
    _remove_quietly(out)
    # -ss before -i seeks fast; grab one frame ~1s in (covers the open of
    # short clips without needing to probe the duration first).
    cmd = ["ffmpeg", "-y", "-ss", "1", "-i", video_path, "-frames:v", "1", out]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_QA] ffmpeg frame extract raised for %s: %s", video_path, exc,
        )
        return None
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    return None


async def _final_frame(video_path: str) -> str | None:
    """The clip's very last frame as a PNG, or ``None``.

    This is the frame the compositor holds when the clip is shorter than its
    scene (``shot_list_renderer._scenes_for_plan``), extracted the same way
    ``_last_frame_still`` extracts it for the continuation scene: ``-sseof``
    lands near the end and ``-update 1`` rewrites one file per decoded frame,
    so what is left on disk is the last one.
    """
    out = os.path.join(
        tempfile.gettempdir(), f"shotqa_final_{os.path.basename(video_path)}.png",
    )
    _remove_quietly(out)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.5", "-i", video_path,
        "-update", "1", out,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_QA] final-frame extract raised for %s: %s", video_path, exc,
        )
        return None
    if proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    return None


async def _crop_frame(
    image_path: str, *, fraction: float, zoom: float,
) -> str | None:
    """Centre crop of ``fraction`` of each edge, upscaled ``zoom``x.

    This exists because the judge is blind to fine detail at full frame.
    Measured 2026-09-20 on a matched pair differing ONLY in text integrity
    (identical subject, identical prompt): at native 832x480 the garbled frame
    scored 85.0 sd 0.0 — indistinguishable from clean, and the model did not
    abstain but CONFABULATED, reporting "every line contains readable English
    words" and quoting a log line that is not in the image. On a 2x centre crop
    the same model correctly answered "some of the text is garbled".
    Signal/noise over the pair went 0.71 -> 7.02.

    Upscaling the WHOLE frame does not help (85.0 either way), so the lever is
    the defect's share of the frame — qwen3-vl's fixed attention budget — not
    absolute pixels. Nothing in our code downsamples; the loss is inside the
    model's own preprocessing.
    """
    out = os.path.join(
        tempfile.gettempdir(),
        f"shotqa_crop_{os.path.basename(image_path)}",
    )
    if not out.lower().endswith(".png"):
        out += ".png"
    # iw*f centred, then scale by zoom. -2 keeps the height even for any codec.
    vf = (
        f"crop=iw*{fraction:.3f}:ih*{fraction:.3f},"
        f"scale=iw*{zoom:.2f}:-2:flags=lanczos"
    )
    cmd = ["ffmpeg", "-y", "-i", image_path, "-vf", vf, "-frames:v", "1", out]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_QA] crop raised for %s: %s", image_path, exc)
        return None
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    return None


async def _ensure_image_frame(frame_path: str) -> str | None:
    """Return an image path to score: passthrough for stills, extract for video."""
    if frame_path.lower().endswith(_VIDEO_EXTS):
        return await _extract_video_frame(frame_path)
    if os.path.exists(frame_path) and os.path.getsize(frame_path) > 0:
        return frame_path
    return None


def _parse_score(text: str) -> ShotQAResult:
    """Parse ``{"score": int, "reason": str}`` from a (possibly fenced) response."""
    json_text = text
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            json_text = m.group(1)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\"score\".*?\}", text, re.DOTALL)
        if not m:
            return ShotQAResult(score=None, reason="unparseable vision response")
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return ShotQAResult(score=None, reason="unparseable vision response")
    raw = parsed.get("score")
    if not isinstance(raw, (int, float)):
        return ShotQAResult(score=None, reason="vision response missing numeric score")
    text = str(parsed.get("text", "") or "").strip().lower()
    return ShotQAResult(
        score=float(raw),
        reason=str(parsed.get("reason", ""))[:200],
        fit=str(parsed.get("fit", "") or "").strip().lower(),
        # An unknown label is no label: it must never cap a score.
        text=text if text in _TEXT_LABELS else "",
    )


async def score_shot_frame(
    *,
    frame_path: str,
    shot: Shot,
    site_config: Any,
    pool: Any = None,
    topic: str = "",
    narration: str = "",
) -> ShotQAResult:
    """Score one rendered shot frame 0-100 with the vision model.

    Routes through the LiteLLM dispatcher (``dispatch_complete``) — the same
    path every other LLM call takes — so the shot-QA vision call lands in
    ``cost_logs`` + Langfuse and picks up the per-model ``api_base`` override
    (the GPU-pinned instance) for free. qwen3-vl emits a long ``<think>``
    trace even when nothing asks it not to, and that trace shares the
    ``max_tokens`` budget with the JSON answer — the same failure
    ``multi_model_qa._check_image_relevance`` already hit and fixed by
    raising its budget 400->1024 (poindexter#563); ``max_tokens`` below
    mirrors that fix so thinking doesn't starve the score out of the
    response. Requires a ``pool``; without one (legacy/test callers) it
    fail-softs to no-score.

    Returns ``ShotQAResult(score=None)`` on any failure (fail-soft).
    """
    if site_config is None:
        return ShotQAResult(score=None, reason="no site_config")
    if pool is None:
        logger.debug("[SHOT_QA] no pool — shot QA skipped (cannot dispatch)")
        return ShotQAResult(score=None, reason="no pool for dispatch")
    model = (site_config.get("qa_vision_model", "") or "").strip()
    if not model:
        logger.debug("[SHOT_QA] qa_vision_model not set — shot QA skipped")
        return ShotQAResult(score=None, reason="no vision model configured")

    if shot.source == "pexels":
        # Stock is judged on FIT to this video, not on how well it matches the
        # words it was found by. See ``_STOCK_FIT_CAPS``.
        return await _score_stock(
            frame_path=frame_path, shot=shot, site_config=site_config,
            pool=pool, model=model, topic=topic, narration=narration,
        )

    image_path = await _ensure_image_frame(frame_path)
    if not image_path:
        return ShotQAResult(score=None, reason="no scoreable frame")

    from poindexter.services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        "qa.video_shot_quality",
        intent=shot.intent,
        visual=(shot.prompt or shot.query or ""),
        source=shot.source,
    )

    opening = await _score_views(
        image_path, prompt=prompt, model=model, pool=pool, shot_idx=shot.idx,
        site_config=site_config,
    )
    opening.frame, opening.opening_score = "opening", opening.score
    if opening.score is None or not _judges_final_frame(frame_path, shot, site_config):
        # No final frame to judge (a still), or an infra miss that a second
        # frame would only repeat.
        return opening
    final = await _score_final_frame(
        frame_path, opening_image=image_path, prompt=prompt, model=model,
        pool=pool, shot_idx=shot.idx, site_config=site_config,
    )
    # Worst frame wins, as with the two views of one frame: the viewer sees
    # both, and the final one for longest.
    if final.score is None or final.score >= opening.score:
        return opening
    final.opening_score = opening.score
    return final


def _judges_final_frame(frame_path: str, shot: Shot, site_config: Any) -> bool:
    """True for an AI video clip, whose last frame the compositor may hold."""
    return (
        shot.source in _FINAL_FRAME_SOURCES
        and frame_path.lower().endswith(_VIDEO_EXTS)
        and _sc_bool(site_config, "video_shot_qa_final_frame_enabled", True)
    )


async def _score_views(
    image_path: str,
    *,
    prompt: str,
    model: str,
    pool: Any,
    shot_idx: int,
    site_config: Any,
) -> ShotQAResult:
    """Judge one frame twice, whole and as a 2x centre crop; worst view wins."""
    full = _cap_garbled_text(
        await _score_image(
            image_path, prompt=prompt, model=model, pool=pool, shot_idx=shot_idx,
        ),
        site_config,
    )
    if not _sc_bool(site_config, "video_shot_qa_crop_enabled", True):
        return full
    if full.score is None:
        return full  # infra miss — a second call would just fail the same way

    fraction = _sc_float(site_config, "video_shot_qa_crop_fraction", 0.62)
    zoom = _sc_float(site_config, "video_shot_qa_crop_zoom", 2.0)
    crop_path = await _crop_frame(image_path, fraction=fraction, zoom=zoom)
    if not crop_path:
        return full  # fail-soft: the full-frame verdict still stands

    close = await _score_image(
        crop_path, prompt=prompt, model=model, pool=pool, shot_idx=shot_idx,
    )
    _remove_quietly(crop_path)
    if close.score is None:
        return full
    # Worst view wins. A defect is a defect wherever it is visible, and the
    # two views are complementary: the crop sees fine detail the full frame
    # cannot resolve, the full frame sees composition the crop cuts away.
    # Measured false-positive rate of the crop pass on 7 known-clean frames:
    # zero — every one scored 95.0 sd 0.0 cropped, same as uncropped.
    worst = close if close.score < full.score else full
    # The text label is the full frame's in either case. The crop's own label
    # is not a viewer's view: it calls a row of small marks on an object
    # "large lettering" once they fill a 2x crop.
    worst.text = full.text
    return worst


def _cap_garbled_text(result: ShotQAResult, site_config: Any) -> ShotQAResult:
    """Cap a full-frame score the judge labelled ``garbled`` under the threshold.

    The number alone does not carry it: the garbled banner came back 65 on
    every full-frame call, over the 60 threshold, with "garbled text" in the
    reason each time. See ``_GARBLED_TEXT_CAP_DEFAULT``.
    """
    if result.score is None or result.text != "garbled":
        return result
    cap = _sc_float(site_config, "video_shot_qa_garbled_text_cap", _GARBLED_TEXT_CAP_DEFAULT)
    if result.score <= cap:
        return result
    return ShotQAResult(
        score=cap, reason=f"garbled text: {result.reason}"[:200], text=result.text,
    )


async def _score_final_frame(
    clip_path: str,
    *,
    opening_image: str,
    prompt: str,
    model: str,
    pool: Any,
    shot_idx: int,
    site_config: Any,
) -> ShotQAResult:
    """Judge the clip's last frame: detail collapse first (no model call), then
    the same two views as the opening frame."""
    last = await _final_frame(clip_path)
    if not last:
        return ShotQAResult(score=None, reason="no final frame")
    ratio = _sc_float(
        site_config, "video_shot_qa_detail_collapse_ratio", _COLLAPSE_RATIO_DEFAULT,
    )
    collapse = _detail_collapse(
        opening_image, last, ratio=ratio,
        min_opening=_sc_float(
            site_config, "video_shot_qa_detail_collapse_min_opening_edge",
            _COLLAPSE_MIN_OPENING_EDGE,
        ),
    )
    if collapse is not None:
        opening_edge, final_edge = collapse
        return ShotQAResult(
            score=_sc_float(
                site_config, "video_shot_qa_detail_collapse_score", _COLLAPSE_SCORE_DEFAULT,
            ),
            reason=(
                f"final frame lost its detail: edge density {final_edge:.2f} "
                f"against {opening_edge:.2f} at the opening (under {ratio:.2f}x)"
            ),
            frame="final",
            detail_collapse=True,
        )
    result = await _score_views(
        last, prompt=prompt, model=model, pool=pool, shot_idx=shot_idx,
        site_config=site_config,
    )
    if result.score is not None:
        result.reason = f"final frame: {result.reason}"[:200]
    result.frame = "final"
    return result


def _edge_density(image_path: str) -> float | None:
    """Mean edge strength of the central 80% of a frame, grayscale, at a fixed
    width so clips of different sizes measure alike. ``None`` if unreadable."""
    try:
        from PIL import Image, ImageFilter, ImageStat

        with Image.open(image_path) as img:
            gray = img.convert("L")
            w, h = gray.size
            centre = gray.crop((w // 10, h // 10, w - w // 10, h - h // 10))
            cw, ch = centre.size
            if cw <= 0 or ch <= 0:
                return None
            sized = centre.resize(
                (_EDGE_SAMPLE_WIDTH, max(3, round(ch * _EDGE_SAMPLE_WIDTH / cw))),
                Image.Resampling.LANCZOS,
            )
            edges = sized.filter(ImageFilter.FIND_EDGES)
            # FIND_EDGES leaves its 1 px border at the raw pixel values, so a
            # flat frame would read ~1% of its brightness as "edges" (a blank
            # white frame measured 2.4). Only the convolved interior counts.
            ew, eh = edges.size
            inner = edges.crop((1, 1, ew - 1, eh - 1))
            return float(ImageStat.Stat(inner).mean[0])
    except Exception:  # noqa: BLE001  # silent-ok: an unreadable frame is not proof of collapse
        return None


def _detail_collapse(
    opening_path: str,
    final_path: str,
    *,
    ratio: float,
    min_opening: float = _COLLAPSE_MIN_OPENING_EDGE,
) -> tuple[float, float] | None:
    """``(opening, final)`` edge densities when the final frame kept less than
    ``ratio`` of the opening's detail, else ``None``.

    The judge scores an empty frame as a fine, calm composition: the cloud
    that panned out of shot came back 87 and 92. Edge density sees it at once
    (1.08 against 10.66 at 1 s), and a clip that is sparse by design is sparse
    in both frames, so the ratio does not punish it.
    """
    opening = _edge_density(opening_path)
    final = _edge_density(final_path)
    if opening is None or final is None or opening <= 0 or opening < min_opening:
        return None
    if final / opening < ratio:
        return opening, final
    return None


async def _probe_seconds(path: str) -> float | None:
    """Clip duration via ffprobe, or ``None``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        value = float(out.decode().strip())
        return value if value > 0 else None
    except Exception:  # noqa: BLE001  # silent-ok: fall back to a single frame
        return None


async def _frame_at(video_path: str, at_s: float, tag: str) -> str | None:
    """One PNG frame ``at_s`` seconds into ``video_path``, or ``None``."""
    out = os.path.join(
        tempfile.gettempdir(), f"shotqa_{tag}_{os.path.basename(video_path)}.png",
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at_s:.2f}", "-i",
           video_path, "-frames:v", "1", out]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_QA] frame extract raised for %s: %s", video_path, exc)
        return None
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    return None


def _is_blank(image_path: str) -> bool:
    """Black or blank: dark AND flat over the central 80% of the frame."""
    try:
        from PIL import Image, ImageStat

        with Image.open(image_path) as img:
            gray = img.convert("L")
            w, h = gray.size
            stat = ImageStat.Stat(gray.crop((w // 10, h // 10, w - w // 10, h - h // 10)))
            return stat.mean[0] < _BLANK_MEAN_LUMA and stat.stddev[0] < _BLANK_LUMA_STDDEV
    except Exception:  # noqa: BLE001  # silent-ok: an unreadable frame is not proof of black
        return False


async def _score_stock(
    *,
    frame_path: str,
    shot: Shot,
    site_config: Any,
    pool: Any,
    model: str,
    topic: str,
    narration: str,
) -> ShotQAResult:
    """Judge a stock clip's FIT to this video over several frames; worst wins.

    One frame ~1 s in is how the mostly black glitch clip passed: the frames
    are sampled evenly across the part of the clip that will actually play
    (``video_shot_qa_stock_frames``, default 3). A black or blank frame scores
    0 without a model call. A judged frame labelled ``loose`` or ``off`` is
    capped under the escalation threshold whatever number came with it, so
    ``_escalate_offtopic_stock`` re-queries it and then swaps in a still.
    """
    from poindexter.services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        "qa.video_stock_fit",
        topic=topic or "(not given)",
        narration=narration or "(not given)",
        intent=shot.intent,
        visual=(shot.query or shot.prompt or ""),
    )
    frames: list[tuple[float | None, str]] = []
    if frame_path.lower().endswith(_VIDEO_EXTS):
        n = max(1, int(_sc_float(site_config, "video_shot_qa_stock_frames", _STOCK_FRAMES_DEFAULT)))
        seconds = await _probe_seconds(frame_path)
        used = min(seconds, float(shot.duration_s)) if seconds else None
        if used:
            for k in range(n):
                at_s = used * (k + 0.5) / n
                img = await _frame_at(frame_path, at_s, f"s{k}")
                if img:
                    frames.append((at_s, img))
        else:
            img = await _extract_video_frame(frame_path)
            if img:
                frames.append((1.0, img))
    else:
        img = await _ensure_image_frame(frame_path)
        if img:
            frames.append((None, img))
    if not frames:
        return ShotQAResult(score=None, reason="no scoreable frame")

    worst: ShotQAResult | None = None
    for at, img in frames:
        where = f" at {at:.1f}s" if at is not None else ""
        if _is_blank(img):
            result = ShotQAResult(score=0.0, reason=f"black or blank frame{where}", fit="off")
        else:
            result = await _score_image(img, prompt=prompt, model=model, pool=pool, shot_idx=shot.idx)
            if result.score is None:
                continue
            cap = _STOCK_FIT_CAPS.get(result.fit)
            if cap is not None and result.score > cap:
                result = ShotQAResult(
                    score=cap, reason=f"{result.fit}{where}: {result.reason}"[:200], fit=result.fit,
                )
        if worst is None or (result.score or 0.0) < (worst.score or 0.0):
            worst = result
    return worst or ShotQAResult(score=None, reason="stock frames unscoreable")


async def _vision_text(
    image_path: str, *, prompt: str, model: str, pool: Any, shot_idx: int,
) -> tuple[str | None, str]:
    """One vision call over one image: ``(reply, "")`` or ``(None, why)``."""
    try:
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_QA] frame read failed for %s: %s", image_path, exc)
        return None, "frame read failed"

    from poindexter.services.llm_providers.dispatcher import dispatch_complete

    # OpenAI-multimodal message; LiteLLM translates image_url data URIs into
    # Ollama's native images array. Frames are PNG (image_gen still, or an
    # extracted video frame). dispatch_complete's messages type is the plain
    # str-content shape; the multimodal content-list shape is the same one
    # image_fanout/media_quality_service/image_captioner already ship.
    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }]
    try:
        completion = await dispatch_complete(
            pool, messages, model,  # type: ignore[arg-type]  # multimodal
            # content list — same shape image_fanout.py ships (its call site
            # carries the identical ignore for the identical reason).
            tier="standard", phase="qa_shot_vision",
            temperature=0.2, max_tokens=1024, timeout_s=150.0,
        )
        text = (getattr(completion, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_QA] vision call failed for shot %d (non-critical): %s",
            shot_idx, exc,
        )
        return None, "vision call failed"
    if not text:
        return None, "empty vision response"
    return text, ""


async def _score_image(
    image_path: str, *, prompt: str, model: str, pool: Any, shot_idx: int,
) -> ShotQAResult:
    """One vision call over one image. Fail-soft to ``score=None``."""
    text, why = await _vision_text(
        image_path, prompt=prompt, model=model, pool=pool, shot_idx=shot_idx,
    )
    if text is None:
        return ShotQAResult(score=None, reason=why)
    return _parse_score(text)


@dataclass(frozen=True)
class TalkingClipVerdict:
    """Is a rendered talking clip free of the tells that read as AI slop?

    ``ok=None``: could not tell. ``problems`` counts the failed checks (hands,
    mouth, face, strangers), so two takes of one shot compare on it.
    """

    ok: bool | None
    problems: int = 0
    reason: str = ""


def _parse_talking_clip(text: str) -> TalkingClipVerdict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return TalkingClipVerdict(ok=None, reason="unparseable vision response")
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return TalkingClipVerdict(ok=None, reason="unparseable vision response")
    checks = [parsed.get(k) for k in ("hands_ok", "mouth_ok", "face_ok")]
    strangers = parsed.get("strangers")
    if any(not isinstance(c, bool) for c in checks) or not isinstance(strangers, int) or isinstance(strangers, bool):
        return TalkingClipVerdict(ok=None, reason="vision response missing a check")
    problems = sum(1 for c in checks if c is False) + (1 if strangers > 0 else 0)
    return TalkingClipVerdict(ok=problems == 0, problems=problems, reason=str(parsed.get("reason", "") or "")[:200])


async def talking_clip_sheet(clip_path: str, frames: int, tag: str) -> str | None:
    """``frames`` moments across a clip tiled into one PNG, in order, or ``None``.

    The tells a talking clip goes wrong with (a hand that melts as it
    gestures, a mouth that stretches past the face) come and go within the
    clip, so one frame misses them; one sheet lets a single vision call see
    the whole take.
    """
    import math

    duration = await _probe_seconds(clip_path)
    if not duration:
        return None
    n = max(1, frames)
    stills = []
    for i in range(n):
        got = await _frame_at(clip_path, duration * (i + 0.5) / n, f"{tag}_{i}")
        if got:
            stills.append(got)
    if not stills:
        return None
    cols = math.ceil(math.sqrt(len(stills)))
    out = os.path.join(tempfile.gettempdir(), f"shotqa_{tag}_sheet_{os.path.basename(clip_path)}.png")
    inputs: list[str] = []
    for still in stills:
        inputs += ["-i", still]
    layout = "|".join(f"{(k % cols) * 512}_{(k // cols) * 288}" for k in range(len(stills)))
    scaled = "".join(f"[{k}:v]scale=512:288[s{k}];" for k in range(len(stills)))
    joined = "".join(f"[s{k}]" for k in range(len(stills)))
    graph = f"{scaled}{joined}xstack=inputs={len(stills)}:layout={layout}:fill=black[out]" \
        if len(stills) > 1 else "[0:v]scale=512:288[out]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]", out]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_QA] contact sheet failed for %s: %s", clip_path, exc)
        return None
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else None


async def check_talking_clip(
    *,
    clip_path: str,
    name: str,
    appearance: str,
    site_config: Any,
    pool: Any,
) -> TalkingClipVerdict:
    """Does a rendered talking clip keep its hands, mouth and face intact, alone?

    Matt on the Red Riding Hood render (2026-10-13): "the hands in the
    animations are slop" and "Red's mouth is too large at times". A talking
    frame is checked before animation (``check_talking_frame``), but these
    tells appear only once the lip-sync moves the picture, so the finished
    clip is checked too. Countable checks, not a score; fail-soft ``ok=None``.
    """
    from poindexter.services.settings_defaults import DEFAULTS

    if site_config is None or pool is None:
        return TalkingClipVerdict(ok=None, reason="no site_config or pool")
    model = (site_config.get("qa_vision_model", "") or "").strip()
    if not model:
        return TalkingClipVerdict(ok=None, reason="no vision model configured")
    raw = site_config.get("video_cast_clip_check_frames", None)
    frames = int(float(raw if raw not in (None, "") else DEFAULTS["video_cast_clip_check_frames"]))
    sheet = await talking_clip_sheet(clip_path, frames, "talkclip")
    if sheet is None:
        return TalkingClipVerdict(ok=None, reason="could not sample the clip")

    from poindexter.services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        "qa.talking_clip", name=name, appearance=appearance or name, frames=frames,
    )
    text, why = await _vision_text(sheet, prompt=prompt, model=model, pool=pool, shot_idx=-1)
    if text is None:
        return TalkingClipVerdict(ok=None, reason=why)
    return _parse_talking_clip(text)


@dataclass
class TalkingFrameVerdict:
    """Is this talking frame the speaker, alone? ``ok=None``: could not tell."""

    ok: bool | None
    people: int | None = None
    reason: str = ""


def _parse_talking_frame(text: str) -> TalkingFrameVerdict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return TalkingFrameVerdict(ok=None, reason="unparseable vision response")
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return TalkingFrameVerdict(ok=None, reason="unparseable vision response")
    people = parsed.get("people")
    if not isinstance(people, int) or isinstance(people, bool):
        return TalkingFrameVerdict(ok=None, reason="vision response missing a people count")
    is_character = parsed.get("is_character")
    reason = str(parsed.get("reason", "") or "")[:200]
    ok = people == 1 and is_character is True
    return TalkingFrameVerdict(ok=ok, people=people, reason=reason)


def _parse_cast_frame(text: str, cast_size: int) -> TalkingFrameVerdict:
    """Pass only with no strangers, no more people than the cast, and every
    required character present. A missing count is no verdict."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return TalkingFrameVerdict(ok=None, reason="unparseable vision response")
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return TalkingFrameVerdict(ok=None, reason="unparseable vision response")
    people, strangers = parsed.get("people"), parsed.get("strangers")
    for value in (people, strangers):
        if not isinstance(value, int) or isinstance(value, bool):
            return TalkingFrameVerdict(ok=None, reason="vision response missing a count")
    reason = str(parsed.get("reason", "") or "")[:200]
    ok = strangers == 0 and people <= cast_size and parsed.get("all_present") is True
    return TalkingFrameVerdict(ok=ok, people=people, reason=reason)


async def check_cast_frame(
    *,
    frame_path: str,
    required: list[tuple[str, str]],
    cast: list[tuple[str, str]],
    site_config: Any,
    pool: Any,
) -> TalkingFrameVerdict:
    """Does this still show the ``required`` characters and nobody outside ``cast``?

    Pairs are ``(name, appearance)``. The stills twin of
    ``check_talking_frame``. Re-directed with scene coverage, render
    a19363a9's closing scene drew a reaction shot with a man in a white coat
    nobody wrote (2026-10-13). The defect is a STRANGER, not another cast
    member: a reaction shot of Kai with Mira over his shoulder is coverage,
    and an exact-count rule rejected two such shots in the same run. Same
    fail-soft contract: ``ok=None`` when nothing can answer.
    """
    if not required:
        return TalkingFrameVerdict(ok=None, reason="no cast to check")
    if site_config is None or pool is None:
        return TalkingFrameVerdict(ok=None, reason="no site_config or pool")
    model = (site_config.get("qa_vision_model", "") or "").strip()
    if not model:
        return TalkingFrameVerdict(ok=None, reason="no vision model configured")

    from poindexter.services.prompt_manager import get_prompt_manager

    everyone = list(dict.fromkeys([*cast, *required]))
    prompt = get_prompt_manager().get_prompt(
        "qa.cast_frame",
        cast="\n".join(f"- {name}: {looks or name}" for name, looks in everyone),
        required=", ".join(name for name, _ in required),
    )
    text, why = await _vision_text(
        frame_path, prompt=prompt, model=model, pool=pool, shot_idx=-1,
    )
    if text is None:
        return TalkingFrameVerdict(ok=None, reason=why)
    return _parse_cast_frame(text, len(everyone))


async def check_talking_frame(
    *,
    frame_path: str,
    name: str,
    appearance: str,
    site_config: Any,
    pool: Any,
) -> TalkingFrameVerdict:
    """Does this talking frame show the speaker and nobody else?

    The lip-sync animates whatever the frame holds, for every line the speaker
    has in the scene, and a talking clip cannot be re-rolled the way a still
    can. So the frame is judged before the clip is spent on it. On render
    a19363a9 (2026-10-13) Mira's frame for the closing scene had a stranger
    beside her (a man in a black shirt lettered with nonsense), though the
    prompt asked for her alone, and he sat through every one of her lines in
    that scene.

    A count and a yes/no, not a score: the defect is countable, and a score
    would blend it with how nice the picture is. Fail-soft: no model, no pool
    or an unreadable reply returns ``ok=None``, and the caller keeps the frame.
    """
    if site_config is None or pool is None:
        return TalkingFrameVerdict(ok=None, reason="no site_config or pool")
    model = (site_config.get("qa_vision_model", "") or "").strip()
    if not model:
        return TalkingFrameVerdict(ok=None, reason="no vision model configured")

    from poindexter.services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        "qa.talking_frame", name=name, appearance=appearance or name,
    )
    text, why = await _vision_text(
        frame_path, prompt=prompt, model=model, pool=pool, shot_idx=-1,
    )
    if text is None:
        return TalkingFrameVerdict(ok=None, reason=why)
    return _parse_talking_frame(text)


def _sc_bool(site_config: Any, key: str, default: bool) -> bool:
    try:
        raw = site_config.get(key, default)
    except Exception:  # noqa: BLE001  # silent-ok: a settings read must never
        return default              # decide a render's fate.
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _sc_float(site_config: Any, key: str, default: float) -> float:
    try:
        return float(site_config.get(key, default))
    except (TypeError, ValueError, AttributeError):
        return default


def _remove_quietly(path: str) -> None:
    """Drop the temp crop. Best-effort by design: the score is already in
    hand, and a leftover file in tempdir must never fail a render."""
    try:
        os.remove(path)
    except OSError:  # silent-ok: best-effort tempfile cleanup after scoring
        pass


__all__ = [
    "ShotQAResult", "TalkingFrameVerdict", "check_cast_frame", "check_talking_frame",
    "score_shot_frame", "_extract_video_frame", "_crop_frame",
]
