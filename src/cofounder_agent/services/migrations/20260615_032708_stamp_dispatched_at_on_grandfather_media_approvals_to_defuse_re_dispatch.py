"""Migration 20260615_032708: stamp dispatched_at on grandfather media_approvals to defuse re-dispatch

INCIDENT: 2026-06-15 grandfather-video YouTube re-upload — already-live videos
blessed ``approved`` *without* ``dispatched_at`` were re-delivered by the upload
jobs (9 long videos, 7 with bare metadata).

The grandfather migrations (``decided_by`` ``auto:grandfather`` /
``migration:grandfather``) bless ALREADY-LIVE media as
``media_approvals.status='approved'`` so a newly-gated RSS feed doesn't
freeze them. That verdict is correct for the *feed* gate — which reads only
``status`` — but it leaves those rows ``dispatched_at IS NULL``. The *upload*
dispatchers read a different shape: ``backfill_videos`` (disk-glob
``VIDEO_DIR/{post_id}.mp4`` + ``is_approved``) and ``media_distribute`` (the
asset pass) both treat ``status='approved' AND dispatched_at IS NULL`` as
"approved but never delivered → upload now". So grandfathering already-public
long videos re-uploaded them to YouTube — the 2026-06-15 mass-upload of ~9
videos with stale metadata.

This backfill closes the conflation for the data already in flight: it stamps
``dispatched_at`` + ``dispatch_success=true`` on every ``approved`` grandfather
row that is still ``dispatched_at IS NULL`` — i.e. declares the already-live
media "distribution handled" so no dispatcher re-sends it.

Scope at apply time (prod, surveyed read-only): the long-form ``video``
grandfather rows already detonated (all ``dispatched_at`` set), so
``dispatched_at IS NULL`` excludes them — this is a no-op on them. It stamps
the 59 ``podcast`` + 59 ``video_short`` ``migration:grandfather`` rows that
are still undispatched (podcast's dispatcher is dormant and video_short never
matched an asset, so neither has fired — but both are armed the same way the
video rows were). Deliberate ``operator:bulk-backfill`` queueing is left
untouched (the ``LIKE '%grandfather%'`` filter excludes it) so a future
intentional backlog push still works.

Idempotent: ``COALESCE`` never overwrites an existing ``dispatched_at`` /
``dispatch_success``, and the ``dispatched_at IS NULL`` predicate means a
second run matches 0 rows. Safe on a fresh migrations-smoke DB: no rows → 0
updated.

One-way backfill: ``down()`` is a deliberate no-op. Un-stamping would re-arm
the re-dispatch landmine this migration exists to defuse, and we cannot
distinguish rows stamped here from rows legitimately dispatched afterward.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFUSE_SQL = """
UPDATE media_approvals
   SET dispatched_at    = COALESCE(dispatched_at, NOW()),
       dispatch_success = COALESCE(dispatch_success, TRUE)
 WHERE status = 'approved'
   AND dispatched_at IS NULL
   AND decided_by LIKE '%grandfather%'
"""


async def up(pool) -> None:
    """Stamp already-live grandfather media as distribution-handled."""
    async with pool.acquire() as conn:
        result = await conn.execute(_DEFUSE_SQL)
    logger.info(
        "Migration stamp_dispatched_at_on_grandfather_media_approvals: %s", result,
    )


async def down(pool) -> None:
    """No-op: this is a one-way backfill.

    Un-stamping ``dispatched_at`` would re-arm the re-dispatch landmine this
    migration exists to defuse, and we cannot tell rows stamped by this
    backfill apart from rows legitimately dispatched afterward. Intentionally
    does nothing (no ``pool.acquire``/``execute``).
    """
    logger.info(
        "Migration stamp_dispatched_at_on_grandfather_media_approvals down: "
        "no-op (one-way backfill)",
    )
