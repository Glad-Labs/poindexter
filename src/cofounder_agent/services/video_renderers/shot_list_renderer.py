"""ShotListRenderer — assemble a video from a VideoShotList.

The shot list (``schemas/video_shot_list.VideoShotList``) is the seam
between the LLM director (decides what each shot should be) and this
renderer (assembles a final MP4 from per-shot clips + the podcast
narration). The shot list lives on ``posts.video_shot_list jsonb`` —
NULL means "director hasn't run, fall back to legacy slideshow".

Per-source dispatch:

- ``sdxl`` — single SDXL frame, held static for ``duration_s``
- ``sdxl_kenburns`` — single SDXL frame with Ken Burns motion
- ``pexels`` — real Pexels stock photo via ``PexelsProvider`` (image,
  not video — pexels-video is a future provider). The director routes
  human / real-world subjects here on purpose; on any Pexels miss the
  renderer holds over the prior clip rather than SDXL-generating a
  person (AI faces/hands are the strongest "AI slop" tell). Ken Burns
  disabled (real photos with motion look fine static for shorter shots).
- ``wan21`` — Wan2.1 T2V model clip via ``Wan21Provider``. Capped at
  6 seconds per the director prompt (artifacts beyond that show
  seams in the diffusion).
- ``holdover`` — cross-fade transition placeholder. V1 treats this
  the same as ``sdxl`` by carrying the prior shot's prompt — a true
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

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plugins.media_compositor import CompositionRequest, CompositionScene
from schemas.video_shot_list import Shot, VideoShotList
from services.video_renderers.shot_vision_qa import ShotQAResult, score_shot_frame
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


# Wan2.1 clip-duration cap. Artifacts beyond ~6s show seams in the
# 1.3B model's output; the director prompt also caps shot duration at
# 6s for wan21. Defensive ceiling here in case the director sneaks
# something through.
_WAN21_MAX_DURATION_S = 6

# Stochastic sources worth re-rolling on a vision-QA miss — a fresh SDXL/Wan
# seed yields a different image. Pexels is deterministic (same top result), so
# it's excluded: a pexels miss falls straight through to the holdover fallback.
_REGENERABLE_SOURCES = frozenset({"sdxl", "sdxl_kenburns", "wan21", "generative"})

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

    The two-pass split exists to stop the SDXL↔vision-model GPU thrash: the
    old per-shot loop ran ``render → score`` for each shot, so every vision
    call evicted SDXL and the next shot's render paid a ~133s cold reload.
    Batching all renders, then all scores, keeps each model resident for a
    whole pass. ``_ShotState`` is the mutable carrier that lets the later
    passes update a shot's best result/score without re-rendering.
    """

    shot: Shot
    result: ShotRenderResult  # best result so far (fresh render, or best regen)
    is_reused: bool  # True ⇒ holdover / pexels-miss (reused a prior clip; never scored)
    qa: ShotQAResult | None = None  # best score (None ⇒ unscored / couldn't score)
    attempts: int = 0  # regen rounds spent on this shot


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
            }),
            "info" if shot_result.success else "warning",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[SHOT_LIST] audit_log insert failed for shot %d: %s",
            shot_result.idx, exc,
        )


async def _render_sdxl_image(
    *,
    prompt: str,
    output_path: str,
    sdxl_url: str,
    http_client_factory: Any,
    render_timeout: int = 240,
) -> bool:
    """Render one SDXL image to disk via the SDXL server.

    Mirrors the shape used in
    ``video_service._generate_images_from_scenes`` but writes to a
    caller-supplied path. Returns True when the PNG is on disk.
    """
    import httpx

    from services.video_service import _consume_sdxl_image_response

    neg = (
        "text, words, letters, watermark, face, person, hands, blurry, "
        "low quality, distorted, ugly, deformed"
    )
    try:
        async with http_client_factory(
            timeout=httpx.Timeout(float(render_timeout), connect=5.0),
        ) as client:
            resp = await client.post(
                f"{sdxl_url}/generate",
                json={
                    "prompt": prompt,
                    "negative_prompt": neg,
                    # steps / guidance_scale omitted — the SDXL server's
                    # per-model registry drives them (z_image_turbo is
                    # guidance-distilled: 9 steps / CFG 0). SDXL-Turbo's
                    # hardcoded 4 / 1.0 produced degraded frames. Matches the
                    # inline-image path (replace_inline_images). #image-zimage-and-variety.
                },
                timeout=render_timeout,
            )
            got = await _consume_sdxl_image_response(
                resp,
                sdxl_url=sdxl_url,
                output_path=output_path,
                frame_label=f"shot SDXL {os.path.basename(output_path)}",
            )
            return got is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_LIST] SDXL render failed for %s: %s",
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
) -> bool:
    """Fetch a real stock photo from Pexels and write it to ``output_path``.

    The director routes human / real-world shots to ``source="pexels"`` on
    purpose (its HUMAN-SUBJECT POLICY — AI faces/hands are the strongest
    "AI slop" tell). This wires the EXISTING ``PexelsProvider`` so those
    shots get actual footage instead of the old behaviour, which ignored the
    configured key and SDXL-generated the human query (six-fingered hands).

    Returns True only when a JPG is on disk. On any miss the caller holds
    over the prior clip rather than SDXL-faking the subject.
    """
    if not api_key or not query.strip():
        return False

    import httpx

    from services.image_providers.pexels import PexelsProvider

    try:
        results = await PexelsProvider().fetch(
            query,
            {"api_key": api_key, "orientation": orientation, "per_page": 1},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_LIST] Pexels search failed for %r: %s", query, exc)
        return False

    if not results:
        return False

    url = results[0].url
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


async def _render_generative_clip(
    *,
    prompt: str,
    output_path: str,
    image_path: str | None,
    duration_s: int,
    site_config: Any,
) -> bool:
    """Render one hero clip to ``output_path`` via the Wan provider.

    When ``image_path`` is set it's the shot's stylized SDXL still, passed
    as the image-to-video init frame (animating the brand still keeps visual
    consistency — spec §3.3). Absent → text-to-video. Delegates to the
    existing ``Wan21Provider`` so the request body shape is the correct one
    for the wan-server. Returns True on success.
    """
    from services.video_providers.wan2_1 import Wan21Provider

    provider = Wan21Provider()
    try:
        results = await provider.fetch(
            prompt,
            {
                "output_path": output_path,
                "duration_s": min(duration_s, _WAN21_MAX_DURATION_S),
                "image_path": image_path or "",
                "_site_config": site_config,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOT_LIST] generative render raised for %s: %s",
            os.path.basename(output_path), exc,
        )
        return False

    if not results:
        return False
    return bool(results[0].file_path) and os.path.exists(results[0].file_path)  # type: ignore[arg-type]


async def _render_one_shot(
    shot: Shot,
    *,
    prior_clip: str | None,
    work_dir: Path,
    sdxl_url: str,
    site_config: Any,
    http_client_factory: Any,
    pexels_key: str = "",
    orientation: str = "landscape",
    post_id: str = "",
) -> ShotRenderResult:
    """Produce a clip file for one shot.

    Returns a ``ShotRenderResult`` with ``clip_path`` set on success.
    Holdover shots reuse ``prior_clip`` (V1 simplification — a true
    cross-fade is a follow-up).
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
        # tell, so we fetch a REAL stock photo instead of SDXL-generating a
        # person. On any Pexels miss we hold over the prior clip rather than
        # SDXL-faking the subject (#media-render-fixes: the short video shipped
        # a six-fingered AI human because this branch used to SDXL the query).
        query = (shot.query or shot.prompt or "").strip()
        if not query:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="pexels shot missing query and prompt",
            )
        clip_path = str(work_dir / f"shot_{shot.idx:02d}.jpg")
        ok = await _render_pexels_image(
            query=query,
            output_path=clip_path,
            api_key=pexels_key,
            orientation=orientation,
            http_client_factory=http_client_factory,
        )
        if ok:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=True,
                clip_path=clip_path,
                duration_s=shot.duration_s,
            )
        # Pexels miss (no key / no result / download fail) — NEVER SDXL a
        # human. Hold over the prior clip if we have one; otherwise this
        # shot drops out and the rest of the video still renders.
        if prior_clip:
            logger.info(
                "[SHOT_LIST] pexels miss at idx=%d — holding over prior clip "
                "(no SDXL human fallback)", shot.idx,
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

    if source in ("sdxl", "sdxl_kenburns"):
        sdxl_prompt = shot.prompt if shot.prompt else (shot.query or "")
        if not sdxl_prompt:
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
        ok = await _render_sdxl_image(
            prompt=sdxl_prompt,
            output_path=clip_path,
            sdxl_url=sdxl_url,
            http_client_factory=http_client_factory,
            render_timeout=render_timeout,
        )
        if not ok:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="SDXL render returned no image",
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
        # Render the stylized SDXL still FIRST — it's both the image-to-video
        # init frame and the Ken-Burns fallback if the clip render misses
        # (spec §3.3). If even the still fails there's nothing to animate or
        # fall back to, so the shot hard-fails.
        still_path = str(work_dir / f"shot_{shot.idx:02d}.png")
        render_timeout = (
            site_config.get_int("image_render_timeout_seconds", 240)
            if site_config is not None else 240
        )
        still_ok = await _render_sdxl_image(
            prompt=shot.prompt,
            output_path=still_path,
            sdxl_url=sdxl_url,
            http_client_factory=http_client_factory,
            render_timeout=render_timeout,
        )
        if not still_ok:
            return ShotRenderResult(
                idx=shot.idx,
                source=source,
                success=False,
                error="generative shot: SDXL still render failed",
            )
        clip_path = str(work_dir / f"shot_{shot.idx:02d}.mp4")
        clip_ok = await _render_generative_clip(
            prompt=shot.prompt,
            output_path=clip_path,
            image_path=still_path,
            duration_s=int(shot.duration_s),
            site_config=site_config,
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
        _emit_hero_fallback_finding(shot=shot, post_id=post_id)
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


async def _render_pass(
    shots: list[Shot],
    *,
    render_kwargs: dict[str, Any],
) -> list[_ShotState]:
    """Render every shot once, with the image model resident across the pass.

    Threads ``render_prior`` (the last successful fresh clip) so holdover and
    pexels-miss shots resolve against the prior clip exactly as the old
    sequential loop did. The key property: only ``sdxl`` / ``wan21`` shots
    touch the GPU here, and nothing scores, so SDXL loads once and stays warm
    for the whole pass instead of being evicted by a per-shot vision call.

    ``is_reused`` flags a shot whose result reused the prior clip (a holdover,
    or a pexels miss that held over) — those are never scored (the prior clip
    was already vetted) and get re-pointed to the post-QA prior in finalize.
    """
    states: list[_ShotState] = []
    render_prior: str | None = None
    for shot in shots:
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
    http_client_factory: Any,
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
            site_config=site_config, http_client_factory=http_client_factory,
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
    http_client_factory: Any,
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
            cand = await _render_one_shot(st.shot, prior_clip=None, **render_kwargs)
            cands.append((st, cand))
        # Re-score the batch (vision model resident), keep-best per shot.
        for st, cand in cands:
            if not (cand.success and cand.clip_path):
                continue
            cand_qa = await score_shot_frame(
                frame_path=cand.clip_path, shot=st.shot,
                site_config=site_config, http_client_factory=http_client_factory,
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


def _emit_hero_fallback_finding(*, shot: Shot, post_id: str) -> None:
    """Emit the ``hero_render_fallback`` finding — a generative hero shot's
    image-to-video render produced no clip, so the renderer fell back to the
    stylized SDXL still (Ken-Burns'd by the compositor). Distinct kind from
    ``shot_quality_fallback`` so the Findings dashboard can track i2v render
    misses separately from QA-score fallbacks (spec §3.3)."""
    emit_finding(
        source="shot_list_renderer", kind="hero_render_fallback",
        title=f"hero shot {shot.idx} fell back to still (Ken Burns)",
        body=(f"shot {shot.idx} ({shot.source}) — image-to-video render "
              f"produced no clip; used the stylized SDXL still with Ken Burns "
              f"motion instead."),
        severity="warn",
        dedup_key=f"hero_render_fallback:{post_id}:{shot.idx}",
        extra={"shot_idx": shot.idx, "source": shot.source},
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
    for st in states:
        shot = st.shot
        result = st.result
        qa_score: float | None = None
        qa_outcome: str | None = None

        if st.is_reused:
            # Holdover / pexels-miss → carry the post-QA prior clip. (An idx-0
            # reuse can't happen: the render pass fails it for lack of a prior.)
            if final_prior:
                result = ShotRenderResult(
                    idx=shot.idx, source=shot.source, success=True,
                    clip_path=final_prior, duration_s=shot.duration_s,
                )
        elif not qa.enabled or not result.success or not result.clip_path:
            pass  # accept the render verbatim — no QA verdict to apply
        elif st.qa is None or st.qa.score is None:
            pass  # could not score → accept, don't penalise
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
            qa_score=qa_score, qa_outcome=qa_outcome,
        )
        out.append(result)
        if result.success and result.clip_path:
            final_prior = result.clip_path
    return out


def _cap_hero_shots(shots: list[Shot], max_hero: int) -> list[Shot]:
    """Keep at most ``max_hero`` hero (generative/wan21) shots; downgrade the
    rest to ``sdxl_kenburns`` — the still+Ken-Burns cousin, carrying the same
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
                out.append(s.model_copy(update={"source": "sdxl_kenburns"}))
                continue
        out.append(s)
    return out


async def render_shot_list(
    *,
    post_id: str,
    shot_list: VideoShotList,
    audio_path: str,
    output_path: str,
    sdxl_url: str,
    site_config: Any,
    pool: Any = None,
    http_client_factory: Any = None,
    width: int = 1920,
    height: int = 1080,
    ambient_path: str | None = None,
    caption_path: str | None = None,
) -> ShotListRenderResult:
    """Render a full video from a shot list.

    Args:
        post_id: For audit_log + temp-dir naming.
        shot_list: Validated ``VideoShotList`` from
            ``posts.video_shot_list`` (or in-memory).
        audio_path: Local path to the podcast narration MP3 (ideally
            the body-only sibling — see
            ``video_service.generate_video_for_post``).
        output_path: Where to write the final MP4.
        sdxl_url: Base URL of the SDXL inference server.
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
    # prior clip rather than SDXL-faking a human (see _render_one_shot).
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
        sdxl_url=sdxl_url,
        site_config=site_config,
        http_client_factory=http_client_factory,
        pexels_key=pexels_key,
        orientation=orientation,
        post_id=post_id,
    )

    # Two-pass to stop the SDXL↔vision-model GPU thrash: render every shot
    # (image model resident for the whole pass), then score every fresh frame
    # (vision model resident), then batched keep-best regen of the
    # sub-threshold stochastic shots, then assign outcomes + audit. The old
    # per-shot ``render → score`` loop evicted SDXL on every vision call, so
    # each next render paid a ~133s cold reload. See ``_ShotState``.
    # Cap the per-video hero-shot budget (spec §3.3) — excess generative shots
    # downgrade to sdxl_kenburns so the director over-asking can't serialise a
    # dozen heavy i2v renders. shots_total below still counts the original list.
    max_hero = (
        site_config.get_int("video_hero_shots_max", 3)
        if site_config is not None else 3
    )
    capped_shots = _cap_hero_shots(list(shot_list.shots), max_hero)

    states = await _render_pass(capped_shots, render_kwargs=render_kwargs)
    await _score_pass(
        states, qa=qa, site_config=site_config,
        http_client_factory=http_client_factory,
    )
    await _repair_pass(
        states, qa=qa, site_config=site_config,
        render_kwargs=render_kwargs, http_client_factory=http_client_factory,
    )
    shot_results = await _finalize_pass(
        states, qa=qa, pool=pool, post_id=post_id,
    )

    rendered = [r for r in shot_results if r.success and r.clip_path]
    if not rendered:
        return ShotListRenderResult(
            success=False,
            shots_total=len(shot_list.shots),
            shots_rendered=0,
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
    scenes: list[CompositionScene] = []
    for r in rendered:
        scenes.append(
            CompositionScene(
                clip_path=r.clip_path or "",
                narration_path=None,
                duration_s=float(r.duration_s),
            ),
        )

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
            "shot_count": len(rendered),
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
    )
