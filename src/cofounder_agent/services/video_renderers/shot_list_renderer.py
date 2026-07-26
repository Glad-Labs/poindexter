"""ShotListRenderer — assemble a video from a VideoShotList.

The shot list (``schemas/video_shot_list.VideoShotList``) is the seam
between the LLM director (decides what each shot should be) and this
renderer (assembles a final MP4 from per-shot clips + the podcast
narration). The shot list lives on ``posts.video_shot_list jsonb`` —
NULL means "director hasn't run, fall back to legacy slideshow".

Per-source dispatch:

- ``image_gen`` — single image-gen frame, held static for ``duration_s``
- ``image_kenburns`` — single image-gen frame with Ken Burns motion
- ``pexels`` — real Pexels stock photo via ``PexelsProvider`` (image,
  not video — pexels-video is a future provider). The director routes
  human / real-world subjects here on purpose; on any Pexels miss the
  renderer holds over the prior clip rather than image-gen-generating a
  person (AI faces/hands are the strongest "AI slop" tell). Ken Burns
  disabled (real photos with motion look fine static for shorter shots).
- ``generative`` / ``wan21`` — hero image-to-video clip via
  ``Wan21Provider`` (Wan 2.2 TI2V-5B server): renders the stylized still,
  then animates it with the shot's ``motion`` direction. Capped at
  5 seconds per the director prompt (the wan-server's 121-frame ceiling
  at 24fps; longer asks come back short and loop-fill with a jump cut).
- ``holdover`` — cross-fade transition placeholder. V1 treats this
  the same as ``image_gen`` by carrying the prior shot's prompt — a true
  cross-fade filtergraph is a follow-up.

Concat happens via ``FFmpegLocalCompositor`` (the existing
``MediaCompositor`` plugin). Each per-shot clip becomes a
``CompositionScene``; the compositor handles normalization, Ken Burns
zoompan for stills, and the narration audio mix.

Every shot's render result lands in ``audit_log`` as a
``video_shot_rendered`` event so operators can see which clips
succeeded without grepping container logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plugins.media_compositor import CompositionRequest, CompositionScene
from schemas.video_shot_list import Shot, VideoShotList
from services.video_renderers.shot_vision_qa import ShotQAResult, score_shot_frame
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# A caller-supplied per-shot progress callback: ``(step, pct) -> awaitable``.
# The Plan-4 media atom wires this to a live_activity ``media`` row so the
# console pulse shows real per-shot render progress; ``None`` (the default, and
# the legacy caller) renders silently.
ProgressCb = Callable[[str, "int | None"], Awaitable[None]]


# Hero clip-duration cap. The wan-server truncates at 121 frames (its
# _MAX_FRAMES), which is ~5s at the TI2V-5B-native 24fps — asking for more
# yields a shorter-than-scheduled clip the compositor loop-fills with a
# visible jump cut. The director prompt caps generative shots at 5s to
# match; defensive ceiling here in case the director sneaks one through.
_WAN21_MAX_DURATION_S = 5

# Hero (i2v) render geometry — Wan 2.2 TI2V-5B's documented 720P working
# range. Overridable per-install via the ``video_hero_width`` /
# ``video_hero_height`` / ``video_hero_fps`` app_settings; portrait lanes
# swap width/height. The prior defaults (832×480@16fps) were the Wan 2.1
# 1.3B native profile left behind by the Piece-4 model swap — off the 5B's
# training distribution, so clips came out soft.
_HERO_DEFAULT_WIDTH = 1280
_HERO_DEFAULT_HEIGHT = 704
_HERO_DEFAULT_FPS = 24

# Motion direction appended to the wan prompt when a (pre-motion-field,
# frozen) shot list carries no ``motion``. Overridable via
# ``video_hero_motion_default``. Deliberately generic-but-animated: the
# failure mode it guards against is the i2v model receiving only the
# still's own description and animating nothing.
_HERO_MOTION_FALLBACK = (
    "slow cinematic push-in with gentle parallax; ambient particles and "
    "light drift softly; smooth continuous motion, stable composition"
)

# Sources worth a second try on a vision-QA miss. image_gen/wan21/generative
# get a fresh random seed each call; pexels can't reseed a stock search, but
# CAN ask for the next-ranked result (see ``candidate_index`` on
# ``_render_pexels_image``) — production data showed 8/8 shot_quality_fallback
# findings in 30 days were pexels shots with zero retry path, so it's included.
_REGENERABLE_SOURCES = frozenset(
    {"image_gen", "image_kenburns", "wan21", "generative", "pexels"},
)

# Hero sources — generative image-to-video clips. The most expensive +
# failure-prone source, so the per-video count is capped (spec §3.3).
_HERO_SOURCES = frozenset({"generative", "wan21"})


@dataclass
class ShotRenderResult:
    """Outcome of rendering one shot."""

    idx: int
    source: str
    success: bool
    clip_path: str | None = None
    duration_s: float = 0.0
    error: str | None = None


@dataclass
class ShotListRenderResult:
    """Outcome of a full shot-list render."""

    success: bool
    output_path: str | None = None
    file_size_bytes: int = 0
    duration_s: float = 0.0
    shots_rendered: int = 0
    shots_total: int = 0
    shots_substituted: int = 0
    shots_carded: int = 0
    error: str | None = None


@dataclass
class _QAConfig:
    """Render-check loop tunables, read once per render off the DI seam."""

    enabled: bool
    threshold: float
    max_retries: int


@dataclass
class _ShotState:
    """Per-shot working state threaded across the render → score → repair →
    finalize passes.

    The two-pass split exists to stop the image-gen↔vision-model GPU thrash: the
    old per-shot loop ran ``render → score`` for each shot, so every vision
    call evicted image-gen and the next shot's render paid a ~133s cold reload.
    Batching all renders, then all scores, keeps each model resident for a
    whole pass. ``_ShotState`` is the mutable carrier that lets the later
    passes update a shot's best result/score without re-rendering.
    """

    shot: Shot
    result: ShotRenderResult  # best result so far (fresh render, or best regen)
    is_reused: bool  # True ⇒ holdover / pexels-miss (reused a prior clip; never scored)
    qa: ShotQAResult | None = None  # best score (None ⇒ unscored / couldn't score)
    attempts: int = 0  # regen rounds spent on this shot
    rung: str = "primary"  # fill provenance: primary | substitute | card | dropped


def _build_qa_config(site_config: Any) -> _QAConfig:
    """Read the render-check tunables off the site_config DI seam.

    ``site_config=None`` (the legacy/test path, and the captionless
    ``video_service`` caller) ⇒ disabled, so the existing render behaviour
    and its whole test suite are unaffected. Defaults mirror
    ``settings_defaults.py`` (enabled / 60 / 2).
    """
    if site_config is None:
        return _QAConfig(enabled=False, threshold=60.0, max_retries=2)
    enabled = str(
        site_config.get("video_shot_qa_enabled", "true") or "true",
    ).strip().lower() in ("true", "1", "yes")
    try:
        threshold = float(site_config.get("video_shot_qa_threshold", "60") or "60")
    except (TypeError, ValueError):
        threshold = 60.0
    try:
        max_retries = int(site_config.get("video_shot_qa_max_retries", "2") or "2")
    except (TypeError, ValueError):
        max_retries = 2
    return _QAConfig(
        enabled=enabled, threshold=threshold, max_retries=max(0, max_retries),
    )


async def _log_shot_audit(
    pool: Any,
    *,
    post_id: str,
    shot_result: ShotRenderResult,
    qa_score: float | None = None,
    qa_outcome: str | None = None,
    rung: str = "primary",
) -> None:
    """Best-effort audit_log insert for a single shot's render result.

    Operator-visible in the audit-log dashboard so the per-source
    success rate can be monitored without grepping container logs.
    Failures here MUST NOT take the render down.
    """
    if pool is None:
        return
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, 'shot_list_renderer', $2::jsonb, $3)
            """,
            "video_shot_rendered",
            json.dumps({
                "post_id": post_id,
                "shot_idx": shot_result.idx,
                "source": shot_result.source,
                "success": shot_result.success,
                "duration_s": shot_result.duration_s,
                "error": shot_result.error,
                "qa_score": qa_score,
                "qa_outcome": qa_outcome,
                "rung": rung,
            }),
            "info" if shot_result.success else "warning",
        )
    except Exception as exc:  # noqa: BLE001
        # Warn rather than emit_finding: a finding IS an audit_log row
        # (emit_finding -> audit_log_bg), so a finding about a broken audit_log
        # would vanish in the very outage it reports. Loki survives a DB outage.
        # This row is the per-source success-rate signal this helper's docstring
        # promises, so losing it silently blanks that dashboard — including the
        # warning-severity rows written for FAILED shots. Per-shot volume is
        # bounded (single-digit-to-~20 per list), so a plain warning is fine.
        logger.warning(
            "[SHOT_LIST] audit_log insert failed for shot %d (the render itself "
            "is unaffected; the per-source success-rate view will under-report): %s",
            shot_result.idx, exc,
        )


async def _render_image_gen_image(
    *,
    prompt: str,
    output_path: str,
    image_gen_url: str,
    http_client_factory: Any,
    render_timeout: int = 240,
    width: int | None = None,
    height: int | None = None,
) -> bool:
    """Render one image-gen image to disk via the image-gen server.

    Writes the PNG to a caller-supplied path. Returns True when the
    PNG is on disk. ``width``/``height`` ride the request when set (the
    hero path asks for the video-aspect frame so the i2v init image and
    its Ken-Burns fallback both match the lane); unset → the server's
    default square.
    """
    import httpx

    from services.video_service import _consume_image_gen_response

    neg = (
        "text, words, letters, watermark, face, person, hands, blurry, "
        "low quality, distorted, ugly, deformed"
    )
    body: dict[str, Any] = {
        "prompt": prompt,
        "negative_prompt": neg,
        # steps / guidance_scale omitted — the image-gen server's
        # per-model registry drives them (z_image_turbo is
        # guidance-distilled: 9 steps / CFG 0). image-gen-Turbo's
        # hardcoded 4 / 1.0 produced degraded frames. Matches the
        # inline-image path (replace_inline_images). #image-zimage-and-variety.
    }
    if width:
        body["width"] = int(width)
    if height:
        body["height"] = int(height)
    try:
        async with http_client_factory(
            timeout=httpx.Timeout(float(render_timeout), connect=5.0),
        ) as client:
            resp = await client.post(
                f"{image_gen_url}/generate",
                json=body,
                timeout=render_timeout,
            )
            got = await _consume_image_gen_response(
                resp,
                image_gen_url=image_gen_url,
                output_path=output_path,
                frame_label=f"shot image-gen {os.path.basename(output_path)}",
            )
            return got is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_LIST] image-gen render failed for %s: %s",
            os.path.basename(output_path), exc,
        )
        return False


async def _render_pexels_image(
    *,
    query: str,
    output_path: str,
    api_key: str,
    orientation: str,
    http_client_factory: Any,
    candidate_index: int = 0,
) -> bool:
    """Fetch a real stock photo from Pexels and write it to ``output_path``.

    The director routes human / real-world shots to ``source="pexels"`` on
    purpose (its HUMAN-SUBJECT POLICY — AI faces/hands are the strongest
    "AI slop" tell). This wires the EXISTING ``PexelsProvider`` so those
    shots get actual footage instead of the old behaviour, which ignored the
    configured key and image-gen-generated the human query (six-fingered hands).

    ``candidate_index`` picks WHICH ranked search result to use — 0 (default)
    is the top result, the common case. A vision-QA regen attempt passes a
    higher index so the retry is a genuinely different photo rather than
    re-fetching the same top result (Pexels search is deterministic, so a
    bare retry with ``candidate_index=0`` would just repeat the miss). Only
    fetches as many results as needed (``per_page=candidate_index+1``) so the
    common no-regen path stays a single-result query. Clamped to whatever
    Pexels actually returns — a thin result set reuses the last candidate
    rather than raising.

    Returns True only when a JPG is on disk. On any miss the caller holds
    over the prior clip rather than image-gen-faking the subject.
    """
    if not api_key or not query.strip():
        return False

    import httpx

    from services.image_providers.pexels import PexelsProvider

    try:
        results = await PexelsProvider().fetch(
            query,
            {
                "api_key": api_key,
                "orientation": orientation,
                "per_page": candidate_index + 1,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_LIST] Pexels search failed for %r: %s", query, exc)
        return False

    if not results:
        return False

    idx = min(candidate_index, len(results) - 1)
    url = results[idx].url
    try:
        async with http_client_factory(
            timeout=httpx.Timeout(30.0, connect=5.0),
        ) as client:
            resp = await client.get(url, timeout=30)
            resp.raise_for_status()
            with open(output_path, "wb") as fh:
                fh.write(resp.content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_LIST] Pexels download failed for %s: %s", url, exc)
        return False

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


async def _render_pexels_video(
    *,
    query: str,
    output_path: str,
    api_key: str,
    orientation: str,
    http_client_factory: Any,
    candidate_index: int = 0,
) -> bool:
    """Fetch a real Pexels stock VIDEO clip and write it to ``output_path``.

    Tried BEFORE the still-photo path in ``_render_one_shot`` — the director
    prompt has always told the LLM that ``source="pexels"`` shots are "stock
    video clips" / "real footage", but this renderer only ever wired up
    still photos until now. Real motion beats a static hold when Pexels
    actually has footage for the query; ``_render_pexels_image`` remains the
    fallback for queries with no video match (niche/abstract subjects are
    more likely to have a photo than a video).

    Same ``candidate_index`` regen convention as the photo path — see
    ``_render_pexels_image``. Returns True only when an MP4 is on disk;
    the compositor's existing ``-stream_loop -1`` + ``-t`` handling trims
    or loops whatever length Pexels returns to the shot's ``duration_s``,
    so no special duration-matching is needed here.
    """
    if not api_key or not query.strip():
        return False

    import httpx

    from services.image_providers.pexels_video import PexelsVideoProvider

    try:
        results = await PexelsVideoProvider().fetch(
            query,
            {
                "api_key": api_key,
                "orientation": orientation,
                "per_page": candidate_index + 1,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_LIST] Pexels video search failed for %r: %s", query, exc,
        )
        return False

    if not results:
        return False

    idx = min(candidate_index, len(results) - 1)
    url = results[idx].file_url
    try:
        async with http_client_factory(
            timeout=httpx.Timeout(30.0, connect=5.0),
        ) as client:
            resp = await client.get(url, timeout=30)
            resp.raise_for_status()
            with open(output_path, "wb") as fh:
                fh.write(resp.content)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_LIST] Pexels video download failed for %s: %s", url, exc,
        )
        return False

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def _hero_render_dims(
    orientation: str, site_config: Any,
) -> tuple[int, int, int]:
    """Resolve the hero (i2v) render geometry as ``(width, height, fps)``.

    Reads ``video_hero_width`` / ``video_hero_height`` / ``video_hero_fps``
    (defaults = Wan 2.2 TI2V-5B's documented 720P@24fps working range) and
    swaps width/height for the portrait (9:16) lane — the settings are
    authored landscape-first. Before this, portrait shorts got landscape
    832×480 hero clips letterboxed into a 1080×1920 canvas.
    """
    width = _HERO_DEFAULT_WIDTH
    height = _HERO_DEFAULT_HEIGHT
    fps = _HERO_DEFAULT_FPS
    if site_config is not None:
        width = site_config.get_int("video_hero_width", _HERO_DEFAULT_WIDTH)
        height = site_config.get_int("video_hero_height", _HERO_DEFAULT_HEIGHT)
        fps = site_config.get_int("video_hero_fps", _HERO_DEFAULT_FPS)
    if orientation == "portrait":
        width, height = height, width
    return width, height, fps


def _compose_hero_wan_prompt(
    still_prompt: str, motion: str | None, site_config: Any,
) -> str:
    """Build the i2v prompt: the still's description + explicit motion.

    The init image already fixes composition/subject/style, so the text
    prompt's job is telling the model what MOVES — re-sending only the
    still's own description (the pre-motion-field behaviour) gave it
    nothing to animate, which is exactly how hero clips came out static
    or randomly morphing. A shot without ``motion`` (every list frozen
    before the field existed) gets the configurable default direction
    (``video_hero_motion_default``).
    """
    direction = (motion or "").strip()
    if not direction:
        fallback = _HERO_MOTION_FALLBACK
        if site_config is not None:
            fallback = str(
                site_config.get("video_hero_motion_default", fallback)
                or fallback
            )
        direction = fallback
    base = (still_prompt or "").strip().rstrip(".")
    if not base:
        return direction
    return f"{base}. Camera and motion: {direction}"


async def _render_generative_clip(
    *,
    prompt: str,
    output_path: str,
    image_path: str | None,
    duration_s: int,
    site_config: Any,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
) -> tuple[bool, str]:
    """Render one hero clip to ``output_path`` via the Wan provider.

    When ``image_path`` is set it's the shot's stylized image-gen still, passed
    as the image-to-video init frame (animating the brand still keeps visual
    consistency — spec §3.3). Absent → text-to-video. Delegates to the
    existing ``Wan21Provider`` so the request body shape is the correct one
    for the wan-server. ``width``/``height``/``fps`` override the provider's
    defaults when set (the caller passes the lane-aspect hero geometry).
    Returns ``(success, reason)`` — ``reason`` is empty on
    success, else a short operator-facing string so a miss is diagnosable from
    the ``hero_render_fallback`` finding alone (the wan-server container that
    produced the miss may already be gone by the time anyone looks).
    """
    from services.video_providers.wan2_1 import Wan21Provider

    provider = Wan21Provider()
    config: dict[str, Any] = {
        "output_path": output_path,
        "duration_s": min(duration_s, _WAN21_MAX_DURATION_S),
        "image_path": image_path or "",
        "_site_config": site_config,
    }
    if width:
        config["width"] = int(width)
    if height:
        config["height"] = int(height)
    if fps:
        config["fps"] = int(fps)
    try:
        results = await provider.fetch(
            prompt,
            config,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_LIST] generative render raised for %s: %s",
            os.path.basename(output_path), exc,
        )
        return False, f"{type(exc).__name__}: {exc}"

    if not results:
        return False, "wan provider returned no result — check wan-server logs/health"
    ok = bool(results[0].file_path) and os.path.exists(results[0].file_path)  # type: ignore[arg-type]
    if not ok:
        return False, "wan provider result had no output file on disk"
    return True, ""


# Brand palette (dark-techno set, video-director SKILL.md STYLE POLICY).
_CARD_FIELD_RGB = (10, 26, 47)  # #0A1A2F deep navy
_CARD_WORDMARK_RGB = (34, 211, 238)  # #22D3EE cyan


def _load_card_font(size: int) -> Any:
    """Best-effort scalable wordmark font at ``size`` px, degrading in quality
    but never in guarantee — every branch returns a usable font and none raise.

    Tiers: (1) DejaVu Bold TrueType — crisp, present in the worker's Linux
    container; (2) ``load_default(size=…)`` — Pillow ≥10.1's scalable default,
    so the wordmark stays properly sized on hosts WITHOUT the Linux font paths
    (a Windows/macOS operator or a fresh OSS install — the earlier code fell
    straight to the ~10px bitmap default there); (3) the bare bitmap default on
    ancient Pillow. The final fallback is the last-resort floor, not the norm.
    """
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    try:
        # Pillow ≥10.1: a TrueType-backed default that honours ``size`` on any
        # platform. Older Pillow raises TypeError (no size kwarg) → bitmap floor.
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _render_brand_card(
    *, output_path: str, width: int, height: int, wordmark: str,
) -> bool:
    """Rung-3 of the fallback ladder: a guaranteed branded still card.

    Pure PIL — no network, no GPU, no ffmpeg — so a shot slot is never empty
    even in a total image-gen + Pexels outage. Returns a PNG the compositor
    consumes like any image-gen still. Two-tier: a centered wordmark when a
    font + draw succeed, else a text-less solid navy field (the draw is wrapped
    so a decoration failure never breaks the save). Returns True when a PNG is
    on disk.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (max(1, width), max(1, height)), _CARD_FIELD_RGB)
    try:
        text = (wordmark or "").strip()
        if text:
            draw = ImageDraw.Draw(img)
            font = _load_card_font(size=max(24, width // 18))
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(
                ((width - tw) / 2, (height - th) / 2),
                text, fill=_CARD_WORDMARK_RGB, font=font,
            )
    except Exception as exc:  # noqa: BLE001 — decoration must not break the floor
        # silent-ok: the wordmark is pure decoration; on any draw failure the
        # solid navy card still saves below (the never-fail floor), so the card's
        # guarantee holds and there is nothing to escalate — debug is right here.
        logger.debug(
            "[SHOT_LIST] card wordmark draw failed, using solid field: %s", exc,
        )
    try:
        img.save(output_path, format="PNG")
    except OSError as exc:
        logger.warning("[SHOT_LIST] card save failed for %s: %s", output_path, exc)
        return False
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def _card_enabled(site_config: Any) -> bool:
    """Master switch for the guaranteed-card rung (default on). Off ⇒ a shot
    with no clip drops as before (legacy behaviour, for A/B / never-card)."""
    if site_config is None:
        return True
    return str(
        site_config.get("video_fallback_card_enabled", "true") or "true",
    ).strip().lower() in ("true", "1", "yes")


def _emit_shot_fallback_finding(*, shot: Shot, post_id: str, rung: str) -> None:
    """Advisory (info) finding when a shot was filled by the fallback ladder
    rather than its primary source. Info severity ⇒ lands in audit_log (the
    Findings/Pipeline dashboards read it) but is below the router floor, so it
    does NOT page — the 'mostly cards = outage' escalation is the gate's job."""
    emit_finding(
        source="shot_list_renderer",
        kind="shot_fallback",
        title=f"shot {shot.idx} ({shot.source}) filled via {rung}",
        body=(
            f"shot {shot.idx} ({shot.source}) produced no clip from its primary "
            f"source; the fallback ladder filled the slot via '{rung}' so the "
            f"timeline stays whole. Many cards in one render signal an "
            f"image-gen/Pexels outage. Advisory only."
        ),
        severity="info",
        dedup_key=f"shot_fallback:{post_id}:{shot.idx}",
        extra={"shot_idx": shot.idx, "source": shot.source, "rung": rung},
    )


# Style-modifier prefixes the director prepends to AI-source prompts
# (video-director SKILL.md STYLE POLICY); stripped to recover the subject nouns
# for a Pexels search when an image-gen shot must fall back to footage.
_STYLE_MODIFIERS = (
    "flat vector illustration", "cinematic illustration", "isometric 3d",
    "line art", "cyberpunk neon", "glassmorphism", "low poly", "watercolor",
    "pixel art", "paper cutout",
)

# Sources whose failures may substitute to Pexels. Pexels itself is excluded —
# a missed pexels shot goes straight to the card (never image-gen a human).
_IMAGE_GEN_FAMILY = frozenset({"image_gen", "image_kenburns", "generative", "wan21"})


def _pexels_query_from_shot(shot: Shot) -> str:
    """Best-effort stock-search query for an image-gen-family shot falling back
    to Pexels. Prefers the concrete prompt subject (leading style-modifier and
    trailing palette clause stripped) over the abstract intent. The image-gen
    prompt policy forbids human nouns, so the derived subject is non-human by
    contract."""
    prompt = (shot.prompt or "").strip()
    low = prompt.lower()
    for mod in _STYLE_MODIFIERS:
        if low.startswith(mod):
            prompt = prompt[len(mod):].lstrip(" ,").strip()
            break
    subject = prompt.split(",")[0].strip() if prompt else ""
    return subject or (shot.intent or "").strip()


async def _substitute_failed_shot(
    shot: Shot,
    *,
    work_dir: Path,
    pexels_key: str,
    orientation: str,
    http_client_factory: Any,
) -> str | None:
    """Rung-2 cross-family substitute: an image-gen-family shot whose render
    hard-failed retries as REAL Pexels footage (video first, then photo) from a
    prompt/intent-derived query. Returns a clip_path or None. The reverse
    direction (pexels → image-gen) is intentionally never attempted."""
    if not pexels_key:
        return None
    query = _pexels_query_from_shot(shot)
    if not query:
        return None
    video_path = str(work_dir / f"shot_{shot.idx:02d}_sub.mp4")
    if await _render_pexels_video(
        query=query, output_path=video_path, api_key=pexels_key,
        orientation=orientation, http_client_factory=http_client_factory,
    ):
        return video_path
    photo_path = str(work_dir / f"shot_{shot.idx:02d}_sub.jpg")
    if await _render_pexels_image(
        query=query, output_path=photo_path, api_key=pexels_key,
        orientation=orientation, http_client_factory=http_client_factory,
    ):
        return photo_path
    return None


async def _backfill_pass(
    states: list[_ShotState],
    *,
    render_kwargs: dict[str, Any],
    site_config: Any,
    post_id: str,
    width: int,
    height: int,
    wordmark: str,
) -> None:
    """Fill every shot that produced no clip so the slot is never empty (never
    drop a shot). Card-only ladder in this task; the cross-family substitute
    (rung 2) is inserted here in a later change. Sets ``st.rung`` and preserves
    each shot's ``duration_s`` so the concat still sums to the plan.
    """
    card_ok = _card_enabled(site_config)
    work_dir: Path = render_kwargs["work_dir"]
    for st in states:
        if st.result.success and st.result.clip_path:
            continue  # primary / holdover / substitute already filled the slot
        if st.shot.source in _IMAGE_GEN_FAMILY:
            sub_path = await _substitute_failed_shot(
                st.shot,
                work_dir=work_dir,
                pexels_key=render_kwargs["pexels_key"],
                orientation=render_kwargs["orientation"],
                http_client_factory=render_kwargs["http_client_factory"],
            )
            if sub_path:
                st.result = ShotRenderResult(
                    idx=st.shot.idx, source=st.shot.source, success=True,
                    clip_path=sub_path, duration_s=st.shot.duration_s,
                )
                st.rung = "substitute"
                _emit_shot_fallback_finding(
                    shot=st.shot, post_id=post_id, rung="substitute",
                )
                continue
        if card_ok:
            card_path = str(work_dir / f"shot_{st.shot.idx:02d}_card.png")
            if _render_brand_card(
                output_path=card_path, width=width, height=height, wordmark=wordmark,
            ):
                st.result = ShotRenderResult(
                    idx=st.shot.idx, source=st.shot.source, success=True,
                    clip_path=card_path, duration_s=st.shot.duration_s,
                )
                st.rung = "card"
                _emit_shot_fallback_finding(shot=st.shot, post_id=post_id, rung="card")
                continue
        st.rung = "dropped"


async def _render_one_shot(
    shot: Shot,
    *,
    prior_clip: str | None,
    work_dir: Path,
    image_gen_url: str,
    site_config: Any,
    http_client_factory: Any,
    pexels_key: str = "",
    orientation: str = "landscape",
    post_id: str = "",
    attempt: int = 0,
) -> ShotRenderResult:
    """Produce a clip file for one shot.

    Returns a ``ShotRenderResult`` with ``clip_path`` set on success.
    Holdover shots reuse ``prior_clip`` (V1 simplification — a true
    cross-fade is a follow-up).

    ``attempt`` is the regen-round counter from ``_repair_pass`` (0 on the
    initial render). Only the ``pexels`` branch consumes it today (picks the
    next-ranked search result — see ``_render_pexels_image``); other sources
    already get natural diversity from a fresh random seed each call.
    """
    source = shot.source

    if source == "holdover":
        if not prior_clip:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="holdover shot at idx=0 has no prior clip to carry",
            )
        return ShotRenderResult(
            idx=shot.idx,
            source=source,
            success=True,
            clip_path=prior_clip,
            duration_s=shot.duration_s,
        )

    if source == "pexels":
        # Human / real-world subjects route here on purpose (the director's
        # HUMAN-SUBJECT POLICY) — AI faces/hands are the strongest "AI slop"
        # tell, so we fetch REAL Pexels footage instead of image-gen-generating
        # a person. On any Pexels miss we hold over the prior clip rather than
        # image-gen-faking the subject (#media-render-fixes: the short video shipped
        # a six-fingered AI human because this branch used to image-gen the query).
        query = (shot.query or shot.prompt or "").strip()
        if not query:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="pexels shot missing query and prompt",
            )

        # Try a real VIDEO clip first — genuine motion beats a static hold,
        # and the director prompt already tells the LLM "pexels" IS real
        # footage/video (see _render_pexels_video's docstring). Gated by
        # video_pexels_video_enabled (default true) so an operator can drop
        # straight to the photo path without a code change if needed.
        video_enabled = True
        if site_config is not None:
            video_enabled = str(
                site_config.get("video_pexels_video_enabled", "true") or "true",
            ).strip().lower() in ("true", "1", "yes")
        if video_enabled:
            video_clip_path = str(work_dir / f"shot_{shot.idx:02d}_pexels.mp4")
            video_ok = await _render_pexels_video(
                query=query,
                output_path=video_clip_path,
                api_key=pexels_key,
                orientation=orientation,
                http_client_factory=http_client_factory,
                candidate_index=attempt,
            )
            if video_ok:
                return ShotRenderResult(
                    idx=shot.idx,
                    source=source,
                    success=True,
                    clip_path=video_clip_path,
                    duration_s=shot.duration_s,
                )

        # No video match (or the download failed) — fall back to a real
        # STILL PHOTO, same as before this fix.
        clip_path = str(work_dir / f"shot_{shot.idx:02d}.jpg")
        ok = await _render_pexels_image(
            query=query,
            output_path=clip_path,
            api_key=pexels_key,
            orientation=orientation,
            http_client_factory=http_client_factory,
            candidate_index=attempt,
        )
        if ok:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=True,
                clip_path=clip_path,
                duration_s=shot.duration_s,
            )
        # Total Pexels miss — no video AND no photo (no key / no result /
        # download fail on both) — NEVER image-gen a human. Hold over the
        # prior clip if we have one; otherwise this shot drops out and the
        # rest of the video still renders.
        if prior_clip:
            logger.info(
                "[SHOT_LIST] pexels miss at idx=%d — holding over prior clip "
                "(no image-gen human fallback)", shot.idx,
            )
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=True,
                clip_path=prior_clip,
                duration_s=shot.duration_s,
            )
        return ShotRenderResult(
            idx=shot.idx,
            source=source,
            success=False,
            error="pexels miss at idx=0 with no prior clip to hold over",
        )

    if source in ("image_gen", "image_kenburns"):
        image_gen_prompt = shot.prompt if shot.prompt else (shot.query or "")
        if not image_gen_prompt:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error=f"{source} shot missing prompt and query",
            )
        clip_path = str(work_dir / f"shot_{shot.idx:02d}.png")
        render_timeout = (
            site_config.get_int("image_render_timeout_seconds", 240)
            if site_config is not None else 240
        )
        ok = await _render_image_gen_image(
            prompt=image_gen_prompt,
            output_path=clip_path,
            image_gen_url=image_gen_url,
            http_client_factory=http_client_factory,
            render_timeout=render_timeout,
        )
        if not ok:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="image-gen render returned no image",
            )
        return ShotRenderResult(
            idx=shot.idx,
            source=source,
            success=True,
            clip_path=clip_path,
            duration_s=shot.duration_s,
        )

    if source in ("generative", "wan21"):
        if not shot.prompt:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error=f"{source} shot missing prompt",
            )
        # Render the stylized image-gen still FIRST — it's both the image-to-video
        # init frame and the Ken-Burns fallback if the clip render misses
        # (spec §3.3). If even the still fails there's nothing to animate or
        # fall back to, so the shot hard-fails. The still is requested at the
        # lane's hero geometry so the i2v init frame needs no crop/stretch and
        # the Ken-Burns fallback fills the frame.
        hero_w, hero_h, hero_fps = _hero_render_dims(orientation, site_config)
        still_path = str(work_dir / f"shot_{shot.idx:02d}.png")
        render_timeout = (
            site_config.get_int("image_render_timeout_seconds", 240)
            if site_config is not None else 240
        )
        still_ok = await _render_image_gen_image(
            prompt=shot.prompt,
            output_path=still_path,
            image_gen_url=image_gen_url,
            http_client_factory=http_client_factory,
            render_timeout=render_timeout,
            width=hero_w,
            height=hero_h,
        )
        if not still_ok:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="generative shot: image-gen still render failed",
            )
        clip_path = str(work_dir / f"shot_{shot.idx:02d}.mp4")
        clip_ok, clip_error = await _render_generative_clip(
            prompt=_compose_hero_wan_prompt(shot.prompt, shot.motion, site_config),
            output_path=clip_path,
            image_path=still_path,
            duration_s=int(shot.duration_s),
            site_config=site_config,
            width=hero_w,
            height=hero_h,
            fps=hero_fps,
        )
        if clip_ok:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=True,
                clip_path=clip_path,
                duration_s=shot.duration_s,
            )
        # i2v miss → fall back to the still. The compositor applies Ken Burns
        # to a PNG scene automatically, so returning the still path is all it
        # takes; emit a finding so the operator sees the degrade. NOT a
        # holdover of the prior clip (spec §3.3).
        _emit_hero_fallback_finding(shot=shot, post_id=post_id, reason=clip_error)
        return ShotRenderResult(
            idx=shot.idx,
            source=source,
            success=True,
            clip_path=still_path,
            duration_s=shot.duration_s,
        )

    return ShotRenderResult(
        idx=shot.idx,
        source=source,
        success=False,
        error=f"unknown source {source!r}",
    )


async def _safe_progress(
    progress_cb: ProgressCb | None, step: str, pct: int | None
) -> None:
    """Fire a caller-supplied progress callback, swallowing any error. The
    callback is a live-activity heartbeat — an observability write that must
    never take the render down.
    """
    if progress_cb is None:
        return
    try:
        await progress_cb(step, pct)
    except Exception as exc:  # noqa: BLE001 — best-effort progress, never fatal
        # silent-ok: a progress callback is an observability heartbeat for the
        # live_activity pulse; its failure must never take the render down.
        logger.debug("[SHOT_LIST] progress_cb swallowed: %s", exc)


async def _render_pass(
    shots: list[Shot],
    *,
    render_kwargs: dict[str, Any],
    progress_cb: ProgressCb | None = None,
) -> list[_ShotState]:
    """Render every shot once, with the image model resident across the pass.

    Threads ``render_prior`` (the last successful fresh clip) so holdover and
    pexels-miss shots resolve against the prior clip exactly as the old
    sequential loop did. The key property: only ``image_gen`` / ``wan21`` shots
    touch the GPU here, and nothing scores, so image-gen loads once and stays warm
    for the whole pass instead of being evicted by a per-shot vision call.

    ``is_reused`` flags a shot whose result reused the prior clip (a holdover,
    or a pexels miss that held over) — those are never scored (the prior clip
    was already vetted) and get re-pointed to the post-QA prior in finalize.

    ``progress_cb`` (best-effort) fires ``("shot i/N", pct)`` at the top of each
    shot, where ``pct`` is honest shot POSITION (``i/total``), capped 1..99 —
    the media pulse row's real per-shot progress.
    """
    states: list[_ShotState] = []
    render_prior: str | None = None
    total = len(shots)
    for i, shot in enumerate(shots, start=1):
        pct = min(99, max(1, round(100 * i / total))) if total else None
        await _safe_progress(progress_cb, f"shot {i}/{total}", pct)
        result = await _render_one_shot(shot, prior_clip=render_prior, **render_kwargs)
        is_reused = bool(
            result.success and result.clip_path
            and result.clip_path == render_prior,
        )
        states.append(_ShotState(shot=shot, result=result, is_reused=is_reused))
        if result.success and result.clip_path:
            render_prior = result.clip_path
    return states


async def _score_pass(
    states: list[_ShotState],
    *,
    qa: _QAConfig,
    site_config: Any,
    pool: Any,
) -> None:
    """Score every fresh, non-reused clip — vision model resident across the pass.

    A ``None`` score (no model / infra down) is stored as-is; later passes treat
    it as "could not score, accept the shot" rather than penalising it. Reused
    clips (holdover / pexels-miss) and failed renders are skipped, matching the
    old per-shot early-return.
    """
    if not qa.enabled:
        return
    for st in states:
        if st.is_reused or not st.result.success or not st.result.clip_path:
            continue
        st.qa = await score_shot_frame(
            frame_path=st.result.clip_path, shot=st.shot,
            site_config=site_config, pool=pool,
        )


def _needs_repair(st: _ShotState, *, qa: _QAConfig) -> bool:
    """True ⇒ this shot is a below-threshold, stochastic source worth re-rolling.

    A ``None`` score is excluded (couldn't score ⇒ accept); pexels/holdover are
    excluded (deterministic / no asset); a shot that has used its retry budget
    is excluded.
    """
    return bool(
        st.qa is not None
        and st.qa.score is not None
        and st.qa.score < qa.threshold
        and st.shot.source in _REGENERABLE_SOURCES
        and st.attempts < qa.max_retries,
    )


async def _repair_pass(
    states: list[_ShotState],
    *,
    qa: _QAConfig,
    site_config: Any,
    render_kwargs: dict[str, Any],
    pool: Any,
) -> None:
    """Batched keep-best regeneration for the sub-threshold stochastic shots.

    Each round re-renders the WHOLE failing batch (image model resident) then
    re-scores the WHOLE batch (vision model resident), so even a multi-failure
    video swaps models a bounded number of times (≤ ``max_retries`` swaps each
    way) instead of once per failure. A shot leaves the batch the moment its
    best score clears threshold or it exhausts ``max_retries``. Keep-best:
    a candidate replaces the incumbent only when it scores strictly higher.
    """
    if not qa.enabled or qa.max_retries <= 0:
        return
    for _ in range(qa.max_retries):
        pending = [st for st in states if _needs_repair(st, qa=qa)]
        if not pending:
            break
        # Re-render the batch (image model resident — no vision call between).
        cands: list[tuple[_ShotState, ShotRenderResult]] = []
        for st in pending:
            st.attempts += 1
            cand = await _render_one_shot(
                st.shot, prior_clip=None, attempt=st.attempts, **render_kwargs,
            )
            cands.append((st, cand))
        # Re-score the batch (vision model resident), keep-best per shot.
        for st, cand in cands:
            if not (cand.success and cand.clip_path):
                continue
            cand_qa = await score_shot_frame(
                frame_path=cand.clip_path, shot=st.shot,
                site_config=site_config, pool=pool,
            )
            best = st.qa
            if (cand_qa.score is not None and best is not None
                    and best.score is not None and cand_qa.score > best.score):
                st.result, st.qa = cand, cand_qa


def _emit_fallback_finding(
    *, shot: Shot, score: float, threshold: float, post_id: str,
    title: str, body: str,
) -> None:
    """Emit the ``shot_quality_fallback`` finding (shared shape for the holdover
    and idx-0 keep-below cases)."""
    emit_finding(
        source="shot_list_renderer", kind="shot_quality_fallback",
        title=title, body=body, severity="warn",
        dedup_key=f"shot_quality_fallback:{post_id}:{shot.idx}",
        extra={"shot_idx": shot.idx, "source": shot.source,
               "score": score, "threshold": threshold},
    )


def _emit_hero_fallback_finding(*, shot: Shot, post_id: str, reason: str = "") -> None:
    """Emit the ``hero_render_fallback`` finding — a generative hero shot's
    image-to-video render produced no clip, so the renderer fell back to the
    stylized image-gen still (Ken-Burns'd by the compositor). Distinct kind from
    ``shot_quality_fallback`` so the Findings dashboard can track i2v render
    misses separately from QA-score fallbacks (spec §3.3).

    ``reason`` carries WHY the render missed (from ``_render_generative_clip``)
    so the finding alone is diagnosable — the wan-server container that
    produced the miss may already be gone (recreated / recycled) by the time
    anyone looks at logs.
    """
    body = (f"shot {shot.idx} ({shot.source}) — image-to-video render "
            f"produced no clip; used the stylized image-gen still with Ken Burns "
            f"motion instead.")
    if reason:
        body += f" reason: {reason}"
    emit_finding(
        source="shot_list_renderer", kind="hero_render_fallback",
        title=f"hero shot {shot.idx} fell back to still (Ken Burns)",
        body=body,
        severity="warn",
        dedup_key=f"hero_render_fallback:{post_id}:{shot.idx}",
        extra={"shot_idx": shot.idx, "source": shot.source, "reason": reason},
    )


async def _finalize_pass(
    states: list[_ShotState],
    *,
    qa: _QAConfig,
    pool: Any,
    post_id: str,
) -> list[ShotRenderResult]:
    """Assign per-shot outcomes, emit findings, and audit — threading the
    post-QA prior clip.

    Outcomes are preserved verbatim from the old per-shot loop:
    ``accepted`` / ``regenerated`` / ``fallback_holdover`` / ``kept_below``
    (or ``None`` when QA is off, the frame couldn't be scored, or the shot
    reused a prior clip). Holdover / pexels-miss shots are re-pointed to the
    post-QA ``final_prior`` so a below-threshold frame never propagates into a
    following holdover (the old loop got this for free by being sequential).
    """
    final_prior: str | None = None
    out: list[ShotRenderResult] = []
    unscored: list[_ShotState] = []
    for st in states:
        shot = st.shot
        result = st.result
        qa_score: float | None = None
        qa_outcome: str | None = None

        if st.rung in ("substitute", "card"):
            # Backfilled slot — result is already final, no QA verdict applies.
            qa_outcome = st.rung
        elif st.is_reused:
            # Holdover / pexels-miss → carry the post-QA prior clip. (An idx-0
            # reuse can't happen: the render pass fails it for lack of a prior.)
            if final_prior:
                result = ShotRenderResult(
                    idx=shot.idx, source=shot.source, success=True,
                    clip_path=final_prior, duration_s=shot.duration_s,
                )
            if qa.enabled:
                # Distinguish "reused a vetted prior clip, skipped by design"
                # from "couldn't score" in the audit row — a NULL qa_score
                # alone conflates the two and hid weeks of scorer no-ops.
                qa_outcome = "skipped_reused"
        elif not qa.enabled or not result.success or not result.clip_path:
            pass  # accept the render verbatim — no QA verdict to apply
        elif st.qa is None or st.qa.score is None:
            # Could not score (no model / infra down / unparseable) → accept,
            # don't penalise — but stamp the outcome + collect for the
            # vision_scorer_unavailable finding so the fail-open is visible.
            qa_outcome = "unscored"
            unscored.append(st)
        elif st.qa.score < qa.threshold:
            qa_score = st.qa.score
            if final_prior:
                _emit_fallback_finding(
                    shot=shot, score=st.qa.score, threshold=qa.threshold,
                    post_id=post_id,
                    title=f"shot {shot.idx} ({shot.source}) fell back to holdover",
                    body=(f"shot {shot.idx} scored {st.qa.score:.0f} < "
                          f"{qa.threshold:.0f} after {st.attempts} regen(s); held "
                          f"over the prior clip. reason: {st.qa.reason}"),
                )
                result = ShotRenderResult(
                    idx=shot.idx, source=shot.source, success=True,
                    clip_path=final_prior, duration_s=shot.duration_s,
                )
                qa_outcome = "fallback_holdover"
            else:
                # idx 0 with no prior clip to hold over: ship the best, flag it.
                _emit_fallback_finding(
                    shot=shot, score=st.qa.score, threshold=qa.threshold,
                    post_id=post_id,
                    title=f"shot {shot.idx} ({shot.source}) kept below threshold",
                    body=(f"shot {shot.idx} scored {st.qa.score:.0f} < "
                          f"{qa.threshold:.0f} and has no prior clip to hold over; "
                          f"kept the best attempt. reason: {st.qa.reason}"),
                )
                qa_outcome = "kept_below"
        else:
            qa_score = st.qa.score
            qa_outcome = "regenerated" if st.attempts else "accepted"

        await _log_shot_audit(
            pool, post_id=post_id, shot_result=result,
            qa_score=qa_score, qa_outcome=qa_outcome, rung=st.rung,
        )
        out.append(result)
        if result.success and result.clip_path:
            final_prior = result.clip_path

    if unscored:
        # One finding per render, not per shot — this is a "fix the vision
        # infra" signal (the shots ship regardless, fail-open by design).
        # Routed per findings.vision_scorer_unavailable.delivery.
        reasons = sorted({(st.qa.reason or "unknown") for st in unscored if st.qa})
        emit_finding(
            source="shot_list_renderer",
            kind="vision_scorer_unavailable",
            title=(
                f"shot render-check could not score {len(unscored)} of "
                f"{len(states)} shot(s) — accepted unscored"
            ),
            body=(
                f"post {post_id}: {len(unscored)} rendered shot(s) were "
                f"accepted without a vision QA score (fail-open). "
                f"reasons: {'; '.join(reasons)}. Confirm the vision model "
                f"(qa_vision_model) is loaded and reachable."
            ),
            severity="warn",
            dedup_key=f"vision_scorer_unavailable:shot_list_renderer:{post_id}",
            extra={
                "surface": "shot_list_renderer",
                "post_id": post_id,
                "unscored": len(unscored),
                "shots_total": len(states),
                "reasons": reasons,
            },
        )
    return out


def _cap_hero_shots(shots: list[Shot], max_hero: int) -> list[Shot]:
    """Keep at most ``max_hero`` hero (generative/wan21) shots; downgrade the
    rest to ``image_kenburns`` — the still+Ken-Burns cousin, carrying the same
    prompt. The hero render is the most expensive + failure-prone source, so
    the director over-asking shouldn't blow the GPU budget (spec §3.3). A
    negative ``max_hero`` disables the cap (keep everything). Order and
    non-hero shots are preserved.
    """
    if max_hero < 0:
        return list(shots)
    out: list[Shot] = []
    seen = 0
    for s in shots:
        if s.source in _HERO_SOURCES:
            seen += 1
            if seen > max_hero:
                out.append(s.model_copy(update={"source": "image_kenburns"}))
                continue
        out.append(s)
    return out


async def _probe_duration_s(path: str, *, ffprobe: str = "ffprobe") -> float | None:
    """Return a media file's duration in seconds via ffprobe, or None if it
    can't be read.

    Async (non-blocking) — used to fit the short's visuals to the real narration
    length (issue #867). Best-effort: any failure returns None and the caller
    falls back to the director's durations (today's pad behavior), so a broken
    ffprobe can never crash a render.
    """
    if not path or not os.path.exists(path):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        fmt = (json.loads(stdout.decode() or "{}") or {}).get("format") or {}
        dur = float(fmt.get("duration", 0.0))
        return dur if dur > 0 else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("[SHOT_LIST] narration ffprobe failed for %s: %s", path, exc)
        return None


def _fit_scene_durations(
    shot_durations: list[float],
    narration_dur_s: float,
    *,
    max_shot_s: float,
    max_scenes: int = 200,
    tail_pad_s: float = 0.0,
    overhang_tolerance_s: float = 1.0,
) -> list[tuple[int, float]]:
    """Lay out ``(shot_index, duration_s)`` pairs so the concat spans the
    narration — the short-lane fix for the frozen tail (issue #867).

    No-op (returns the director's durations, one pair per shot in order) when the
    narration already fits: the video is longer than the narration, or the
    overhang is within ``overhang_tolerance_s`` (the compositor's tail-pad
    absorbs it).

    Gentle regime (``scale <= max_shot_s / avg_shot``): pure proportional
    rescale — every shot ×``narration/total`` — which spans the narration in one
    pass and preserves the director's pacing EXACTLY (the ~2s hook stays
    proportionally shortest). ``max_shot_s`` is a cycle-trigger, not a hard
    per-frame clamp, so the single longest shot may run modestly over it here —
    fine visually, and it avoids an ugly sub-second cycled fragment.

    Pathological regime (a large overshoot — the average shot would itself exceed
    the ceiling, e.g. re-rendering an old runaway script): cap each shot at
    ``max_shot_s`` and CYCLE the sequence (repeating visuals at a steady cadence)
    until the narration is filled, so no single image is held absurdly long.
    ``max_scenes`` bounds a pathological fill.
    """
    n = len(shot_durations)
    total = sum(shot_durations)
    originals = list(enumerate(shot_durations))
    if n == 0 or total <= 0 or narration_dur_s <= 0:
        return originals
    if narration_dur_s - total <= overhang_tolerance_s:
        return originals
    scale = narration_dur_s / total
    target = narration_dur_s + max(0.0, tail_pad_s)
    avg = total / n
    if scale <= max_shot_s / avg:
        # Gentle: pure proportional rescale, one pass, exact span, pacing kept.
        return [(i, round(d * scale, 3)) for i, d in enumerate(shot_durations)]
    # Pathological: cap each shot at the ceiling and cycle to fill the narration.
    capped = [min(d * scale, max_shot_s) for d in shot_durations]
    layout: list[tuple[int, float]] = []
    acc = 0.0
    i = 0
    while acc < target - 1e-6 and len(layout) < max_scenes:
        idx = i % n
        dur = capped[idx]
        remaining = target - acc
        if dur >= remaining:
            layout.append((idx, round(remaining, 3)))
            break
        layout.append((idx, round(dur, 3)))
        acc += dur
        i += 1
    return layout


async def render_shot_list(
    *,
    post_id: str,
    shot_list: VideoShotList,
    audio_path: str,
    output_path: str,
    image_gen_url: str,
    site_config: Any,
    pool: Any = None,
    http_client_factory: Any = None,
    width: int = 1920,
    height: int = 1080,
    ambient_path: str | None = None,
    caption_path: str | None = None,
    progress_cb: ProgressCb | None = None,
    narration_fit: bool = False,
    narration_fit_max_shot_s: float = 9.0,
) -> ShotListRenderResult:
    """Render a full video from a shot list.

    Args:
        post_id: For audit_log + temp-dir naming.
        shot_list: Validated ``VideoShotList`` from
            ``posts.video_shot_list`` (or in-memory).
        audio_path: Local path to the podcast narration MP3 (ideally
            the body-only sibling — see
            ``podcast_service._maybe_generate_narration_sibling``).
        output_path: Where to write the final MP4.
        image_gen_url: Base URL of the image-gen inference server.
        site_config: DI seam — required for Wan2.1 provider config.
        pool: asyncpg pool for audit-log inserts. Optional.
        http_client_factory: ``httpx.AsyncClient`` factory — defaults
            to the real client. Tests inject a mock.
        width: Output frame width. Default 1920 (16:9 long-form). Pass
            1080 for the 9:16 short profile (Gap A — the renderer used
            to hardcode 1920x1080).
        height: Output frame height. Default 1080 (16:9 long-form).
            Pass 1920 for the 9:16 short profile.
        ambient_path: Local path to the ambient music/SFX bed mixed
            under the narration as the soundtrack (#679). ``None`` =
            no soundtrack mix, so the narration plays clean. The
            narration itself rides ``CompositionRequest.narration_track_path``
            (laid full-volume over the whole concat); the ambient bed is
            mixed UNDER it, so passing the narration here too would
            double-use it.
        caption_path: Local path to an SRT/VTT caption track to burn
            into the video (#676 Plan 5). ``None`` = no captions
            (backcompat default — the existing ``video_service.py``
            caller and the Plan-4 render path render without captions).
            When set, it's threaded to ``CompositionRequest.caption_track_path``
            and the compositor burns the subtitles in.
        progress_cb: Optional async callback ``(step: str, pct: int | None)``
            fired once per shot as it renders (``"shot i/N"`` + honest
            position-pct, 1..99). Best-effort — a raising callback never fails
            the render. The Plan-4 media atom wires this to a live_activity
            ``media`` row so the console pulse shows real per-shot progress;
            ``None`` (the default, and the legacy caller) renders silently.
        narration_fit: Short-lane fit-to-narration (issue #867). When True and
            an ``audio_path`` exists, ffprobe the narration and lay out the
            scenes (via ``_fit_scene_durations``) to span its ACTUAL duration —
            so the compositor never clones the final frame to cover an overhang
            (the frozen tail). Default False (the long lane keeps the director's
            durations and deliberate pacing untouched); the short atom opts in.
        narration_fit_max_shot_s: Per-shot ceiling for the fit rescale — no
            single image is held longer than this; beyond it the shot sequence
            cycles instead of stretching. Only consulted when ``narration_fit``.

    Returns:
        ``ShotListRenderResult`` with file path on success.
    """
    if http_client_factory is None:
        import httpx
        http_client_factory = httpx.AsyncClient

    # Pexels secret + orientation for human/real-world shots. The key is
    # is_secret=true so it MUST come through get_secret (async, DB-backed);
    # orientation follows the shot list's aspect so portrait shorts fetch
    # portrait photos. An empty key just means pexels shots hold over the
    # prior clip rather than image-gen-faking a human (see _render_one_shot).
    pexels_key = ""
    if site_config is not None:
        pexels_key = (await site_config.get_secret("pexels_api_key", "")) or ""
    orientation = "portrait" if shot_list.aspect == "9:16" else "landscape"

    # Per-shot temp dir. Cleaned up unless the operator wants to
    # forensic-debug a failed render (kept on disk on exception).
    work_dir = Path(
        tempfile.mkdtemp(prefix=f"shotlist_{post_id}_", suffix=""),
    )
    logger.info(
        "[SHOT_LIST] rendering post_id=%s — %d shots, total=%.1fs, work_dir=%s",
        post_id, len(shot_list.shots), shot_list.total_duration_s, work_dir,
    )

    qa = _build_qa_config(site_config)
    render_kwargs = dict(
        work_dir=work_dir,
        image_gen_url=image_gen_url,
        site_config=site_config,
        http_client_factory=http_client_factory,
        pexels_key=pexels_key,
        orientation=orientation,
        post_id=post_id,
    )

    # Two-pass to stop the image-gen↔vision-model GPU thrash: render every shot
    # (image model resident for the whole pass), then score every fresh frame
    # (vision model resident), then batched keep-best regen of the
    # sub-threshold stochastic shots, then assign outcomes + audit. The old
    # per-shot ``render → score`` loop evicted image-gen on every vision call, so
    # each next render paid a ~133s cold reload. See ``_ShotState``.
    # Cap the per-video hero-shot budget (spec §3.3) — excess generative shots
    # downgrade to image_kenburns so the director over-asking can't serialise a
    # dozen heavy i2v renders. shots_total below still counts the original list.
    max_hero = (
        site_config.get_int("video_hero_shots_max", 3)
        if site_config is not None else 3
    )
    capped_shots = _cap_hero_shots(list(shot_list.shots), max_hero)

    states = await _render_pass(
        capped_shots, render_kwargs=render_kwargs, progress_cb=progress_cb,
    )
    await _score_pass(
        states, qa=qa, site_config=site_config, pool=pool,
    )
    await _repair_pass(
        states, qa=qa, site_config=site_config,
        render_kwargs=render_kwargs, pool=pool,
    )
    wordmark = ""
    if site_config is not None:
        wordmark = str(site_config.get("site_name", "") or "").strip()
    await _backfill_pass(
        states, render_kwargs=render_kwargs, site_config=site_config,
        post_id=post_id, width=width, height=height, wordmark=wordmark,
    )
    shot_results = await _finalize_pass(
        states, qa=qa, pool=pool, post_id=post_id,
    )

    shots_substituted = sum(1 for st in states if st.rung == "substitute")
    shots_carded = sum(1 for st in states if st.rung == "card")

    rendered = [r for r in shot_results if r.success and r.clip_path]
    if not rendered:
        return ShotListRenderResult(
            success=False,
            shots_total=len(shot_list.shots),
            shots_rendered=0,
            shots_substituted=shots_substituted,
            shots_carded=shots_carded,
            error="no shots rendered — director output unrenderable",
        )

    # Build CompositionScenes. The compositor handles Ken Burns for
    # stills (per-scene zoompan via ``ken_burns_enabled``). Scenes are
    # SILENT — the narration is laid over the whole concat via
    # ``CompositionRequest.narration_track_path`` below. Binding the
    # multi-scene narration to scene 0's ``narration_path`` truncated it
    # at the first transition (#media-render-fixes: audio cut off after
    # scene 2). Per-shot narration slicing is a follow-up — the schema's
    # ``narration_offset_s`` field is the seam.
    # Narration-fit (short lane, issue #867): lay out the scenes to span the
    # ACTUAL narration so the compositor never clones the final frame to cover
    # an overhang (the frozen tail). No-op when the narration already fits;
    # scoped by the caller to the short lane so the long lane's pacing is
    # untouched. Cycling repeats a clip_path — free, no re-render.
    shot_durs = [float(r.duration_s) for r in rendered]
    scene_plan: list[tuple[int, float]] = list(enumerate(shot_durs))
    if narration_fit and audio_path:
        narration_dur = await _probe_duration_s(audio_path)
        if narration_dur:
            scene_plan = _fit_scene_durations(
                shot_durs,
                narration_dur,
                max_shot_s=narration_fit_max_shot_s,
            )
            logger.info(
                "[SHOT_LIST] narration-fit: %d shots (%.1fs) -> %d scenes "
                "for %.1fs narration",
                len(rendered),
                sum(shot_durs),
                len(scene_plan),
                narration_dur,
            )
        else:
            logger.info(
                "[SHOT_LIST] narration-fit: ffprobe unreadable for %s, "
                "keeping director durations (compositor tail-pad handles it)",
                audio_path,
            )
    scenes: list[CompositionScene] = [
        CompositionScene(
            clip_path=rendered[idx].clip_path or "",
            narration_path=None,
            duration_s=dur,
        )
        for idx, dur in scene_plan
    ]

    request = CompositionRequest(
        scenes=scenes,
        # Full-length narration laid over the WHOLE concat at full volume
        # (#media-render-fixes). The compositor overlays this before mixing
        # the ambient bed UNDER it, so the voiceover spans every scene
        # instead of dying at the first transition. None = no narration.
        narration_track_path=audio_path or None,
        # The soundtrack is the AMBIENT bed (#679), NOT the narration. It's
        # mixed under the narration_track_path above. None = clean narration.
        soundtrack_path=ambient_path,
        # SRT/VTT caption track to burn in (#676 Plan 5). None = no captions
        # (backcompat — the legacy video_service caller renders captionless).
        caption_track_path=caption_path,
        output_path=output_path,
        width=width,
        height=height,
        fps=30,
        codec="h264",
        container="mp4",
        metadata={
            "post_id": post_id,
            "renderer": "shot_list_renderer",
            "shot_count": len(scenes),
            "director_model": shot_list.director_model,
            "director_prompt_version": shot_list.director_prompt_version,
        },
    )

    from services.media_compositors.ffmpeg_local import FFmpegLocalCompositor

    compositor = FFmpegLocalCompositor(site_config=site_config)
    composition = await compositor.compose(request)

    if not composition.success or not composition.output_path:
        return ShotListRenderResult(
            success=False,
            shots_total=len(shot_list.shots),
            shots_rendered=len(rendered),
            shots_substituted=shots_substituted,
            shots_carded=shots_carded,
            error=(
                f"compositor failed: {composition.error or 'no output_path'}"
            ),
        )

    return ShotListRenderResult(
        success=True,
        output_path=composition.output_path,
        file_size_bytes=composition.file_size_bytes,
        duration_s=composition.duration_s,
        shots_rendered=len(rendered),
        shots_total=len(shot_list.shots),
        shots_substituted=shots_substituted,
        shots_carded=shots_carded,
    )
