"""FanoutCandidatePruneJob — age out retained featured-fanout candidate images.

``image_fanout._retain_candidates`` uploads EVERY candidate of every judged
fan-out to ``fanout/<YYYYMMDD>/<task>/<candidate>-<HHMMSS>.png`` so a judged
row's score can be checked against the image it describes (poindexter#1032).
That is the whole point of retention — and it also means the prefix only ever
grows. Nothing else deletes from it.

**Why this is not ``media_orphan_sweep``, and why ``fanout/`` must never be
added to ``media_orphan_sweep_prefixes``.** That job decides by REFERENCE: an
object survives if its key appears in a post, a ``media_assets`` row, or the
feed XML. Fan-out candidates appear in none of those — they are referenced
only from ``audit_log`` details — so every one of them, including the render
from ten minutes ago, reads as an orphan there. Adding the prefix would
delete them on the orphan job's grace window and for the wrong reason. These
objects are not orphans; they are deliberately-kept audit artifacts with a
TTL, which is a different lifecycle and gets its own job.

**The TTL is aligned to the audit_log retention window (90d) on purpose.** The
image exists to make its judged row checkable, so it should not outlive the
row, and the row should not outlive the image. Change one and consider the
other.

Safety, in the order it matters:

- **Hard prefix guard.** Every key is re-checked against ``fanout/`` at the
  delete call site, not merely at the list call. A prefix typo therefore
  deletes nothing rather than sweeping the bucket.
- **A retention floor.** Below ``_MIN_RETENTION_DAYS`` the job refuses to run
  at all, so a fat-fingered ``0`` cannot mean "delete everything".
- **A per-run cap**, so a first real run on a large backlog is bounded and
  observable rather than a single unbounded delete storm.
- ``idempotent = False`` -> apscheduler ``max_instances=1``. Deletes must not
  run concurrently with themselves; idempotent jobs get ``max_instances=3``.

Note it is inert for the first 90 days of retained data by construction —
there is nothing old enough to delete — so it reports zero until then.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

# The ONLY prefix this job may ever delete under. Re-asserted per key at the
# delete site — see the module docstring.
_PREFIX = "fanout/"

# Refuse to run below this. The setting is an operator knob, and "0" is the
# shape a fat finger produces; without a floor that reads as "delete all".
_MIN_RETENTION_DAYS = 7

_DEFAULT_RETENTION_DAYS = 90
_DEFAULT_MAX_DELETES = 500


def _num(site_config: Any, key: str, default: int) -> int:
    if site_config is None:
        return default
    try:
        val = site_config.get(key, default)
    except Exception as exc:  # noqa: BLE001 — a settings read must not decide
        # a delete job's behaviour; the code default is the documented one.
        logger.warning(
            "[FANOUT_PRUNE] settings read failed for %s (%s) — using %d",
            key, describe_exception(exc), default,
        )
        return default
    if val in (None, ""):
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


class FanoutCandidatePruneJob:
    name = "fanout_candidate_prune"
    description = (
        "Delete retained featured-fanout candidate images older than "
        "image_fanout_candidate_retention_days from the object store."
    )
    schedule = "37 4 * * *"  # daily 04:37 operator-local, off the hour
    # Deletes are irreversible: never run two passes concurrently.
    # False -> apscheduler max_instances=1.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config = (config or {}).get("_site_config")
        if site_config is None:
            # Fail loud rather than construct an uploader that will fail on its
            # first settings read: without SiteConfig we cannot resolve the
            # retention window either, and guessing it here is exactly the
            # silent default that must never gate a delete.
            return JobResult(
                ok=False,
                detail="no _site_config in job config — refusing to prune",
                changes_made=0,
                metrics={"refused": 1},
            )
        days = _num(site_config, "image_fanout_candidate_retention_days",
                    _DEFAULT_RETENTION_DAYS)
        cap = _num(site_config, "image_fanout_candidate_prune_max_deletes_per_run",
                   _DEFAULT_MAX_DELETES)

        if days < _MIN_RETENTION_DAYS:
            # Fail loud and change nothing — per feedback_no_silent_defaults,
            # a nonsensical retention window is an operator error to surface,
            # not one to silently substitute a default for.
            return JobResult(
                ok=False,
                detail=(
                    f"refusing to prune: image_fanout_candidate_retention_days"
                    f"={days} is below the {_MIN_RETENTION_DAYS}-day floor"
                ),
                changes_made=0,
                metrics={"refused": 1, "retention_days": days},
            )

        try:
            from services.r2_upload_service import R2UploadService

            svc = R2UploadService(site_config=site_config)
            objects = await svc.list_objects(_PREFIX)
        except Exception as exc:  # noqa: BLE001 — a listing failure is a
            # transient/config problem, not a reason to crash the scheduler.
            return JobResult(
                ok=False,
                detail=f"list_objects({_PREFIX}) failed: {describe_exception(exc)}",
                changes_made=0,
                metrics={"listed": 0},
            )

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stale = []
        for obj in objects:
            key = str(obj.get("key") or "")
            lm = obj.get("last_modified")
            # No timestamp => cannot prove it is old => keep. Retention errs
            # toward keeping an image, never toward deleting an unknown.
            if not key.startswith(_PREFIX) or lm is None:
                continue
            if lm.tzinfo is None:
                lm = lm.replace(tzinfo=timezone.utc)
            if lm < cutoff:
                stale.append((key, obj.get("size") or 0))

        stale.sort(key=lambda kv: kv[0])
        capped = stale[:cap]

        deleted = 0
        freed = 0
        failures = 0
        for key, size in capped:
            if not key.startswith(_PREFIX):  # hard guard, per-key, at the
                continue                     # delete site — see docstring.
            try:
                if await svc.delete_object(key):
                    deleted += 1
                    freed += int(size)
                else:
                    failures += 1
            except Exception as exc:  # noqa: BLE001 — one bad key must not
                # abandon the rest of the sweep.
                failures += 1
                logger.warning(
                    "[FANOUT_PRUNE] delete failed for %s (%s)",
                    key, describe_exception(exc),
                )

        remaining = len(stale) - len(capped)
        detail = (
            f"pruned {deleted} candidate image(s) older than {days}d "
            f"({freed / 1_048_576:.1f} MiB freed; {len(objects)} object(s) "
            f"under {_PREFIX})"
        )
        if remaining:
            detail += f"; {remaining} over the {cap}/run cap, next pass continues"
        if failures:
            detail += f"; {failures} delete(s) failed"

        return JobResult(
            ok=failures == 0,
            detail=detail,
            changes_made=deleted,
            metrics={
                "listed": len(objects),
                "stale": len(stale),
                "deleted": deleted,
                "bytes_freed": freed,
                "over_cap": remaining,
                "failures": failures,
                "retention_days": days,
            },
        )
