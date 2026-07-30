"""Shared contention-skip reporting for the media stages (poindexter#914 P2).

Library, not a stage — the leading underscore keeps it out of any registry
walk, same convention as ``atoms/_image_helpers.py``.

All three media stages (``generate_media_scripts``,
``generate_video_shot_list``, ``review_video_shot_list``) opt into GPU
admission with :func:`services.gpu_scheduler.media_wait_budget_s`, and each
already has a degraded path for a failed LLM call. What they lack on their
own is a way to say *why* they degraded: every one of them catches broad
``Exception`` and logs it as a dispatch failure, so an ordinary contention
skip would be indistinguishable from a real infra fault — a burst of render
pressure would read as the media pipeline breaking.

Hence a distinct ``media_gpu_busy_skip`` finding kind at ``info``, mirroring
the ``qa_rail_gpu_busy_skip`` split that group 1 made for the QA rails.
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


def surface_media_gpu_busy_skip(
    stage: str, busy: Any, *, task_id: str | None,
) -> None:
    """Report a media stage that skipped because admission refused the wait.

    ``info`` severity and ``log_only`` delivery: a bounded skip under load is
    the system working as designed. Persistent skips are the signal worth
    acting on — either ``gpu_sched_media_max_wait_s`` is too tight, or media
    is being scheduled straight into render contention.
    """
    try:
        from utils.findings import emit_finding

        eta_raw = getattr(busy, "eta_seconds", None)
        eta = f"{eta_raw:.0f}s" if eta_raw is not None else "unknown"
        reason = getattr(busy, "reason", "unknown")
        emit_finding(
            source=f"stages.{stage}",
            kind="media_gpu_busy_skip",
            severity="info",
            title=f"{stage} skipped — GPU busy beyond its wait budget",
            body=(
                f"GPU admission refused the wait ({reason}; holder ETA ~{eta}), "
                f"so {stage} skipped rather than queueing behind a long render "
                "up to the lock ceiling. The post continues without this "
                "media artefact — publish is not blocked. Persistent skips "
                "mean render pressure is crowding out media: raise "
                "gpu_sched_media_max_wait_s, or stop scheduling media against "
                "renders."
            ),
            dedup_key=f"media_gpu_busy_skip:{stage}",
            extra={
                "stage": stage,
                "reason": reason,
                "eta_seconds": eta_raw,
                "task_id": task_id,
            },
        )
    except Exception:  # noqa: BLE001  # silent-ok: emit_finding never raises by
        # contract; this only fires if that contract itself breaks. Mirrors the
        # guard in ragas_eval._surface_gpu_busy_skip.
        logger.debug("[%s] gpu-busy-skip finding emit skipped", stage, exc_info=True)
