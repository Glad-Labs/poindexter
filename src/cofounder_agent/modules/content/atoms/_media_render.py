"""Shared render helper for the Stage-2 media render atoms (Plan 4).

Underscore-prefixed so the atom-registry filesystem scan SKIPS it
(``services/atom_registry.py``: files starting with ``_`` are not
discovered as atoms). This is plumbing, not an atom — the two thin atoms
``media.render_long_video`` / ``media.render_short_video`` both delegate
here so the wiring of the EXISTING render engine into graph state lives
in one place.

What it does:

1. Reads a shot-list dict from state (``video_shot_list`` for the long
   form, ``short_shot_list`` for the 9:16 short) and rehydrates it into a
   ``VideoShotList``. A missing / None shot list is a graceful no-op —
   nothing to render, so the atom emits an empty output key rather than
   failing the graph. A shot list that EXISTS but fails validation returns
   the same empty key (the graph must not halt) but is NOT a no-op: it emits
   a ``shot_list_invalid`` finding, because the watchdog will otherwise
   re-dispatch it against the identical failure forever (issue #874).
2. Resolves the aspect profile (``9:16`` → 1080×1920, else 1920×1080) and
   threads the podcast narration + the ambient bed (#679) into the
   existing ``render_shot_list`` engine.
3. On a partial render (some-but-not-all shots): below
   ``app_settings.video_render_min_shot_ratio`` (default 0.5) the render is
   treated as FAILED — ``partial_render_rejected`` finding, empty output, so
   the media_reconciliation watchdog re-dispatches it instead of a badly
   degraded video shipping (2026-07-03: a 2/7-shot video shipped). At or
   above the ratio it ships with a ``partial_render`` finding so a degraded
   video never ships silently (redesign §9).
4. On render failure emits a ``render_failed`` finding and returns empty —
   it NEVER raises, because a render failure must not halt the graph.
"""

from __future__ import annotations

import logging
import tempfile
from typing import Any

from pydantic import ValidationError

from schemas.video_shot_list import VideoShotList
from services import live_activity
from services.gpu_scheduler import gpu
from services.video_renderers.shot_list_renderer import render_shot_list
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# image-gen URL convention (mirrors source_featured_image.py / the shot renderer):
# the compose service DNS name, resolvable container-to-container on the shared
# network — never the host-published port (which wedges on Docker Desktop/WSL2).
_DEFAULT_IMAGE_GEN_URL = "http://image-gen-server:9836"


def _resolve_dims(aspect: str) -> tuple[int, int]:
    """Aspect profile → (width, height). 9:16 short vs 16:9 long-form."""
    if aspect == "9:16":
        return 1080, 1920
    return 1920, 1080


async def render_from_state(
    state: dict[str, Any],
    *,
    shot_list_key: str,
    output_key: str,
    narration_key: str = "podcast_audio_path",
    caption_key: str = "caption_srt_path",
    narration_fit: bool = False,
    narration_fit_max_shot_key: str = "video_short_max_shot_seconds",
    narration_fit_max_shot_default: float = 9.0,
) -> dict[str, Any]:
    """Render a video from a shot-list channel in graph state.

    Args:
        state: The LangGraph pipeline state dict.
        shot_list_key: Which state channel holds the shot-list dict
            (``video_shot_list`` or ``short_shot_list``).
        output_key: Which state channel to write the rendered path into
            (``long_video_path`` or ``short_video_path``). MUST be a
            declared ``PipelineState`` channel or LangGraph drops it.
        narration_key: Which state channel holds this lane's narration audio
            path (``long_narration_audio_path`` / ``short_narration_audio_path``
            per #689). Defaults to ``podcast_audio_path`` for backcompat.
        caption_key: Which state channel holds this lane's burned-in SRT
            (``long_caption_srt_path`` / ``short_caption_srt_path``). Defaults to
            ``caption_srt_path`` for backcompat.
        narration_fit: Opt into fit-to-narration (issue #867 stretch + the
            2026-07-31 compression fix). BOTH render atoms pass True now — the
            long lane's old opt-out ("deliberate pacing untouched") is exactly
            what let a 300s director plan ride over a ~175s narration and ship
            2 minutes of silent footage (the 2026-07 rejections). Still gated
            by ``video_narration_fit_enabled`` (master switch); the shared
            compression floor/hold come from ``video_fit_min_shot_seconds`` /
            ``video_fit_trailing_hold_seconds``.
        narration_fit_max_shot_key: Which app_setting holds this lane's
            per-shot ceiling for the fit rescale — the short atom keeps the
            default (``video_short_max_shot_seconds``, 9s retention pacing);
            the long atom passes ``video_long_max_shot_seconds`` (30 — matching
            the director's own per-shot rule, since a 9s ceiling would shred
            legitimate 15-30s long-form holds into cycled fragments).
        narration_fit_max_shot_default: Code default for that ceiling when the
            setting is unreadable (9.0 short / 30.0 long).

    Returns:
        ``{output_key: <path-or-empty-string>}`` — empty on no-op /
        invalid input / render failure. Never raises.
    """
    shot_list_dict = state.get(shot_list_key)
    task_id = state.get("task_id")
    if not shot_list_dict:
        # Nothing persisted (e.g. a non-media task, or the director never
        # produced this aspect) — graceful no-op, render NOT attempted. This
        # one stays silent: it's a normal state, not a defect.
        logger.info(
            "[media.render] no %s in state — skipping %s render",
            shot_list_key,
            output_key,
        )
        return {output_key: ""}

    try:
        shot_list = VideoShotList.model_validate(shot_list_dict)
    except ValidationError as exc:
        # A shot list that EXISTS but won't parse is a defect, not a no-op. No
        # video gets produced, so the media_reconciliation watchdog re-dispatches
        # and hits the identical failure — forever. Task 511012cc burned ~31
        # graph runs over three weeks that way, re-paying TTS + transcription +
        # audio QA each time, because this path only logged (issue #874).
        logger.warning(
            "[media.render] %s failed VideoShotList validation: %s — skipping",
            shot_list_key,
            exc,
        )
        emit_finding(
            source="media.render_video",
            kind="shot_list_invalid",
            title=f"{output_key}: frozen shot list fails schema validation",
            body=(
                f"The {shot_list_key} frozen for task {task_id} no longer "
                f"validates against VideoShotList, so no video can be rendered "
                f"and the media_reconciliation watchdog will re-dispatch it "
                f"indefinitely. Shot lists are frozen at Stage 1 and re-read at "
                f"render time — sometimes weeks later — so this usually means a "
                f"schema change landed without a backcompat shim for the rows "
                f"already frozen (see schemas/video_shot_list.py "
                f"_DEPRECATED_SOURCES for the established shape). Errors: {exc}"
            ),
            severity="warn",
            dedup_key=f"shot_list_invalid:{task_id}:{output_key}",
            extra={
                "task_id": str(task_id or ""),
                "output_key": output_key,
                "shot_list_key": shot_list_key,
                "error_count": exc.error_count(),
            },
        )
        return {output_key: ""}

    site_config = state.get("site_config")
    image_gen_url = (
        site_config.get("image_gen_server_url", _DEFAULT_IMAGE_GEN_URL)
        if site_config is not None
        else _DEFAULT_IMAGE_GEN_URL
    )

    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )

    narration = state.get(narration_key) or ""
    ambient = state.get("video_ambient_audio_path") or None
    # SRT caption track produced by media.transcribe_narration (per-lane #689).
    # Empty-string is the atom's no-op sentinel, so `or None` maps it to None —
    # no track to burn.
    caption = state.get(caption_key) or None
    width, height = _resolve_dims(shot_list.aspect)

    # Narration-fit is opt-in per lane AND gated by the master setting; the
    # per-shot ceiling is lane-appropriate (issue #867 + silent-tail fix) and
    # the compression floor/hold are shared, all operator-tunable.
    fit_enabled = bool(narration_fit) and (
        site_config.get_bool("video_narration_fit_enabled", True)
        if site_config is not None
        else True
    )
    fit_max_shot_s = (
        site_config.get_float(
            narration_fit_max_shot_key, narration_fit_max_shot_default,
        )
        if site_config is not None
        else narration_fit_max_shot_default
    )
    fit_min_shot_s = (
        site_config.get_float("video_fit_min_shot_seconds", 2.0)
        if site_config is not None
        else 2.0
    )
    fit_hold_s = (
        site_config.get_float("video_fit_trailing_hold_seconds", 3.0)
        if site_config is not None
        else 3.0
    )

    out_path = f"{tempfile.gettempdir()}/media_{task_id}_{output_key}.mp4"

    # Best-effort live-activity: surface the render as a kind='media' row in the
    # console pulse's "In Production" column, with real per-shot progress. The
    # ledger never affects the render (swallow-on-error; None id ⇒ silent no-op).
    lane = output_key.replace("_video_path", "") or output_key  # "long" / "short"
    total_shots = len(shot_list.shots)
    try:
        async with live_activity.track(
            pool,
            kind="media",
            ref_id=str(task_id) if task_id else None,
            title=f"Video · {lane}",
            detail={
                "medium": "video",
                "output_key": output_key,
                "shots_total": total_shots,
            },
            heartbeat_seconds=live_activity.resolve_heartbeat_seconds(site_config),
        ) as act:

            async def _progress(step: str, pct: int | None) -> None:
                await act.update(step=step, pct=pct)

            # Hold the GPU for the whole render. The render drives wan + image-gen
            # over HTTP and never went through the scheduler before (validation
            # findings 4b/7): the ~18GB writer/director stayed resident in Ollama
            # and starved wan+image-gen → "inference server unreachable" → render
            # failures. The "video" owner evicts Ollama on acquire, and the
            # cross-process pg_advisory_lock blocks the content pipeline
            # (prefect-worker) for the render's duration so they can't
            # oversubscribe the 32GB card.
            async with gpu.lock(
                "video",
                model="shot_list_render",
                task_id=str(task_id or "") or None,
                phase="media_render",
            ):
                result = await render_shot_list(
                    post_id=str(task_id or ""),
                    shot_list=shot_list,
                    audio_path=narration,
                    output_path=out_path,
                    image_gen_url=image_gen_url,
                    site_config=site_config,
                    pool=pool,
                    width=width,
                    height=height,
                    ambient_path=ambient,
                    caption_path=caption,
                    progress_cb=_progress,
                    narration_fit=fit_enabled,
                    narration_fit_max_shot_s=fit_max_shot_s,
                    narration_fit_min_shot_s=fit_min_shot_s,
                    narration_fit_hold_s=fit_hold_s,
                )
            if not result.success:
                act.fail()
    except Exception as exc:  # noqa: BLE001 — a render must never halt the graph
        logger.exception("[media.render] %s render raised: %s", output_key, exc)
        emit_finding(
            source="media.render_video",
            kind="render_failed",
            title=f"{output_key}: render raised an exception",
            body=f"render_shot_list raised for task {task_id}: {exc}",
            severity="warn",
            dedup_key=f"render_failed:{task_id}:{output_key}",
            extra={"task_id": str(task_id or ""), "output_key": output_key},
        )
        return {output_key: ""}

    if not result.success:
        emit_finding(
            source="media.render_video",
            kind="render_failed",
            title=f"{output_key}: render failed",
            body=(
                f"render_shot_list returned success=False for task {task_id}: "
                f"{result.error or 'no error detail'}"
            ),
            severity="warn",
            dedup_key=f"render_failed:{task_id}:{output_key}",
            extra={"task_id": str(task_id or ""), "output_key": output_key},
        )
        return {output_key: ""}

    # Real-source ratio gate (2026-07-15, "never drop a shot" redesign). The
    # fallback ladder guarantees shots_rendered == shots_total on the happy
    # path — a failed source becomes a cross-family substitute or a rung-3
    # branded card rather than a dropped shot — so a "shots dropped" count no
    # longer signals a real outage (an all-cards video reads as 100% rendered).
    # Key the gate off how much of the video comes from a REAL source (primary
    # + holdover + substitute) vs. branded-card fill. Below the tunable floor
    # the render is treated as FAILED — empty output key, so no video is
    # persisted and the media_reconciliation watchdog re-dispatches it once
    # image-gen/Pexels recover — instead of shipping a mostly-card video. '0'
    # disables the reject gate. (When the card ladder is disabled, shots still
    # drop and `dropped` carries the degrade signal instead.)
    real_shots = result.shots_rendered - result.shots_carded
    real_ratio = real_shots / max(result.shots_total, 1)
    dropped = result.shots_total - result.shots_rendered
    min_real_ratio = (
        site_config.get_float("video_render_min_real_source_ratio", 0.5)
        if site_config is not None
        else 0.5
    )
    if real_ratio < min_real_ratio:
        emit_finding(
            source="media.render_video",
            kind="partial_render_rejected",
            title=(
                f"{output_key}: only {real_shots}/{result.shots_total} shots from "
                "a real source — below the ship threshold, treated as failed"
            ),
            body=(
                f"Only {real_shots} of {result.shots_total} shots for task "
                f"{task_id} came from a real source "
                f"({result.shots_carded} branded-card fill(s), {dropped} dropped; "
                f"real ratio {real_ratio:.0%} < the "
                f"video_render_min_real_source_ratio of {min_real_ratio:.0%}). The "
                "render was rejected instead of shipping a mostly-card video; the "
                "media_reconciliation watchdog will re-dispatch it once image-gen/"
                "Pexels recover. Check the per-shot video_shot_rendered audit_log "
                "rows (rung) for which sources failed."
            ),
            severity="warn",
            dedup_key=f"partial_render_rejected:{task_id}:{output_key}",
            extra={
                "task_id": str(task_id or ""),
                "output_key": output_key,
                "shots_rendered": result.shots_rendered,
                "shots_total": result.shots_total,
                "shots_carded": result.shots_carded,
                "real_ratio": real_ratio,
                "min_real_ratio": min_real_ratio,
            },
        )
        return {output_key: ""}
    if result.shots_carded or result.shots_substituted or dropped:
        # A degraded-but-above-floor video (fallback cards/substitutes, or —
        # with the card ladder disabled — some dropped shots) still ships, but
        # never silently: surface it for triage (redesign §9 / Gap C).
        emit_finding(
            source="media.render_video",
            kind="partial_render",
            title=(
                f"{output_key}: {result.shots_carded} card + "
                f"{result.shots_substituted} substitute fill(s) of "
                f"{result.shots_total} shots"
            ),
            body=(
                f"Task {task_id} shipped with fallback fills — "
                f"{result.shots_carded} branded card(s), "
                f"{result.shots_substituted} cross-family substitute(s), "
                f"{dropped} dropped. Check the per-shot video_shot_rendered "
                "audit_log rows (rung) for which primary sources failed."
            ),
            severity="warn",
            dedup_key=f"partial_render:{task_id}:{output_key}",
            extra={
                "task_id": str(task_id or ""),
                "output_key": output_key,
                "shots_rendered": result.shots_rendered,
                "shots_total": result.shots_total,
                "shots_carded": result.shots_carded,
                "shots_substituted": result.shots_substituted,
            },
        )

    return {output_key: result.output_path or ""}


__all__ = ["render_from_state"]
