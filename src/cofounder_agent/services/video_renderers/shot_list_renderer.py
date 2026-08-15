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
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plugins.media_compositor import CompositionRequest, CompositionScene
from schemas.video_shot_list import _DEMO_ID_RE, Shot, VideoShotList
from services.settings_defaults import default_int
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
# Wan 2.2 TI2V-5B's documented 480P working range. NOT the 720P range it also
# supports: i2v activation memory scales ~quadratically with the plate, and at
# 1152x640 the model peaked ~25-26 GB — enough that a single co-resident (the
# writer LLM, the vision model, image-gen) OOM'd it by as little as 320 MiB on
# a 32 GB card, and it cannot fit an 8-16 GB consumer card at all. At 832x480
# it peaks ~19-20 GB and generated 4-for-4 on the 2026-08-07 validation runs.
# The compositor upscales the clip to the canvas, so the visible cost is
# nil at feed scale. Operators with headroom raise video_hero_width/height.
_HERO_DEFAULT_WIDTH = 832
_HERO_DEFAULT_HEIGHT = 480
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
# ``cli_demo`` is deliberately absent: it is a pre-baked deterministic
# recording, so a "regen" replays identical frames and only burns a QA pass.
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


# Free-VRAM ladder for the hero plate, widest first. i2v activation memory
# scales ~quadratically with the plate, so a step down buys real headroom.
# The gap this closes (2026-08-08): the dispatcher pre-gates on
# media_render_min_free_vram_gb, but the DESKTOP keeps allocating after that
# check — Chrome, the Electron apps and the COSMIC shell held ~3 GB on GPU0
# while wan wanted 26.7 GB of a 32.6 GB card, and the hero OOM'd on its last
# 320 MiB. Same code went 4-for-4 hours earlier on a quieter desktop; the
# operator should not have to close their browser to get motion.
# Plates wan can render at ACCEPTABLE QUALITY, widest first, with the free
# VRAM each needs. There is no rung below 704x400 on purpose.
#
# The 2026-08-08 version of this ladder went down to 512x320 on the theory
# that "a smaller clip upscaled still reads as motion". It does not. Every
# hero rendered at the 512x320 floor came back as neon morphing garbage —
# nonsensical colours and shapes — and the compositor then upscaled that to
# 1080p, which made it worse. The operator's words: "total slop". A clean
# Ken Burns still is FAR better than degraded generative video, so below the
# quality floor this function declines to animate rather than shipping mush.
# Prometheus scrapes GPU memory every ~10s and the worker has no nvidia-smi,
# so one sample right after a reclaim reports the pre-reclaim figure. Sample
# across a scrape interval and keep the max.
_FREE_VRAM_SAMPLES = 4
_FREE_VRAM_SAMPLE_GAP_S = 4.0

_HERO_PLATE_LADDER: tuple[tuple[int, int, float], ...] = (
    (832, 480, 27.0),   # validated: 4-for-4, good output
    (704, 400, 22.0),   # quality floor — modest step, still coherent
)


async def _live_free_vram_gb(site_config: Any) -> float | None:
    """Device-level free VRAM (GB) read LIVE from the wan server, or None.

    The Prometheus path (``GPURegistry.free_gb``) is authoritative for slow
    signals but lags ~40s worst case — 10s exporter refresh plus a 30s scrape
    — and this decision happens milliseconds after a reclaim frees ~25GB.
    Measured 2026-08-09: Prometheus reported 29342 MiB used while nvidia-smi
    showed 16955, so the plate gate skipped every hero as "no room" on a card
    that had just been cleared for it.

    wan runs ON the card and answers from ``torch.cuda.mem_get_info``, so its
    ``/health`` is exact and instant. None means "ask Prometheus instead".
    """
    try:
        import httpx

        from services.video_providers.wan2_1 import _resolve_server_url

        url = _resolve_server_url({}, site_config).rstrip("/") + "/health"
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        free_mb = payload.get("device_free_mb")
        if free_mb is None:
            return None  # older wan build — fall back to Prometheus
        # wan's own pool is reusable BY wan, so add it back.
        own_mb = float(payload.get("vram_used_mb") or 0.0)
        return (float(free_mb) + own_mb) / 1024.0
    except Exception:  # noqa: BLE001
        # silent-ok: this is the preferred source, not the only one; the
        # Prometheus fallback below still applies.
        return None


async def _wan_resident_gb(site_config: Any) -> float:
    """VRAM wan currently holds, in GB — 0.0 when unknown or unloaded.

    Read from the wan server's own ``/health`` (``vram_used_mb``) rather than
    inferred, and fail-soft to 0.0: under-counting only makes the plate check
    more conservative, never less.
    """
    try:
        import httpx

        from services.video_providers.wan2_1 import _resolve_server_url

        url = _resolve_server_url({}, site_config).rstrip("/") + "/health"
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return 0.0
        return float(resp.json().get("vram_used_mb") or 0.0) / 1024.0
    except Exception:  # noqa: BLE001
        # silent-ok: this is a refinement of a best-effort probe. Failing to
        # read it just means the stricter (raw-free) comparison applies.
        return 0.0


async def _fit_hero_dims_to_free_vram(
    width: int, height: int, site_config: Any,
) -> tuple[int, int] | None:
    """Pick a hero plate the card can spare, or ``None`` to skip animating.

    Returns the requested dims unchanged when free VRAM is unknown or ample —
    an unreadable probe must never shrink a render that would have succeeded.
    Steps DOWN only as far as the ladder's quality floor.

    ``None`` means "do not animate this shot": there is not enough VRAM for a
    plate that produces watchable output. The caller falls back to the shot's
    Ken Burns still, which looks intentional, instead of a low-plate render
    that looks broken (see the ladder comment above).
    """
    try:
        if site_config is not None and not site_config.get_bool(
            "video_hero_adaptive_plate_enabled", True,
        ):
            return width, height
    except Exception:  # noqa: BLE001
        # silent-ok: a settings read must not decide the geometry; the
        # documented default (adaptive on) applies.
        pass
    # The worker container is GPU-less, so free VRAM comes from Prometheus —
    # scraped every ~10s. The caller reclaims immediately before this, and a
    # single sample taken 3s later still reports the PRE-reclaim figure. Take
    # the max of a few samples across one scrape interval so a stale low
    # reading cannot decide against a card that was just freed.
    # Prefer the live device reading; Prometheus is the fallback.
    live = await _live_free_vram_gb(site_config)
    if live is not None:
        free_gb = live
        landscape = width >= height
        for lw, lh, needs_gb in _HERO_PLATE_LADDER:
            if free_gb >= needs_gb:
                new_w, new_h = (lw, lh) if landscape else (lh, lw)
                if new_w * new_h >= width * height:
                    return width, height
                logger.info(
                    "[SHOT_LIST] hero plate %dx%d -> %dx%d (%.1fGB free live, "
                    "needs %.0fGB)", width, height, new_w, new_h,
                    free_gb, needs_gb,
                )
                return new_w, new_h
        logger.warning(
            "[SHOT_LIST] only %.1fGB free (live) — NOT animating this hero; "
            "using its Ken Burns still.", free_gb,
        )
        return None

    try:
        from services.gpu_registry import GPURegistry

        registry = GPURegistry(site_config=site_config)
        free_gb = None
        for attempt in range(_FREE_VRAM_SAMPLES):
            sample = await registry.free_gb(0)
            if sample is not None:
                free_gb = sample if free_gb is None else max(free_gb, sample)
            if free_gb is not None and free_gb >= _HERO_PLATE_LADDER[0][2]:
                break  # already ample; no need to wait out the scrape
            if attempt < _FREE_VRAM_SAMPLES - 1:
                await asyncio.sleep(_FREE_VRAM_SAMPLE_GAP_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_LIST] free-VRAM probe failed (%s) — keeping %dx%d",
            exc, width, height,
        )
        return width, height
    if free_gb is None:
        return width, height

    # Add back wan's OWN resident pool. The ladder's needs_gb figures are
    # whole-model footprints, but this probe usually runs with wan already
    # loaded from the previous hero — so raw free VRAM reads ~1GB and the
    # gate concluded there was no room for the model that is already there,
    # skipping every hero after the first (observed 2026-08-09: "only 1.0GB
    # free" while nvidia-smi showed 19GB free and wan held 23GB). wan reuses
    # its own allocation, so the honest question is "free + what wan already
    # holds".
    free_gb += await _wan_resident_gb(site_config)

    landscape = width >= height
    for lw, lh, needs_gb in _HERO_PLATE_LADDER:
        if free_gb >= needs_gb:
            new_w, new_h = (lw, lh) if landscape else (lh, lw)
            # Never step UP past what the operator configured.
            if new_w * new_h >= width * height:
                return width, height
            logger.info(
                "[SHOT_LIST] hero plate %dx%d -> %dx%d (%.1fGB free, needs "
                "%.0fGB) — stepping down rather than OOMing into a still",
                width, height, new_w, new_h, free_gb, needs_gb,
            )
            return new_w, new_h

    _, _, floor_gb = _HERO_PLATE_LADDER[-1]
    logger.warning(
        "[SHOT_LIST] only %.1fGB free (quality floor needs %.0fGB) — NOT "
        "animating this hero; using its Ken Burns still. A sub-floor plate "
        "renders unwatchable morphing, which is worse than a clean still.",
        free_gb, floor_gb,
    )
    return None


def _hero_render_dims(
    orientation: str, site_config: Any,
) -> tuple[int, int, int]:
    """Resolve the hero (i2v) render geometry as ``(width, height, fps)``.

    Reads ``video_hero_width`` / ``video_hero_height`` / ``video_hero_fps``
    (defaults = Wan 2.2 TI2V-5B's documented 480P@24fps working range) and
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


async def _clear_image_gen_for_hero(site_config: Any) -> None:
    """Hard-unload image-gen AND evict Ollama so the wan hero load has the card.

    Two co-residents, not one (poindexter#907, then #992 on 2026-08-07): the
    media pipeline runs its own LLM work — vision QA, caption re-scoring,
    self-consistency — on this same GPU, and Ollama keeps the ~21 GB writer
    resident on its keep_alive timer well past the last call. So the hero
    phase would begin with image-gen cleared but Ollama still squatting, and
    wan (~25.3 GB at 832x480) OOM'd by as little as 320 MiB. Evicting Ollama
    is confirmed, not fire-and-forget: ``_unload_ollama_models`` re-polls
    /api/ps until the model is actually gone.

    Best-effort by design: the caller is about to attempt a render either way,
    and a reclaim that fails should not convert a possible render into a
    certain skip. A failure here just means wan may OOM and fall back to a
    still — the pre-existing behaviour — so this can only improve the odds.

    Gated on ``video_hero_unload_image_gen`` (default on) so an operator whose
    card comfortably fits both can avoid paying image-gen's cold reload.
    """
    try:
        enabled = (
            site_config.get_bool("video_hero_unload_image_gen", True)
            if site_config is not None else True
        )
    except Exception:  # noqa: BLE001  # silent-ok: a settings read must not
        # decide whether a render is attempted; default to the safer behaviour.
        enabled = True
    if not enabled:
        return
    try:
        from services.gpu_scheduler import gpu

        await gpu._unload_image_gen(hard=True)
        # The pipeline's own QA/vision calls leave the writer resident on
        # Ollama's keep_alive timer; it reloads on demand in ~20-30s, which is
        # cheap against a 147s hero that would otherwise fail outright.
        if (
            site_config.get_bool("video_hero_evict_ollama", True)
            if site_config is not None else True
        ):
            await gpu._unload_ollama_models()
        settle = (
            site_config.get_float("video_hero_unload_settle_seconds", 3.0)
            if site_config is not None else 3.0
        ) or 3.0
        # Let the CUDA context actually return to the host before wan asks for
        # it — the exit is asynchronous from this process's point of view.
        await asyncio.sleep(settle)
        logger.info(
            "[SHOT_LIST] cleared co-residents before hero clip (image-gen "
            "hard-unload + Ollama evict, settle %.1fs) — poindexter#907/#992",
            settle,
        )
    except Exception as exc:  # noqa: BLE001  # silent-ok: reclaim is an
        # optimisation, not a precondition. Logged so a persistently failing
        # unload is visible, but never allowed to block the render attempt.
        logger.warning(
            "[SHOT_LIST] pre-hero co-resident clear failed (%s) — attempting "
            "the hero render anyway", exc,
        )


async def _wait_image_gen_ready(
    image_gen_url: str, site_config: Any, *, budget_s: float,
) -> bool:
    """Poll the image-gen /health until it answers, up to ``budget_s``.

    The mirror of ``_wait_wan_ready``, and for the same reason one rung
    earlier in the pipeline (poindexter#992, 2026-08-07). The hero phase
    hard-EXITS image-gen to free the card for wan, so the NEXT render's still
    phase races a container that is cold-booting and lazy-loading a 6B
    checkpoint (~45-60s). Its /generate answers 503 while loading, the
    renderer scores that as an un-renderable shot, and the backfill
    substitutes a recycled image. Measured on the 2026-08-07 takes: **7 of 11
    shots substituted** — not just the heroes; the whole video degrades to
    reused art. Waiting costs seconds against a render that is minutes long.

    Any HTTP 200 counts (the pipeline itself lazy-loads on the first
    generate). Timeout returns False and the caller proceeds — the shots then
    fail into the same substitute ladder as before, never worse.
    """
    import httpx

    if not image_gen_url:
        return False
    url = image_gen_url.rstrip("/") + "/health"
    deadline = time.monotonic() + max(0.0, budget_s)
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            # silent-ok: an unreachable server IS the condition being polled
            # for — "not ready yet" is the expected state during a cold boot,
            # not a failure. The timeout branch below logs at warning when the
            # wait genuinely gives up.
            pass
        if time.monotonic() >= deadline:
            logger.warning(
                "[SHOT_LIST] image-gen not ready after %.0fs at %s — "
                "rendering anyway; failed stills fall to the substitute "
                "ladder (poindexter#992)", budget_s, url,
            )
            return False
        await asyncio.sleep(2.0)

async def _wait_wan_ready(site_config: Any, *, budget_s: float) -> bool:
    """Poll the wan-server /health until it answers, up to ``budget_s``.

    poindexter#966 follow-up (the #899 reliability half): 44 hero shots fell
    back to Ken-Burns stills in one week, dominated by animate calls racing a
    wan-server that was still cold-booting after a hard-unload (its own idle
    exit, or the reclaim rung). The container restart takes seconds — the
    render can afford to wait for /health instead of burning the shot. Any
    HTTP 200 counts (the model itself lazy-loads on the generate call);
    timeout returns False and the caller proceeds — the attempt then fails
    into the same still-fallback as before, never worse.
    """
    import httpx

    from services.video_providers.wan2_1 import _resolve_server_url

    url = _resolve_server_url({}, site_config).rstrip("/") + "/health"
    deadline = time.monotonic() + max(0.0, budget_s)
    while True:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=3.0),
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return True
        except Exception:  # noqa: BLE001  # silent-ok: unreachable IS the
            # condition being waited out; each miss just polls again until
            # the budget expires.
            pass
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(3.0)


async def _clip_has_motion(
    clip_path: str, *, min_delta: float, work_dir: Path,
) -> bool:
    """Frame-delta motion check for a hero clip (poindexter#899).

    Vision QA samples ONE frame, so a structurally valid clip whose motion
    silently died scores like a masterpiece. Extract frames at ~25% and ~75%
    of the clip, downscale to a small grayscale plate, and compare mean
    absolute pixel difference — a genuinely animated clip lands well above
    ``min_delta`` (0-255 scale); a frozen one sits near zero. Fail-open: any
    probe/extract/compare error returns True (assume motion) — the checker
    must never cost a working clip.
    """
    try:
        duration = await _probe_duration_s(clip_path)
        if not duration or duration <= 0.2:
            return True
        frames: list[str] = []
        for tag, frac in (("m1", 0.25), ("m2", 0.75)):
            out = str(work_dir / f"motion_{tag}_{os.path.basename(clip_path)}.png")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-loglevel", "error", "-y",
                "-ss", f"{duration * frac:.3f}", "-i", clip_path,
                "-frames:v", "1", "-vf", "scale=96:54", out,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode != 0 or not os.path.exists(out):
                return True
            frames.append(out)
        from PIL import Image, ImageChops, ImageStat

        with Image.open(frames[0]) as a, Image.open(frames[1]) as b:
            diff = ImageChops.difference(a.convert("L"), b.convert("L"))
            delta = ImageStat.Stat(diff).mean[0]
        return delta >= min_delta
    except Exception as exc:  # noqa: BLE001  # silent-ok: fail-open by
        # contract — a broken checker must never fail a rendered clip.
        logger.debug("[SHOT_LIST] motion check errored for %s: %s", clip_path, exc)
        return True


async def _clear_wan_for_stills(shots: list[Shot], site_config: Any) -> None:
    """Hard-unload wan before the still phase when this render has image-gen
    work (the between-lanes half of poindexter#966).

    The two-phase order fixed hero poisoning WITHIN a render, but the media
    graph runs the LONG lane's render before the SHORT lane's — and the
    long's hero phase leaves ~24 GB of wan resident (its idle unload is
    minutes away), so the short's still phase can't load image-gen and every
    image-gen-family shot substitutes (2026-08-01: 1d10e119's long rendered
    clean while its short substituted 5/8). Symmetric to
    ``_clear_image_gen_for_hero``: best-effort, and cheap when wan holds
    nothing — the wan-server declines the exit (``nothing_to_reclaim``,
    stack#2984) below its reserved floor, so the common wan-cold case costs
    one HTTP round-trip.

    Skipped entirely when the list has no image-gen-family work (an
    all-pexels/holdover list doesn't need image-gen at all), and gated by the
    same ``video_hero_unload_image_gen`` switch — an operator whose card
    fits both models has opted out of this whole choreography.
    """
    needs_image_gen = any(
        s.source in ("image_gen", "image_kenburns") or s.source in _HERO_SOURCES
        for s in shots
    )
    if not needs_image_gen:
        return
    try:
        enabled = (
            site_config.get_bool("video_hero_unload_image_gen", True)
            if site_config is not None else True
        )
    except Exception:  # noqa: BLE001  # silent-ok: a settings read must not
        # decide whether a render is attempted; default to the safer behaviour.
        enabled = True
    if not enabled:
        return
    try:
        from services.gpu_scheduler import gpu

        await gpu._unload_wan(hard=True)
        # ComfyUI holds its loaded models the same way between renders; its
        # /free is decline-gated (no-ops while a render is queued and costs
        # one HTTP round-trip when already empty), so calling both
        # unconditionally is cheaper than threading provider selection here.
        await gpu._unload_comfyui()
    except Exception as exc:  # noqa: BLE001  # silent-ok: reclaim is an
        # optimisation, not a precondition — a failure here reverts to the
        # pre-fix odds, never blocks the render.
        logger.warning(
            "[SHOT_LIST] wan pre-still unload failed (%s) — rendering anyway",
            exc,
        )


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
    """Render one hero clip to ``output_path`` via the configured provider.

    When ``image_path`` is set it's the shot's stylized image-gen still, passed
    as the image-to-video init frame (animating the brand still keeps visual
    consistency — spec §3.3). Absent → text-to-video (wan21 only; the ComfyUI
    provider is i2v-only by design). ``video_generative_provider`` picks the
    animator — ``wan21`` (default) or ``comfyui`` — per clip.
    ``width``/``height``/``fps`` override the provider's
    defaults when set (the caller passes the lane-aspect hero geometry).
    Returns ``(success, reason)`` — ``reason`` is empty on
    success, else a short operator-facing string so a miss is diagnosable from
    the ``hero_render_fallback`` finding alone (the wan-server container that
    produced the miss may already be gone by the time anyone looks).
    """
    from services.video_providers.wan2_1 import Wan21Provider

    # poindexter#907 defect 2 — clear the render card before wan loads.
    #
    # The dispatch-time VRAM gate checks free VRAM ONCE, before the flow
    # starts, and nothing holds that reservation for the ~3.5 minutes the
    # render takes. Measured 2026-07-29: the gate passed with 29.4 GB free at
    # 05:03:45, image-gen then loaded 25.1 GB mid-flow to illustrate the NEXT
    # article, and wan OOM'd at 05:06:36 with 98 MB free — its own process
    # holding just 1.82 GB. It was crowded out, not too big. 18
    # hero_render_fallback findings trace to this.
    #
    # A soft /unload does not return the VRAM (the process keeps its CUDA
    # reserved pool), so this is the HARD unload — process exit + Docker
    # restart, image-gen lazy-reloads on its next /generate. Cheap to repeat:
    # once image-gen holds nothing the server declines the exit
    # (`nothing_to_reclaim`), so the per-clip call is a no-op after the first.
    await _clear_image_gen_for_hero(site_config)

    # Provider seam (ComfyUI integration, 2026-08-15 spike): the operator
    # picks the animator per install via ``video_generative_provider`` —
    # ``wan21`` (deployed 5B sidecar, the default) or ``comfyui`` (Wan 2.2
    # 14B via the ComfyUI sidecar). Read per clip, so flipping is a settings
    # change, not a deploy.
    provider_choice = "wan21"
    if site_config is not None:
        try:
            provider_choice = str(
                site_config.get("video_generative_provider", "wan21") or "wan21",
            ).strip().lower()
        except Exception:  # noqa: BLE001  # silent-ok: a settings read must
            # not decide a render's fate; the deployed default provider stands.
            provider_choice = "wan21"
    provider: Any
    if provider_choice == "comfyui":
        from services.video_providers.comfyui import ComfyUIProvider

        provider = ComfyUIProvider()
    else:
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
        # poindexter#996: the provider explains itself via ``last_error`` —
        # surface that, not a generic string, so the hero_render_fallback
        # finding is diagnosable after the sidecar container is gone.
        reason = getattr(provider, "last_error", "") or (
            f"{provider_choice} provider returned no result — "
            "check sidecar logs/health"
        )
        return False, reason
    ok = bool(results[0].file_path) and os.path.exists(results[0].file_path)  # type: ignore[arg-type]
    if not ok:
        return False, (
            f"{provider_choice} provider result had no output file on disk"
        )
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


async def _resolve_demo_clip(
    shot: Shot,
    site_config: Any,
) -> tuple[str, float, str]:
    """Locate a pre-baked CLI demo clip. Returns ``(path, duration_s, error)``.

    Demo clips are baked out of band (``poindexter media demos bake``) rather
    than recorded during a render, so this is a lookup, not a render — see
    ``services/demo_clips.py`` for why.

    The ``demo_id`` is re-checked against the slug pattern here even though
    ``Shot`` already validates it. This function turns the value into a
    filesystem path, and a shot list can reach it by routes that skip
    re-validation (a hand-edited ``pipeline_versions`` row, a future caller
    building a ``Shot`` directly), so the check belongs at the point of use as
    well as at the boundary.
    """
    demo_id = (shot.demo_id or "").strip()
    if not demo_id:
        return "", 0.0, "cli_demo shot has no demo_id"
    if not _DEMO_ID_RE.fullmatch(demo_id):
        return "", 0.0, f"demo_id {demo_id!r} is not a valid slug"

    clip_dir = "/home/appuser/.poindexter/demo-clips"
    if site_config is not None:
        clip_dir = str(site_config.get("demo_clip_dir", clip_dir) or clip_dir)

    path = Path(clip_dir) / f"{demo_id}.mp4"
    # Belt-and-braces against a clip_dir that itself contains traversal, and
    # against symlink escapes: the resolved path must stay under the resolved
    # clip directory.
    try:
        resolved = path.resolve()
        root = Path(clip_dir).resolve()
        if not resolved.is_relative_to(root):
            return "", 0.0, f"demo clip for {demo_id!r} resolves outside {clip_dir}"
    except OSError as exc:
        return "", 0.0, f"demo clip path error for {demo_id!r}: {exc}"

    if not resolved.is_file():
        return "", 0.0, (
            f"demo clip {demo_id!r} not baked at {resolved} — run "
            f"`poindexter media demos bake --slug {demo_id}`"
        )

    duration = await _probe_duration_s(str(resolved)) or 0.0
    if duration <= 0:
        return "", 0.0, f"demo clip {demo_id!r} has unreadable duration"
    return str(resolved), duration, ""


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

    if source == "cli_demo":
        clip_path, clip_dur, error = await _resolve_demo_clip(shot, site_config)
        if error:
            # Missing / unbaked clip is NOT fatal — fall through to the
            # backfill ladder (pexels substitute, then branded card) exactly
            # like any other failed source. A shot list frozen weeks ago can
            # legitimately name a demo that has since been retired.
            # Distinct kind from the ladder's own ``shot_fallback``: that one
            # says "a slot got filled", this one says WHICH demo is unbaked or
            # retired, which is the actionable half. The ladder still emits its
            # finding when it fills the slot — the two are complementary, not
            # duplicates.
            emit_finding(
                source="shot_list_renderer",
                kind="demo_clip_missing",
                title=f"demo clip {shot.demo_id!r} unavailable for shot {shot.idx}",
                body=(
                    f"{error}. The shot falls through to the fallback ladder so "
                    f"the timeline stays whole. Repeated hits mean the bake job "
                    f"is failing or the shot list names a retired demo."
                ),
                severity="warn",
                dedup_key=f"demo_clip_missing:{shot.demo_id}",
                extra={"shot_idx": shot.idx, "demo_id": shot.demo_id},
            )
            return ShotRenderResult(
                idx=shot.idx, source=source, success=False, error=error,
            )
        # Clamp to what the clip actually contains. The compositor
        # ``-stream_loop``s any non-still shorter than its scene duration, and
        # a looped terminal recording re-types the command mid-shot — visibly
        # broken in a way a looped abstract clip is not. ``shot_durs`` in
        # ``render_shot_list`` reads THIS value, so clamping here is what
        # reaches the scene plan.
        duration = min(float(shot.duration_s), clip_dur) if clip_dur > 0 else float(shot.duration_s)
        if clip_dur and duration < shot.duration_s:
            logger.info(
                "[SHOT_LIST] cli_demo idx=%d demo_id=%s: director asked %.1fs, "
                "clip is %.1fs — trimming the shot rather than looping it",
                shot.idx, shot.demo_id, shot.duration_s, clip_dur,
            )
        return ShotRenderResult(
            idx=shot.idx, source=source, success=True,
            clip_path=clip_path, duration_s=duration,
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
        _render_default = default_int("image_render_timeout_seconds")
        render_timeout = (
            site_config.get_int("image_render_timeout_seconds", _render_default)
            if site_config is not None else _render_default
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
        still_result = await _render_hero_still(
            shot,
            work_dir=work_dir,
            image_gen_url=image_gen_url,
            site_config=site_config,
            http_client_factory=http_client_factory,
            orientation=orientation,
        )
        if not still_result.success or not still_result.clip_path:
            return still_result
        return await _animate_hero(
            shot,
            still_path=still_result.clip_path,
            site_config=site_config,
            orientation=orientation,
            post_id=post_id,
        )

    return ShotRenderResult(
        idx=shot.idx,
        source=source,
        success=False,
        error=f"unknown source {source!r}",
    )


async def _render_hero_still(
    shot: Shot,
    *,
    work_dir: Path,
    image_gen_url: str,
    site_config: Any,
    http_client_factory: Any,
    orientation: str,
) -> ShotRenderResult:
    """Render a hero shot's stylized image-gen INIT still (poindexter#966).

    Split out of the generative branch so ``_render_pass`` can produce every
    hero's still during the still phase — while image-gen is resident — and
    defer the wan animation to the hero phase. The still is both the
    image-to-video init frame and the Ken-Burns fallback if the clip render
    misses (spec §3.3); if even the still fails there's nothing to animate or
    fall back to, so the shot hard-fails into the backfill ladder. Requested
    at the lane's hero geometry so the i2v init frame needs no crop/stretch
    and the Ken-Burns fallback fills the frame.
    """
    if not shot.prompt:
        return ShotRenderResult(
            idx=shot.idx,
            source=shot.source,
            success=False,
            error=f"{shot.source} shot missing prompt",
        )
    hero_w, hero_h, _hero_fps = _hero_render_dims(orientation, site_config)
    still_path = str(work_dir / f"shot_{shot.idx:02d}.png")
    _render_default = default_int("image_render_timeout_seconds")
    render_timeout = (
        site_config.get_int("image_render_timeout_seconds", _render_default)
        if site_config is not None else _render_default
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
            source=shot.source,
            success=False,
            error="generative shot: image-gen still render failed",
        )
    return ShotRenderResult(
        idx=shot.idx,
        source=shot.source,
        success=True,
        clip_path=still_path,
        duration_s=shot.duration_s,
    )


async def _animate_hero(
    shot: Shot,
    *,
    still_path: str,
    site_config: Any,
    orientation: str,
    post_id: str,
) -> ShotRenderResult:
    """Animate a hero shot's pre-rendered still via wan (poindexter#966).

    The wan-side half of the generative branch. ``_render_generative_clip``
    hard-unloads image-gen before the wan load (#907 defect 2) — running the
    animations only AFTER every still has rendered means that unload fires
    when image-gen has no work left this pass, so it can no longer poison
    the remaining image-gen-family shots (the 2026-08-01 e68e renders
    substituted the identical 4 shots twice because the first mid-list hero
    evicted image-gen while ~24 GB of wan crowded the card).

    On an i2v miss the shot falls back to its own still (the compositor
    applies Ken Burns to a PNG scene automatically) with a
    ``hero_render_fallback`` finding — NOT a holdover of the prior clip
    (spec §3.3).
    """
    hero_w, hero_h, hero_fps = _hero_render_dims(orientation, site_config)
    # Right-size the plate to what the card can spare RIGHT NOW — the
    # dispatcher's pre-flight VRAM gate can't see the desktop allocating
    # afterwards (2026-08-08: wan OOM'd on its last 320 MiB against Chrome
    # and the COSMIC shell).
    # Reclaim BEFORE measuring. _render_generative_clip does this too, but it
    # runs after this point — so probing first measured the card with image-gen
    # still holding ~25GB and skipped every hero ("only 3.1GB free" while the
    # clear was about to free 25GB). The second call inside
    # _render_generative_clip is then a cheap no-op (it declines when nothing
    # is reserved), and other call paths keep their own reclaim.
    await _clear_image_gen_for_hero(site_config)
    plate = await _fit_hero_dims_to_free_vram(hero_w, hero_h, site_config)
    if plate is None:
        # Not enough VRAM for a plate that renders watchable motion. Ship the
        # still rather than neon morphing garbage (2026-08-09: every hero
        # rendered at the old 512x320 ladder floor came back as slop).
        _emit_hero_fallback_finding(
            shot=shot, post_id=post_id,
            reason=(
                "insufficient free VRAM for a quality hero plate — used the "
                "still rather than rendering a sub-floor clip, which produces "
                "unwatchable morphing"
            ),
        )
        return ShotRenderResult(
            idx=shot.idx,
            source=shot.source,
            success=True,
            clip_path=still_path,
            duration_s=shot.duration_s,
        )
    hero_w, hero_h = plate
    clip_path = str(Path(still_path).with_suffix(".mp4"))
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
        # Motion check (poindexter#899): a clip can render structurally valid
        # with dead motion — single-frame vision QA can't see it. Frame-delta
        # gate; a dead clip falls back to its own still (which at least gets
        # honest Ken Burns motion) with a distinct finding.
        check_enabled = True
        min_delta = 2.0
        if site_config is not None:
            try:
                check_enabled = site_config.get_bool(
                    "video_hero_motion_check_enabled", True,
                )
                min_delta = site_config.get_float(
                    "video_hero_min_motion_delta", 2.0,
                )
            except Exception:  # noqa: BLE001  # silent-ok: settings read must
                # not decide a render's fate; fall back to code defaults.
                check_enabled, min_delta = True, 2.0
        if check_enabled and not await _clip_has_motion(
            clip_path, min_delta=min_delta, work_dir=Path(still_path).parent,
        ):
            emit_finding(
                source="shot_list_renderer",
                kind="hero_motion_dead",
                title=f"hero shot {shot.idx} rendered with no motion — using still",
                body=(
                    f"The wan clip for shot {shot.idx} (post {post_id}) passed "
                    f"render but its frames are near-identical (delta below "
                    f"video_hero_min_motion_delta={min_delta}). Falling back to "
                    "the Ken-Burns still so the shot at least moves. Recurring "
                    "hits mean the i2v motion prompt or the wan-server needs "
                    "attention (poindexter#899)."
                ),
                severity="warn",
                dedup_key=f"hero_motion_dead:{post_id}:{shot.idx}",
                extra={"shot_idx": shot.idx, "post_id": post_id, "min_delta": min_delta},
            )
            return ShotRenderResult(
                idx=shot.idx,
                source=shot.source,
                success=True,
                clip_path=still_path,
                duration_s=shot.duration_s,
            )
        return ShotRenderResult(
            idx=shot.idx,
            source=shot.source,
            success=True,
            clip_path=clip_path,
            duration_s=shot.duration_s,
        )
    _emit_hero_fallback_finding(shot=shot, post_id=post_id, reason=clip_error)
    return ShotRenderResult(
        idx=shot.idx,
        source=shot.source,
        success=True,
        clip_path=still_path,
        duration_s=shot.duration_s,
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
    """Render every shot once, in two VRAM-coherent phases (poindexter#966).

    **Still phase** — every shot in list order, with image-gen resident for
    the whole phase: non-hero shots render fully via ``_render_one_shot``;
    hero shots render ONLY their image-gen init still
    (``_render_hero_still``). **Hero phase** — each pending hero's still is
    animated via wan (``_animate_hero``). The first animation hard-unloads
    image-gen for the wan load (#907 defect 2, via
    ``_render_generative_clip``); ordering the phases this way means that
    unload fires when image-gen has NO work left this pass. Before the split,
    a mid-list hero evicted image-gen while ~24 GB of wan crowded the card,
    so every later image-gen-family shot — and every later hero's own init
    still — failed deterministically into a Pexels substitute (2026-08-01:
    task e68e4fe8 rendered twice and substituted the identical 4 shots both
    times).

    Threads ``render_prior`` (the last successful fresh clip) so holdover and
    pexels-miss shots resolve against the prior clip exactly as the old
    sequential loop did. During the still phase a hero's ``render_prior``
    contribution is its still; after the hero phase, any reused result that
    held a hero's still is re-pointed to the finished clip so a
    holdover-after-hero carries the animated clip exactly as before the
    split.

    ``is_reused`` flags a shot whose result reused the prior clip (a holdover,
    or a pexels miss that held over) — those are never scored (the prior clip
    was already vetted) and get re-pointed to the post-QA prior in finalize.

    ``progress_cb`` (best-effort) fires ``("shot i/N", pct)`` at the top of
    each still-phase shot and ``("hero clip j/M", pct)`` per animation —
    the media pulse row's real per-shot progress.
    """
    # Between-lanes choreography: a PREVIOUS render's hero phase (e.g. the
    # long lane's, when this is the short's) may still have ~24 GB of wan
    # resident — evict it before this pass's image-gen work, mirroring the
    # pre-hero image-gen unload. Declined server-side when wan holds nothing.
    await _clear_wan_for_stills(shots, render_kwargs.get("site_config"))

    states: list[_ShotState] = []
    render_prior: str | None = None
    total = len(shots)
    pending_heroes: list[_ShotState] = []

    # The previous render's hero phase exits image-gen to free the card, so
    # this still phase can arrive while it is still cold-booting. Wait for
    # /health rather than substituting recycled art for every shot.
    _sc_local = render_kwargs.get("site_config")
    try:
        _img_budget = (
            _sc_local.get_float("video_image_gen_ready_wait_s", 90.0)
            if _sc_local is not None else 90.0
        )
    except Exception:  # noqa: BLE001
        # silent-ok: a settings read must never decide whether the wait runs;
        # fall through to the documented default rather than skipping it.
        _img_budget = 90.0
    if _img_budget > 0:
        await _wait_image_gen_ready(
            render_kwargs.get("image_gen_url", ""), _sc_local,
            budget_s=_img_budget,
        )

    for i, shot in enumerate(shots, start=1):
        pct = min(99, max(1, round(100 * i / total))) if total else None
        await _safe_progress(progress_cb, f"shot {i}/{total}", pct)
        if shot.source in _HERO_SOURCES:
            result = await _render_hero_still(
                shot,
                work_dir=render_kwargs["work_dir"],
                image_gen_url=render_kwargs["image_gen_url"],
                site_config=render_kwargs["site_config"],
                http_client_factory=render_kwargs["http_client_factory"],
                orientation=render_kwargs["orientation"],
            )
            state = _ShotState(shot=shot, result=result, is_reused=False)
            if result.success and result.clip_path:
                pending_heroes.append(state)
        else:
            result = await _render_one_shot(
                shot, prior_clip=render_prior, **render_kwargs,
            )
            state = _ShotState(
                shot=shot,
                result=result,
                is_reused=bool(
                    result.success and result.clip_path
                    and result.clip_path == render_prior,
                ),
            )
        states.append(state)
        if result.success and result.clip_path:
            render_prior = result.clip_path

    # Hero phase: animate every pre-rendered still. The first
    # _render_generative_clip call hard-unloads image-gen (once — later calls
    # find nothing_to_reclaim); by now image-gen's work this pass is done.
    # Ready-wait first (#899 reliability half): a wan-server mid-cold-boot
    # (its own idle exit, or the reclaim rung) fails every animate call it
    # races — 44 still-fallbacks in one week. Bounded + best-effort: a
    # timeout proceeds into the same still-fallback as before.
    hero_total = len(pending_heroes)
    if pending_heroes:
        sc = render_kwargs.get("site_config")
        try:
            wait_budget = (
                sc.get_float("video_hero_wan_ready_wait_s", 90.0)
                if sc is not None else 90.0
            )
        except Exception:  # noqa: BLE001  # silent-ok: a settings read must
            # not decide whether heroes render; default budget applies.
            wait_budget = 90.0
        if wait_budget > 0 and not await _wait_wan_ready(sc, budget_s=wait_budget):
            logger.warning(
                "[SHOT_LIST] wan-server not reachable within %.0fs — "
                "attempting %d hero animation(s) anyway", wait_budget, hero_total,
            )
    for j, state in enumerate(pending_heroes, start=1):
        await _safe_progress(progress_cb, f"hero clip {j}/{hero_total}", None)
        still_path = state.result.clip_path or ""
        state.result = await _animate_hero(
            state.shot,
            still_path=still_path,
            site_config=render_kwargs["site_config"],
            orientation=render_kwargs["orientation"],
            post_id=render_kwargs.get("post_id", ""),
        )
        # A holdover/pexels-miss that reused this hero's STILL during the
        # still phase now points at the finished clip — the pre-split
        # semantics, where a post-hero holdover carried the animated clip.
        if (
            state.result.success
            and state.result.clip_path
            and state.result.clip_path != still_path
        ):
            for other in states:
                if other.is_reused and other.result.clip_path == still_path:
                    other.result.clip_path = state.result.clip_path
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
    min_shot_s: float = 0.0,
    shortfall_hold_s: float | None = None,
    max_scenes: int = 200,
    tail_pad_s: float = 0.0,
    overhang_tolerance_s: float = 1.0,
) -> list[tuple[int, float]]:
    """Lay out ``(shot_index, duration_s)`` pairs so the concat spans the
    narration — the fit for the frozen tail (issue #867) and, with
    ``shortfall_hold_s`` set, for the silent tail (2026-07-31 long-lane fix).

    No-op (returns the director's durations, one pair per shot in order) when the
    narration already fits: the overhang in either direction is within its
    tolerance (``overhang_tolerance_s`` when the narration runs longer — the
    compositor's tail-pad absorbs it; ``shortfall_hold_s`` when the visuals run
    longer — a short outro hold after the voice is deliberate).

    NARRATION LONGER THAN VISUALS (stretch):

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

    VISUALS LONGER THAN NARRATION (compress — only when ``shortfall_hold_s`` is
    not None): proportional scale-down aimed at ``narration + shortfall_hold_s``
    (the visuals hold one deliberate beat after the voice stops instead of a
    hard cut), flooring every shot at ``min_shot_s`` so a deep over-plan can't
    compress the hook into a flicker. Floor collisions may leave the total
    modestly over the target — bounded by ``n × min_shot_s``, and strictly
    better than the minutes-long silent tail it replaces (a 300s director plan
    over a ~175s narration was the shape that got the 2026-07 long videos
    rejected). ``None`` (the default) disables compression entirely — the
    pre-fix contract, which every legacy caller keeps.
    """
    n = len(shot_durations)
    total = sum(shot_durations)
    originals = list(enumerate(shot_durations))
    if n == 0 or total <= 0 or narration_dur_s <= 0:
        return originals
    overhang = narration_dur_s - total
    if overhang > overhang_tolerance_s:
        # Stretch: the narration outruns the visuals (the #867 frozen tail).
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
    if shortfall_hold_s is not None:
        # Compress: the visuals outrun the voice (the long-lane silent tail).
        # Trigger only past hold+tolerance so a deliberate outro beat (or a
        # sub-second rounding gap) never churns a re-layout.
        shortfall = -overhang
        if shortfall > shortfall_hold_s + overhang_tolerance_s:
            target = narration_dur_s + max(0.0, shortfall_hold_s)
            scale = target / total
            floor = max(0.0, min_shot_s)
            return [
                (i, round(max(d * scale, floor), 3))
                for i, d in enumerate(shot_durations)
            ]
    return originals


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
    narration_fit_min_shot_s: float = 0.0,
    narration_fit_hold_s: float | None = None,
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
        narration_fit: Fit-to-narration (issue #867 + the 2026-07-31 long-lane
            silent-tail fix). When True and an ``audio_path`` exists, ffprobe
            the narration and lay out the scenes (via ``_fit_scene_durations``)
            to span its ACTUAL duration — so the compositor never clones the
            final frame to cover an overhang (the frozen tail), and — when
            ``narration_fit_hold_s`` is set — the visuals never outlive the
            voice by minutes (the silent tail). Both render atoms opt in;
            default False for the legacy ``video_service`` caller.
        narration_fit_max_shot_s: Per-shot ceiling for the fit rescale — no
            single image is held longer than this; beyond it the shot sequence
            cycles instead of stretching. Lane-appropriate: the short atom
            passes ``video_short_max_shot_seconds`` (9), the long atom
            ``video_long_max_shot_seconds`` (30 — long-form pacing legitimately
            holds a shot 15-30s, so the short ceiling would shred it). Only
            consulted when ``narration_fit``.
        narration_fit_min_shot_s: Per-shot floor for the compression direction —
            no shot is scaled below this when the visuals outrun the voice.
        narration_fit_hold_s: How long the visuals may outlive the voice before
            the fit compresses them (and the outro beat it compresses TO).
            ``None`` disables compression (stretch-only, the pre-fix contract).

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
    # Narration-fit (issue #867 + the long-lane silent-tail fix): lay out the
    # scenes to span the ACTUAL narration so the compositor never clones the
    # final frame to cover an overhang (the frozen tail) and the visuals never
    # outlive the voice by minutes (the silent tail — a 300s director plan over
    # a ~175s narration got the 2026-07 long videos rejected). No-op when the
    # narration already fits. Cycling repeats a clip_path — free, no re-render.
    shot_durs = [float(r.duration_s) for r in rendered]
    scene_plan: list[tuple[int, float]] = list(enumerate(shot_durs))
    if narration_fit and audio_path:
        narration_dur = await _probe_duration_s(audio_path)
        if narration_dur:
            scene_plan = _fit_scene_durations(
                shot_durs,
                narration_dur,
                max_shot_s=narration_fit_max_shot_s,
                min_shot_s=narration_fit_min_shot_s,
                shortfall_hold_s=narration_fit_hold_s,
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
