"""media.qa — Stage-2 media QA atom (Plan 6, #1193).

QA-checks the RENDERED videos after the render nodes, replacing the audit-era
duration+size-only check (``services/media_quality_service.py``) with three
content-aware checks per rendered asset:

A. **A/V duration sync** (deterministic): probe the rendered video's duration
   (ffprobe) and compare to the lane's ACTUAL narration audio when present
   (silent-tail fix, 2026-07-31), falling back to the director shot-list's
   ``total_duration_s`` when there is no narration to compare against. The
   narration is the reference that matters — comparing against the PLAN both
   missed the 2026-07 silent-tail rejections (video faithfully rendered an
   over-long plan → zero drift vs plan, minutes of drift vs voice) AND
   false-alarmed on every narration-fitted short (fitted video ≈ narration ≠
   plan). Asymmetric tolerance: the video may run under the voice by at most
   ``media.qa.av_sync_tolerance_s`` (default 2.0s — a shorter video cuts the
   speaker off) and over it by ``video_fit_trailing_hold_seconds`` + the same
   tolerance (the renderer deliberately holds one outro beat). Beyond either
   bound emits an advisory ``av_desync`` finding.
B. **Caption presence** (deterministic): captions are best-effort upstream
   (``media.transcribe_narration`` no-ops when whisper is absent), so a missing
   ``caption_srt_path`` is advisory — one ``missing_captions`` finding total.
C. **Frame human-detection** (vision, GATED + fail-soft): when
   ``media_qa_frame_detection_enabled`` is on, extract a midpoint frame and ask
   the local vision model whether it shows a PHOTOREALISTIC human (policy #675 —
   image-gen renders abstract editorial art, "no people"). A "yes" emits an advisory
   ``human_in_frame`` finding. Entirely fail-soft: a missing ffmpeg / vision
   error records ``"unavailable"`` and emits NO finding (don't cry wolf when the
   tool isn't there).

**A QA failure must NEVER halt the graph.** The whole body is wrapped so the
atom always returns a ``{"media_qa_result": {...}}`` dict — even if a check
raises. ``media_qa_result`` MUST be a declared ``PipelineState`` channel
(#674 trap) or LangGraph silently drops it.

(``qa.audio`` — the Qwen2-Audio narration check grouped into #1193 — is
DEFERRED: that model isn't installed and can't be validated, so it is NOT
scaffolded here.)
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import tempfile
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

# Reuse the audit-era ffprobe/subprocess helpers rather than reinventing them.
from services.media_quality_service import _probe_duration, _run_argv
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# Default A/V-sync tolerance (seconds). Tunable via app_settings
# ``media.qa.av_sync_tolerance_s``. A drift beyond this between the probed
# render duration and the shot-list's planned total flags an av_desync.
_DEFAULT_AV_SYNC_TOLERANCE_S = 2.0

# poindexter#716 — no hardcoded model name; "vision_alt_model" is seeded in
# settings_defaults.py.  An empty string here means "not configured" — the
# human-detect step below skips gracefully rather than sending an empty-model
# request. The constant is kept for the site_config.get() default parameter so
# callers that pass site_config=None can still reach the skip guard.
_DEFAULT_VISION_MODEL = ""

# Generous generation budget so qwen3-vl's reasoning tokens don't starve the
# one-word answer (mirrors image_captioner's _DEFAULT_GEN_MAX_TOKENS note).
_VISION_GEN_MAX_TOKENS = 1024

_HUMAN_PROMPT = (
    "Does this video frame contain a PHOTOREALISTIC human face, hands, or body? "
    "Stylized illustrations, silhouettes, and cartoon figures do NOT count. "
    "Answer with one word: yes or no."
)


ATOM_META = AtomMeta(
    name="media.qa",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2 media QA: A/V duration sync, caption presence, and gated "
        "frame human-detection on the rendered videos."
    ),
    inputs=(
        FieldSpec(name="long_video_path", type="str", description="rendered 16:9 MP4 path", required=False),
        FieldSpec(name="short_video_path", type="str", description="rendered 9:16 MP4 path", required=False),
        FieldSpec(name="video_shot_list", type="dict", description="16:9 director shot list (total_duration_s)", required=False),
        FieldSpec(name="short_shot_list", type="dict", description="9:16 director shot list (total_duration_s)", required=False),
        FieldSpec(name="long_narration_audio_path", type="str", description="long narration MP3 — the A/V-sync reference when present", required=False),
        FieldSpec(name="short_narration_audio_path", type="str", description="short narration MP3 — the A/V-sync reference when present", required=False),
        FieldSpec(name="caption_srt_path", type="str", description="burned-in SRT path ('' when unavailable)", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (QA thresholds / vision model)", required=False),
        FieldSpec(name="platform", type="object", description="capability handle — platform.dispatch for the vision LLM call (Seam 1, #667)", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool source)", required=False),
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
    ),
    outputs=(
        FieldSpec(name="media_qa_result", type="dict", description="per-asset QA signals"),
    ),
    requires=("task_id",),
    produces=("media_qa_result",),
    capability_tier=None,
    cost_class="compute",
    idempotent=True,
    side_effects=("reads rendered files; may call a vision model",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=False,
)


def _resolve_tolerance(site_config: Any) -> float:
    """Read the DB-configurable A/V-sync tolerance (default 2.0s)."""
    if site_config is None:
        return _DEFAULT_AV_SYNC_TOLERANCE_S
    try:
        raw = site_config.get(
            "media.qa.av_sync_tolerance_s", _DEFAULT_AV_SYNC_TOLERANCE_S
        )
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_AV_SYNC_TOLERANCE_S


# Default deliberate post-voice outro hold (seconds) — mirrors the
# settings_defaults seed for video_fit_trailing_hold_seconds.
_DEFAULT_TRAILING_HOLD_S = 3.0


def _resolve_trailing_hold(site_config: Any) -> float:
    """Read the renderer's trailing-hold allowance (default 3.0s).

    Same ``video_fit_trailing_hold_seconds`` setting the narration-fit
    compression aims at, so QA and the renderer share one definition of
    "acceptable tail". Same duck-typed ``.get`` + cast shape as
    ``_resolve_tolerance`` (site_config fakes in tests only carry ``get``).
    """
    if site_config is None:
        return _DEFAULT_TRAILING_HOLD_S
    try:
        raw = site_config.get(
            "video_fit_trailing_hold_seconds", _DEFAULT_TRAILING_HOLD_S
        )
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TRAILING_HOLD_S


def _frame_detection_enabled(site_config: Any) -> bool:
    """Gate for the vision frame human-detection check (default on)."""
    if site_config is None:
        return True
    raw = site_config.get("media_qa_frame_detection_enabled", "true")
    return str(raw).strip().lower() in ("true", "1", "yes")


def _pool_from_state(state: dict[str, Any]) -> Any:
    """Best-effort pool resolution (database_service.pool, else state['pool'])."""
    database_service = state.get("database_service")
    if database_service is not None:
        return getattr(database_service, "pool", None)
    return state.get("pool")


async def _extract_frame(video_path: str, *, at_s: float) -> bytes | None:
    """Extract ONE frame at ``at_s`` seconds as PNG bytes. None on any failure.

    Fail-soft: a missing ffmpeg, a failed extract, or a missing output file all
    return None so the caller records ``"unavailable"`` rather than crying wolf.
    """
    if not shutil.which("ffmpeg"):
        return None
    out_png = os.path.join(
        tempfile.gettempdir(),
        f"media_qa_frame_{os.getpid()}_{abs(hash(video_path)) % 10**8}.png",
    )
    try:
        rc, _, _ = await _run_argv(
            [
                "ffmpeg", "-y", "-hide_banner", "-nostats",
                "-ss", f"{max(0.0, at_s):.3f}",
                "-i", video_path,
                "-frames:v", "1",
                out_png,
            ],
            timeout=30.0,
        )
    except Exception:  # noqa: BLE001 - silent-ok: None is this helper's
        # documented 'no frame extracted' answer and the caller already
        # branches on it. ffprobe/ffmpeg being absent or hung is the
        # expected reason, not an anomaly worth paging on. — fail-soft, the tool may be absent/hung
        return None
    if rc != 0:
        return None
    try:
        with open(out_png, "rb") as f:
            data = f.read()
    except OSError:
        return None
    finally:
        try:
            os.remove(out_png)
        except OSError:
            # silent-ok: see the rationale immediately below.
            # Best-effort temp-frame cleanup — a failed unlink (file already
            # gone, transient FS error) must not mask the extracted frame or
            # break QA. The OS reaps the temp dir eventually; intentionally
            # swallow.
            pass
    return data or None


async def _detect_human_in_frame(
    video_path: str,
    *,
    duration_s: float | None,
    site_config: Any,
    platform: Any,
    pool: Any,
    task_id: str | None,
) -> str:
    """Vision-check a midpoint frame for a photoreal human (policy #675).

    Returns one of: ``"clean"`` (model said no), ``"human_found"`` (model said
    yes), ``"unavailable"`` (ffmpeg/vision unavailable or errored — fail-soft),
    or ``"disabled"`` (gate off). Never raises.

    The LLM call goes through the ``platform.dispatch`` capability handle (Seam 1
    module-purity discipline #667 — ``modules/content`` rents the dispatcher via
    the platform seam rather than importing ``services`` internals).
    """
    if not _frame_detection_enabled(site_config):
        return "disabled"

    if platform is None:
        # No capability handle to dispatch the vision call — fail-soft.
        logger.debug(
            "[media.qa] no platform handle for %s — skipping human-detect",
            video_path,
        )
        return "unavailable"

    midpoint = (duration_s / 2.0) if (duration_s and duration_s > 0) else 0.0
    png_bytes = await _extract_frame(video_path, at_s=midpoint)
    if png_bytes is None:
        # ffmpeg missing / extract failed — fail-soft, no finding.
        logger.debug(
            "[media.qa] frame extract unavailable for %s — skipping human-detect",
            video_path,
        )
        return "unavailable"

    vmodel = (
        site_config.get("vision_alt_model", _DEFAULT_VISION_MODEL)
        if site_config is not None
        else _DEFAULT_VISION_MODEL
    )
    if not vmodel:
        # poindexter#716 — vision_alt_model not configured; skip human-detect
        # rather than sending an empty-model request.
        logger.debug(
            "[media.qa] vision_alt_model not set; human-detect skipped for %s",
            video_path,
        )
        return "unavailable"
    image_b64 = base64.b64encode(png_bytes).decode()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _HUMAN_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ],
        }
    ]

    # GPU coordination — qwen3-vl is ~19.6 GB; serialize against image-gen/writer.
    from services.gpu_scheduler import gpu

    try:
        async with gpu.lock(
            "ollama", model=vmodel, task_id=task_id, phase="media_qa_human_detect"
        ):
            # Via the platform.dispatch seam (#667) — NOT a direct services import.
            result = await platform.dispatch.complete(
                pool=pool,
                messages=messages,
                model=vmodel,
                tier="standard",
                task_id=task_id,
                phase="media_qa_human_detect",
                temperature=0.0,
                max_tokens=_VISION_GEN_MAX_TOKENS,
            )
    except Exception as exc:  # noqa: BLE001 — fail-soft, vision failure ≠ QA failure
        logger.warning("[media.qa] vision human-detect call failed: %s", exc)
        return "unavailable"

    raw = (getattr(result, "text", "") or "").strip()
    # Strip reasoning-model chain-of-thought (mirrors image_captioner).
    if "</think>" in raw:
        raw = raw.split("</think>", 1)[1].strip()
    return "human_found" if raw.lower().startswith("yes") else "clean"


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """QA the rendered videos. Best-effort — NEVER raises.

    Returns ``{"media_qa_result": {<label>: {...signals...}, ...}}``. Assets
    with an empty / nonexistent path are skipped (nothing to QA).
    """
    task_id = state.get("task_id")
    site_config = state.get("site_config")
    platform = state.get("platform")
    pool = _pool_from_state(state)
    result: dict[str, Any] = {}

    try:
        tolerance = _resolve_tolerance(site_config)
        caption_present = bool(state.get("caption_srt_path"))
        captions_finding_emitted = False

        # The renderer's deliberate post-voice outro beat (silent-tail fix) —
        # the video may outlive the narration by this much before it's a
        # defect. Same setting the narration-fit compression aims at, so QA
        # and the renderer share one definition of "acceptable tail".
        trailing_hold_s = _resolve_trailing_hold(site_config)

        assets = [
            (
                "long",
                state.get("long_video_path") or "",
                state.get("video_shot_list"),
                state.get("long_narration_audio_path") or "",
            ),
            (
                "short",
                state.get("short_video_path") or "",
                state.get("short_shot_list"),
                state.get("short_narration_audio_path") or "",
            ),
        ]

        for label, video_path, shot_list, narration_path in assets:
            # Skip assets that weren't rendered (empty path) or whose file is
            # missing — nothing to QA.
            if not video_path or not os.path.exists(video_path):
                continue

            asset: dict[str, Any] = {}

            # --- Check A: A/V duration sync (deterministic) ---
            # Reference: the lane's ACTUAL narration audio when present (the
            # property that matters — the 2026-07 silent-tail videos matched
            # their over-long PLAN perfectly while running minutes past the
            # voice, and narration-fitted shorts false-alarmed against the
            # plan). Plan total is the fallback when there is no narration.
            actual = await _probe_duration(video_path)
            plan = (shot_list or {}).get("total_duration_s") if shot_list else None
            narration_dur = None
            if narration_path and os.path.exists(narration_path):
                narration_dur = await _probe_duration(narration_path)
            asset["actual_duration_s"] = actual
            asset["expected_duration_s"] = plan
            asset["narration_duration_s"] = narration_dur
            asset["av_sync_reference"] = "narration" if narration_dur else "plan"
            if actual is None:
                asset["av_sync_ok"] = None  # unknown — probe failed
            elif narration_dur:
                # Asymmetric bounds: under-run cuts the speaker off
                # (tolerance only); over-run is allowed one deliberate outro
                # hold beyond the voice (hold + tolerance) before it's a
                # silent tail.
                shortfall = float(narration_dur) - float(actual)  # voice cut
                overrun = float(actual) - float(narration_dur)  # silent tail
                voice_cut = shortfall > tolerance
                silent_tail = overrun > trailing_hold_s + tolerance
                in_sync = not (voice_cut or silent_tail)
                asset["av_sync_ok"] = in_sync
                if not in_sync:
                    drift = shortfall if voice_cut else overrun
                    shape = (
                        "the narration outruns the video — the speaker is cut off"
                        if voice_cut
                        else "the video outlives the narration — silent footage tail"
                    )
                    emit_finding(
                        source="media.qa",
                        kind="av_desync",
                        title=(
                            f"{label} video A/V desync "
                            f"{actual:.1f}s vs {narration_dur:.1f}s narration"
                        ),
                        body=(
                            f"The rendered {label} video for task {task_id} is "
                            f"{actual:.2f}s but its narration audio runs "
                            f"{narration_dur:.2f}s: {shape} "
                            f"(drift {drift:.2f}s; allowed under-run "
                            f"{tolerance}s, allowed over-run "
                            f"{trailing_hold_s + tolerance:.1f}s = trailing hold "
                            f"+ tolerance). Advisory only."
                        ),
                        severity="warn",
                        dedup_key=f"av_desync:{task_id}:{label}",
                        extra={
                            "task_id": str(task_id or ""),
                            "label": label,
                            "actual_duration_s": actual,
                            "narration_duration_s": narration_dur,
                            "expected_duration_s": plan,
                            "drift_s": drift,
                            "tolerance_s": tolerance,
                            "trailing_hold_s": trailing_hold_s,
                            "reference": "narration",
                        },
                    )
            elif plan is None:
                asset["av_sync_ok"] = None  # no reference to compare against
            else:
                drift = abs(float(actual) - float(plan))
                in_sync = drift <= tolerance
                asset["av_sync_ok"] = in_sync
                if not in_sync:
                    emit_finding(
                        source="media.qa",
                        kind="av_desync",
                        title=(
                            f"{label} video A/V desync "
                            f"{actual:.1f}s vs {plan:.1f}s"
                        ),
                        body=(
                            f"The rendered {label} video for task {task_id} is "
                            f"{actual:.2f}s but the director shot list planned "
                            f"{plan:.2f}s (drift {drift:.2f}s > tolerance "
                            f"{tolerance}s; no narration audio to compare "
                            "against). Likely a render truncation. Advisory only."
                        ),
                        severity="warn",
                        dedup_key=f"av_desync:{task_id}:{label}",
                        extra={
                            "task_id": str(task_id or ""),
                            "label": label,
                            "actual_duration_s": actual,
                            "expected_duration_s": plan,
                            "drift_s": drift,
                            "tolerance_s": tolerance,
                            "reference": "plan",
                        },
                    )

            # --- Check B: caption presence (deterministic, advisory) ---
            asset["caption_present"] = caption_present
            if not caption_present and not captions_finding_emitted:
                # Emit ONCE total — the captions are shared across both renders.
                emit_finding(
                    source="media.qa",
                    kind="missing_captions",
                    title=f"{label} video has no caption track",
                    body=(
                        f"No caption_srt_path on state for task {task_id} — the "
                        "rendered videos have no burned-in captions (ASR was "
                        "unavailable upstream, e.g. whisper not installed). "
                        "Captions are best-effort; advisory only."
                    ),
                    severity="info",
                    dedup_key=f"missing_captions:{task_id}",
                    extra={"task_id": str(task_id or "")},
                )
                captions_finding_emitted = True

            # --- Check C: frame human-detection (vision, gated + fail-soft) ---
            human_detection = await _detect_human_in_frame(
                video_path,
                duration_s=actual,
                site_config=site_config,
                platform=platform,
                pool=pool,
                task_id=task_id,
            )
            asset["human_detection"] = human_detection
            if human_detection == "human_found":
                emit_finding(
                    source="media.qa",
                    kind="human_in_frame",
                    title=(
                        f"{label} video frame may contain a photoreal human "
                        "(policy #675)"
                    ),
                    body=(
                        f"The vision model flagged a photorealistic human in a "
                        f"midpoint frame of the rendered {label} video for task "
                        f"{task_id}. Glad Labs media is abstract editorial art "
                        "(image-gen, 'no people') — a real human likely means a "
                        "stock-frame leak or a misrendered shot. Advisory only."
                    ),
                    severity="warn",
                    dedup_key=f"human_in_frame:{task_id}:{label}",
                    extra={"task_id": str(task_id or ""), "label": label},
                )

            result[label] = asset

        logger.info(
            "[media.qa] task=%s QA'd %d asset(s): %s",
            task_id,
            len(result),
            {k: v.get("av_sync_ok") for k, v in result.items()},
        )
    except Exception as exc:  # noqa: BLE001 — a QA failure must never halt the graph
        logger.exception("[media.qa] task=%s QA raised, returning partial: %s", task_id, exc)

    return {"media_qa_result": result}


__all__ = ["ATOM_META", "run"]
