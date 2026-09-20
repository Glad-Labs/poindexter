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


@dataclass
class ShotQAResult:
    """Outcome of scoring one rendered shot frame.

    ``score`` is 0-100; ``None`` means the frame could not be scored
    (no model / call failed / unparseable / unreadable frame) — callers
    accept the shot rather than penalising it.
    """

    score: float | None
    reason: str = ""


async def _extract_video_frame(video_path: str) -> str | None:
    """Pull a representative (≈1s-in) still from a video clip via ffmpeg.

    Returns the PNG path, or ``None`` on failure. Vision models score
    images, not clips, so wan21/generative shots need a frame pulled first.
    ffmpeg is baked into the worker image (#1449).
    """
    out = os.path.join(
        tempfile.gettempdir(), f"shotqa_{os.path.basename(video_path)}.png",
    )
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
    return ShotQAResult(score=float(raw), reason=str(parsed.get("reason", ""))[:200])


async def score_shot_frame(
    *,
    frame_path: str,
    shot: Shot,
    site_config: Any,
    pool: Any = None,
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

    full = await _score_image(
        image_path, prompt=prompt, model=model, pool=pool, shot_idx=shot.idx,
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
        crop_path, prompt=prompt, model=model, pool=pool, shot_idx=shot.idx,
    )
    _remove_quietly(crop_path)
    if close.score is None:
        return full
    # Worst view wins. A defect is a defect wherever it is visible, and the
    # two views are complementary: the crop sees fine detail the full frame
    # cannot resolve, the full frame sees composition the crop cuts away.
    # Measured false-positive rate of the crop pass on 7 known-clean frames:
    # zero — every one scored 95.0 sd 0.0 cropped, same as uncropped.
    return close if close.score < full.score else full


async def _score_image(
    image_path: str, *, prompt: str, model: str, pool: Any, shot_idx: int,
) -> ShotQAResult:
    """One vision call over one image. Fail-soft to ``score=None``."""
    try:
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_QA] frame read failed for %s: %s", image_path, exc)
        return ShotQAResult(score=None, reason="frame read failed")

    from poindexter.services.llm_providers.dispatcher import dispatch_complete

    # OpenAI-multimodal message; LiteLLM translates image_url data URIs into
    # Ollama's native images array. Frames are PNG (image_gen still, or an
    # extracted video frame).
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }]
    try:
        completion = await dispatch_complete(
            pool, messages, model,
            tier="standard", phase="qa_shot_vision",
            temperature=0.2, max_tokens=1024, timeout_s=150.0,
        )
        text = (getattr(completion, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_QA] vision call failed for shot %d (non-critical): %s",
            shot_idx, exc,
        )
        return ShotQAResult(score=None, reason="vision call failed")

    if not text:
        return ShotQAResult(score=None, reason="empty vision response")
    return _parse_score(text)


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


__all__ = ["ShotQAResult", "score_shot_frame", "_extract_video_frame", "_crop_frame"]
