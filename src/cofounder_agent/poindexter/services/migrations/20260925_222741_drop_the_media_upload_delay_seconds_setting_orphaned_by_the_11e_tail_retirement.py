"""Migration 20260925_222741: drop media_upload_delay_seconds, orphaned by the
11e tail retirement

``media_upload_delay_seconds`` outlived its one reader.

It gated a single call site: ``publish_service._upload_media_to_r2_bg`` (phase
"11e" on the immediate-publish tail) slept this many seconds, then uploaded
``~/.poindexter/{podcast,video}/{post_id}.{mp3,mp4}`` to R2 and re-rendered
both RSS feeds. That function is deleted in this same change — measured
2026-09-25, it had done no work in production in at least 30 days:

* It is spawned from exactly one place, the immediate-publish tail of
  ``publish_post_from_task``, gated by ``_should_run_post_publish_hooks()``.
  The default operator flow since ~2026-06-24 is approve -> ``stage_only`` ->
  promote, and the promote short-circuit (``_promote_or_skip_existing``)
  never reaches that tail. Neither does ``fire_post_distribution_hooks``
  (gate-clear), ``publish_now``, nor the ``scheduled_publisher`` promote loop.
  Loki, 30 days: 20 posts created (19 via ``stage_only``, 1 via the tail),
  zero of the tail's own log lines.
* Even on the one path that does reach it (a Prefect auto-publish flow
  subprocess, or the CLI publish command), ``DatabaseService.close()`` drains
  background tasks with a 30s timeout -- which cancelled it mid-sleep, before
  any upload, every time.
* Its file-naming convention was also stale on both media types: every file
  under ``~/.poindexter/podcast/`` and ``~/.poindexter/video/`` on the
  operator box is task-keyed (``{task_id}.mp3`` / ``{task_id}[_short].mp4``),
  never post-keyed, because podcast delivery has been task-keyed since the
  #884 dedup cutover and video since the #1460 one-video-per-post cutover.
  Even a live 11e would have found nothing to upload.

The two feed rebuilds it also did are fully covered by three still-live
event-coupled triggers (``media_approval_service.decide`` on approve,
``podcast_distribute`` Pass 3, ``media_distribute`` on video dispatch) plus
the ``media_feed_reconciliation`` 15-minute convergence watchdog, which
renders each feed from the DB and republishes on drift regardless of which
event fired -- 26 successful ``RSS feed rebuilt on R2`` lines from those
callers in the same 30-day window this key's one reader produced zero.

Nothing else reads this key: not the two R2UploadService methods
(``upload_podcast_episode`` / ``upload_video_episode``, deleted alongside the
tail -- they only ever fired from it), not any job, not any route. It is in
none of the free-tier brain seed's 80 keys, so this migration plus the
``settings_defaults.py`` / ``0000_baseline.seeds.sql`` edits in the same
commit are the whole fix (``feedback_seed_data_in_baseline_not_new_migrations``
in reverse -- removing seeded data edits the seed sources too, or the
every-boot ``INSERT ... ON CONFLICT DO NOTHING`` resurrects the row).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Listed explicitly rather than by prefix/LIKE so this can never widen to a key
# that is still live.
ORPHANED_KEYS = ("media_upload_delay_seconds",)


async def up(pool) -> None:
    """Delete the orphaned row. No-op on installs that never had it."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration drop_the_media_upload_delay_seconds_setting_orphaned_by_the_11e_tail_retirement: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the row with the value it held at retirement.

    Structure-only restore: no code reads this key on either side of the
    migration once the 11e tail is gone, so the value is inert. Re-adding it
    is a no-op for behaviour -- there is nothing left to re-wire it to.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            VALUES
              ('media_upload_delay_seconds', '240', 'general',
               'RETIRED 2026-09-25 -- no reader. Its only call site, '
               'publish_service._upload_media_to_r2_bg (the "11e" '
               'immediate-publish tail), was deleted in the same change: it '
               'was unreachable from the default approve->stage->promote '
               'flow, and its file-naming convention no longer matched '
               'anything the pipeline produces. Restored by a migration '
               'rollback; safe to delete.',
               false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info(
        "Migration drop_the_media_upload_delay_seconds_setting_orphaned_by_the_11e_tail_retirement: "
        "reverted (%d orphaned key(s) restored)",
        len(ORPHANED_KEYS),
    )
