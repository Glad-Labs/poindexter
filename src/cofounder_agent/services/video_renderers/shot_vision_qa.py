"""Per-shot vision-QA frame scorer (video-quality Piece 2, spec §3.2).

Substrate twin of the blog-image vision gate (``MultiModelQA._check_image_relevance``).
It reuses the SAME vision model (``qa_vision_model``, default qwen3-vl:30b) and the
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

from schemas.video_shot_list import Shot

logger = logging.getLogger(__name__)

_DEFAULT_OLLAMA_URL = "http://host.docker.internal:11434"
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
    http_client_factory: Any = None,
) -> ShotQAResult:
    """Score one rendered shot frame 0-100 with the vision model.

    Returns ``ShotQAResult(score=None)`` on any failure (fail-soft).
    """
    if site_config is None:
        return ShotQAResult(score=None, reason="no site_config")
    model = (
        (site_config.get("qa_vision_model", "") or "").strip().removeprefix("ollama/")
    )
    if not model:
        logger.debug("[SHOT_QA] qa_vision_model not set — shot QA skipped")
        return ShotQAResult(score=None, reason="no vision model configured")

    image_path = await _ensure_image_frame(frame_path)
    if not image_path:
        return ShotQAResult(score=None, reason="no scoreable frame")

    try:
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_QA] frame read failed for %s: %s", image_path, exc)
        return ShotQAResult(score=None, reason="frame read failed")

    from services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        "qa.video_shot_quality",
        intent=shot.intent,
        visual=(shot.prompt or shot.query or ""),
        source=shot.source,
    )
    base = site_config.get("ollama_base_url", _DEFAULT_OLLAMA_URL)
    url = f"{str(base).rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "options": {"temperature": 0.2, "num_predict": 200},
    }

    import httpx

    if http_client_factory is None:
        http_client_factory = httpx.AsyncClient
    timeout = httpx.Timeout(120.0, connect=5.0)
    try:
        async with http_client_factory(timeout=timeout) as client:
            resp = await asyncio.wait_for(
                client.post(url, json=payload, timeout=timeout), timeout=150,
            )
        if resp.status_code != 200:
            logger.warning(
                "[SHOT_QA] ollama HTTP %d for shot %d", resp.status_code, shot.idx,
            )
            return ShotQAResult(score=None, reason=f"ollama http {resp.status_code}")
        text = resp.json().get("message", {}).get("content", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_QA] vision call failed for shot %d (non-critical): %s",
            shot.idx, exc,
        )
        return ShotQAResult(score=None, reason="vision call failed")

    if not text:
        return ShotQAResult(score=None, reason="empty vision response")
    return _parse_score(text)


__all__ = ["ShotQAResult", "score_shot_frame", "_extract_video_frame"]
