"""Shared helpers for the media/podcast dispatch lanes.

Two things the ``media_distribute`` and ``podcast_distribute`` jobs both need:

1. **Single-flight claim** (:func:`claim_media_dispatch`) — a per-``(post, medium)``
   advisory-lock guard that serializes the recheck→upload→stamp critical section
   so two overlapping dispatch passes can never both perform the *irreversible*
   upload of the same asset. See that function's docstring for why this is not a
   purely-hypothetical multi-worker concern.

2. **External-handle capture+persist** (:func:`persist_platform_handles`) — on a
   successful video upload, capture the platform's external handles: the video id
   is merged into ``media_assets.platform_video_ids`` (a non-clobbering jsonb
   ``||`` merge) and a ``pipeline_distributions`` row is upserted
   (``status='published'``) for observability + re-upload dedupe. Both writes are
   scoped by ``medium``, so the long form and the Short of one post land as two
   rows rather than one overwriting the other. This module single-sources the
   :class:`PlatformDispatchResult` value type, the two SQL statements, and the
   per-platform merge+insert loop.

   (The handle-capture piece was originally extracted after #1584 / #1601 to
   de-duplicate byte-identical copies shared with the post-keyed
   ``backfill_videos`` disk-scan job; ``backfill_videos`` was retired in #1460,
   leaving ``media_distribute`` the sole caller.)

The caller keeps its own thin ``_persist_dispatch_result`` wrapper because it
stamps ``record_dispatched`` in its own transaction. :func:`persist_platform_handles`
therefore does the merge+insert loop ONLY (no ``record_dispatched``); the caller
owns the transaction and the dispatch stamp.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Advisory-lock namespace for the media/podcast dispatch critical section. Two
# dispatch passes both SELECT the same ``approved AND dispatched_at IS NULL``
# asset and both perform the irreversible upload (a duplicate YouTube video for
# ``media_distribute``; a wasted re-upload + double stamp for ``podcast_distribute``).
# ``pg_try_advisory_lock(NS, hashtext("<post_id>:<medium>"))`` serializes the whole
# recheck→upload→stamp section cluster-wide — advisory locks span connections AND
# workers, so this holds both for a single worker (these jobs are ``idempotent``,
# which the scheduler maps to ``max_instances=3``, so a cycle that runs past its
# 10-min interval overlaps itself) and for the multi-worker SaaS future. A stable
# arbitrary int32, distinct from ``gpu_scheduler.GPU_ADVISORY_LOCK_KEY`` and
# ``social_drafts._SOCIAL_POST_LOCK_NS`` (0x50AC). Mirrors the #3370 approve_draft fix.
_MEDIA_DISPATCH_LOCK_NS: int = 0x4D44  # "MD" — media dispatch critical section

# Re-check, under the lock, that this ``(post, medium)`` is STILL eligible for
# dispatch. The batch that surfaced the row was SELECTed BEFORE the lock was held,
# so a concurrent pass that already dispatched it (then released the lock) leaves a
# stale row in our batch — uploading it again would duplicate. Same "re-read inside
# the lock" shape as ``social_drafts._approve_draft_locked``.
_STILL_UNDISPATCHED_SQL = """
    SELECT 1
      FROM media_approvals
     WHERE post_id = $1::uuid
       AND medium = $2
       AND status = 'approved'
       AND dispatched_at IS NULL
     LIMIT 1
"""


@contextlib.asynccontextmanager
async def claim_media_dispatch(
    pool: Any, *, post_id: str, medium: str,
) -> AsyncIterator[bool]:
    """Single-flight guard around one irreversible media dispatch.

    Yields ``True`` when the caller holds an exclusive claim on this
    ``(post_id, medium)`` and should proceed with the upload+stamp, or ``False``
    when it must skip WITHOUT uploading — either because another pass holds the
    lock right now (``pg_try_advisory_lock`` is non-blocking: the loser bails
    rather than waiting on a minutes-long upload) or because a concurrent pass
    already dispatched this asset between the batch SELECT and now (the re-check).

    The advisory lock is released on ``__aexit__`` (including when the caller
    ``continue``\\ s or raises), and a dropped connection releases it too — so a
    crash mid-upload can't wedge the row; it stays ``dispatched_at IS NULL`` and
    is retried next cycle, exactly as before this guard existed.

    **Why this matters even single-worker.** These jobs declare
    ``idempotent = True``, which the scheduler maps to apscheduler
    ``max_instances=3`` (``plugins/scheduler.py``). A dispatch cycle that runs
    past its 10-minute interval — plausible when a backlog of up to
    ``media_distribute_max_per_cycle`` videos uploads sequentially — overlaps the
    next fire, and without this guard both instances SELECT the same
    ``approved AND dispatched_at IS NULL`` rows (no ``FOR UPDATE SKIP LOCKED``)
    and both upload. Mirrors the ``approve_draft`` fix (glad-labs-stack#3370).

    A mock/degraded pool whose ``pg_try_advisory_lock`` returns ``None`` (not a
    real Postgres bool) is treated as "no lock backend — proceed", never as
    contention: only a literal ``False`` means another session holds it.
    """
    lock_key = f"{post_id}:{medium}"
    async with pool.acquire() as conn:
        got = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1, hashtext($2))",
            _MEDIA_DISPATCH_LOCK_NS,
            lock_key,
        )
        if got is False:
            logger.info(
                "[MEDIA_DISPATCH] post %s (%s) already being dispatched by "
                "another pass (lock contended) — skipping to avoid a duplicate "
                "upload",
                post_id, medium,
            )
            yield False
            return
        try:
            still = await conn.fetchval(_STILL_UNDISPATCHED_SQL, post_id, medium)
            if not still:
                logger.info(
                    "[MEDIA_DISPATCH] post %s (%s) already dispatched by a "
                    "concurrent pass — skipping (re-check under lock)",
                    post_id, medium,
                )
                yield False
            else:
                yield True
        finally:
            # We only reach here when the lock was acquired (got is not False),
            # so an unlock is always owed. Best-effort — a dropped connection
            # frees the session lock anyway, so a failure here can't wedge it.
            try:
                await conn.fetchval(
                    "SELECT pg_advisory_unlock($1, hashtext($2))",
                    _MEDIA_DISPATCH_LOCK_NS,
                    lock_key,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[MEDIA_DISPATCH] advisory-unlock for post %s (%s) failed "
                    "(lock frees on disconnect)",
                    post_id, medium, exc_info=True,
                )


@dataclass(frozen=True)
class PlatformDispatchResult:
    """Outcome of delivering one asset to one video platform.

    A dispatch helper returns one of these per enabled adapter so the caller can
    both decide the aggregate dispatch outcome (``record_dispatched``) AND persist
    the platform's external handles (``external_id`` / ``url``). The upload handler
    returns the external video id under its ``post_id`` key and the public watch
    URL under ``url`` (see
    ``services/integrations/handlers/publishing_youtube.py``); they're captured
    here under clearer names. ``external_id`` is ``None`` on failure.
    """

    platform: str
    success: bool
    external_id: str | None = None
    url: str | None = None


# Merge one platform's external video id into the asset's platform_video_ids
# jsonb. The ``||`` concat operator is a shallow merge, so {"youtube": "<id>"}
# replaces only the youtube key and leaves any other platform's id intact —
# the asset can be cross-posted without one delivery clobbering another's id.
_MERGE_PLATFORM_VIDEO_ID_SQL = """
    UPDATE media_assets
       SET platform_video_ids = platform_video_ids || $2::jsonb,
           updated_at = NOW()
     WHERE id = $1::uuid
"""

# Record the external delivery for observability + dedupe. ``task_id`` is the
# media_assets task key (== posts.metadata->>'pipeline_task_id', the FK target
# for pipeline_distributions).
#
# **``medium`` is part of the key, and that is the whole point.** One task
# produces BOTH a long-form ``video`` and a ``video_short`` and dispatches both
# to ``target='youtube'``; under the old ``(task_id, target)`` key the second
# upsert overwrote the first and one YouTube handle was silently lost — five of
# the twelve rows on prod, unreachable by ``youtube_metadata_sync`` ever since
# (migration 20260901_173133). Keying on the medium instead of the external id
# keeps the "same render updates in place" contract: a re-dispatch mints a NEW
# video id but the same medium, so it refreshes its row rather than appending a
# second one that claims the first is still live.
_RECORD_DISTRIBUTION_SQL = """
    INSERT INTO pipeline_distributions
        (task_id, target, medium, status, external_id, external_url, post_id, published_at)
    VALUES ($1, $2, $3, 'published', $4, $5, $6::uuid, NOW())
    ON CONFLICT (task_id, target, medium) DO UPDATE SET
        status       = EXCLUDED.status,
        external_id  = COALESCE(EXCLUDED.external_id, pipeline_distributions.external_id),
        external_url = COALESCE(EXCLUDED.external_url, pipeline_distributions.external_url),
        post_id      = COALESCE(EXCLUDED.post_id, pipeline_distributions.post_id),
        published_at = COALESCE(EXCLUDED.published_at, pipeline_distributions.published_at)
"""


async def persist_platform_handles(
    conn: Any,
    *,
    post_id: str,
    medium: str,
    asset_id: str | None,
    task_id: str | None,
    results: list[PlatformDispatchResult],
) -> None:
    """Persist each successful platform's external handles on an open connection.

    The per-platform merge+insert loop shared by both video-dispatch jobs. For
    each successful result carrying an ``external_id`` we:

    1. merge ``{platform: external_id}`` into ``media_assets.platform_video_ids``
       (shallow ``||`` merge — never clobbers another platform's id) when an
       ``asset_id`` is known, and
    2. upsert a ``pipeline_distributions`` row (``status='published'``) carrying
       ``external_id`` / ``external_url`` when a ``task_id`` is known (it's the
       NOT NULL FK key); the row is logged-and-skipped otherwise.

    ``medium`` is which render this dispatch delivered (``video`` /
    ``video_short`` / ``podcast`` — the ``media_approvals.medium`` vocabulary the
    caller already holds). It is part of the distribution row's unique key, so a
    post's long form and its Short each get their own row instead of the second
    overwriting the first; see :data:`_RECORD_DISTRIBUTION_SQL`.

    Results that failed or carry no ``external_id`` are skipped. This is the loop
    ONLY — the caller owns the transaction and the ``record_dispatched`` stamp,
    because the two jobs stamp it differently and source ``(asset_id, task_id)``
    differently. ``conn`` is an already-acquired connection so all writes share
    the caller's transaction.
    """
    for r in results:
        if not (r.success and r.external_id):
            continue
        if asset_id:
            await conn.execute(
                _MERGE_PLATFORM_VIDEO_ID_SQL,
                asset_id,
                json.dumps({r.platform: r.external_id}),
            )
        if task_id:
            await conn.execute(
                _RECORD_DISTRIBUTION_SQL,
                task_id, r.platform, medium, r.external_id, r.url, post_id,
            )
        else:
            logger.warning(
                "%s delivered post %s (%s, external_id=%s) but no task_id — "
                "skipping pipeline_distributions row",
                r.platform, post_id, medium, r.external_id,
            )


__all__ = [
    "PlatformDispatchResult",
    "claim_media_dispatch",
    "persist_platform_handles",
    "_MEDIA_DISPATCH_LOCK_NS",
    "_MERGE_PLATFORM_VIDEO_ID_SQL",
    "_RECORD_DISTRIBUTION_SQL",
    "_STILL_UNDISPATCHED_SQL",
]
