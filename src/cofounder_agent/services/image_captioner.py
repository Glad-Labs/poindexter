"""Vision-based alt-text captioner — describes the ACTUAL image.

Single source of truth for image alt generation, shared by the
``stage.caption_images`` pipeline node and ``scripts/backfill-image-alt-vision.py``
(mirrors how ``services.alt_text`` is shared by the stage + the #84 backfill).

Routes the vision call through ``dispatch_complete`` (provider-swappable —
Matt's directive: everything through the dispatcher so the LLM provider can
be swapped without rewrites) using OpenAI-style image content blocks, which
the active litellm provider forwards to the local qwen3-vl Ollama model. The
raw model output is post-processed by ``alt_text.sanitize_alt_text`` so every
existing guard (token strip, mid-word truncation, SDXL-prompt-shape) still
applies. Fail-soft: returns ``None`` on any error so callers keep the prior
alt — a backfill must never blank or degrade a post.

Why vision (not text cleanup): the legacy alt text was derived from the
writer's ``[IMAGE-N: <description>]`` placeholder, i.e. the generation
*intent*. SDXL renders abstract editorial art (negative prompt: "no people,
no faces"), so the stored alt frequently describes people/scenes the image
does not contain. Only reading the actual pixels makes the alt accurate.
"""
from __future__ import annotations

import base64
import logging
import re

import httpx

from services.alt_text import sanitize_alt_text
from services.llm_providers.dispatcher import dispatch_complete
from services.llm_providers.thinking_models import strip_think_blocks

logger = logging.getLogger(__name__)

# poindexter#716 — "vision_alt_model" is seeded in settings_defaults.py; the
# empty-string sentinel here means "no model configured" (fail-soft: caption
# returns None). Code that previously relied on this constant as a module-level
# fallback now reads the settings key via site_config.get() and treats an empty
# result as "not configured" rather than falling back to a pinned name.
_DEFAULT_VISION_MODEL = ""

# qwen3-vl is a *reasoning* model: it spends tokens on internal reasoning
# before emitting the answer. A small cap (e.g. the ~120-char alt budget)
# starves the answer and the model returns an EMPTY string — which would
# fail-soft on every image and silently leave alts unchanged. The
# generation budget must be generous; the final length is enforced
# separately by ``sanitize_alt_text(budget=...)``. Verified empirically
# 2026-06-02: mt=120/1000 → empty, mt=2048 → clean caption every time.
_DEFAULT_GEN_MAX_TOKENS = 2048

# Strip a leading "image of" / "a photo of" / "picture of" — redundant for
# screen readers (they already announce "image") and weak for SEO. sanitize
# only strips "IMAGE:"/"FIGURE:" leaders, so we handle the natural-language
# prefix here before sanitization.
_IMAGE_OF_PREFIX_RE = re.compile(
    r"^(?:an?\s+)?(?:image|photo|picture|illustration|graphic)\s+(?:of|showing|depicting)\s+",
    re.IGNORECASE,
)


def _prompt(budget: int) -> str:
    return (
        "Write alt text for this image. Describe ONLY what is actually visible "
        f"— factual, concise, one sentence, under {budget} characters. Do NOT begin "
        "with 'image of' or 'photo of'. Do NOT invent details that aren't "
        "visible."
    )


async def _fetch_b64(image_url: str) -> str | None:
    """Download an image and base64-encode it. ``None`` on any failure."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            resp = await client.get(image_url)
        if resp.status_code != 200:
            logger.warning("image_captioner: GET %s -> %s", image_url, resp.status_code)
            return None
        return base64.b64encode(resp.content).decode()
    except Exception as exc:  # noqa: BLE001 — fail-soft, never raise out
        logger.warning("image_captioner: fetch failed for %s: %s", image_url, exc)
        return None


def _strip_image_of_prefix(text: str) -> str:
    out = _IMAGE_OF_PREFIX_RE.sub("", text).strip()
    if out:
        out = out[:1].upper() + out[1:]
    return out


async def caption_image(
    *,
    image_url: str | None = None,
    image_b64: str | None = None,
    topic: str,
    budget: int,
    site_config,
    pool,
    model: str | None = None,
    task_id: str | None = None,
) -> str | None:
    """Return accurate alt text for an image, or ``None`` (caller keeps prior alt).

    Provide either ``image_url`` (downloaded here) or ``image_b64`` (already
    encoded). Routes through the dispatcher with an image content block.
    """
    if image_b64 is None:
        if not image_url:
            return None
        image_b64 = await _fetch_b64(image_url)
        if image_b64 is None:
            return None

    vmodel = model or (
        site_config.get("vision_alt_model", _DEFAULT_VISION_MODEL)
        if site_config is not None
        else _DEFAULT_VISION_MODEL
    )
    if not vmodel:
        # poindexter#716 — vision_alt_model not configured; skip rather than
        # send an empty-model request that fails with an opaque Ollama error.
        logger.debug(
            "image_captioner: vision_alt_model not set; caption skipped for %s",
            image_url or "<b64>",
        )
        return None
    # Generous generation budget so qwen3-vl's reasoning doesn't starve the
    # answer (see _DEFAULT_GEN_MAX_TOKENS note). Final length is enforced by
    # sanitize_alt_text(budget=...), NOT by this cap.
    gen_max_tokens = (
        site_config.get_int("vision_alt_max_tokens", _DEFAULT_GEN_MAX_TOKENS)
        if site_config is not None
        else _DEFAULT_GEN_MAX_TOKENS
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _prompt(budget)},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ],
        }
    ]

    # GPU coordination — qwen3-vl is ~19.6 GB; serialize against SDXL/writer.
    from services.gpu_scheduler import gpu

    try:
        async with gpu.lock(
            "ollama", model=vmodel, task_id=task_id, phase="caption_image"
        ):
            result = await dispatch_complete(
                pool=pool,
                messages=messages,
                model=vmodel,
                tier="standard",
                task_id=task_id,
                phase="caption_image",
                temperature=0.2,
                max_tokens=gen_max_tokens,
            )
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("image_captioner: vision call failed: %s", exc)
        return None

    raw = (getattr(result, "text", "") or "").strip()
    # Some reasoning models wrap chain-of-thought in <think>…</think>.
    raw = strip_think_blocks(raw).strip()
    if not raw:
        return None
    raw = _strip_image_of_prefix(raw)
    if not raw:
        return None
    return sanitize_alt_text(raw, budget=budget, topic=topic)
