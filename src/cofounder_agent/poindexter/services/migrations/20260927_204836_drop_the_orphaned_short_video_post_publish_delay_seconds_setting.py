"""Migration 20260927_204836: drop short_video_post_publish_delay_seconds,
orphaned when the 11d short-video hook left the publish path

``short_video_post_publish_delay_seconds`` has had no reader since 2026-06-01.

It gated a single call site: the ``_gen_short`` coroutine that phase "11d" of
``publish_service.publish_post_from_task`` spawned once a post published. It
slept this many seconds -- "lets podcast finish first", per its seed
description -- then called ``video_service.generate_short_video_for_post``.
#893 (ce92f5326, media-gated publish) deleted phases 11b/c/d together:
podcast, video and short generation stopped being post-publish hooks, and
nothing on the publish path has scheduled media since. The
``# 11b/c/d. Derived media (podcast / video / short) — REMOVED.`` comment in
``publish_service.py`` marks the spot. The one ``_sc.get`` of this key went
with that block; the seed row stayed behind.

Verified 2026-09-27, before writing this:

* A grep for the key across every tracked file finds no reader -- no Python,
  SQL, console JS, MCP server or Grafana panel. The only hits were this row's
  own seed and its line in the generated ``docs/reference/app-settings.md``
  (both removed in this commit) plus a historical CHANGELOG entry. #4086 had
  already dropped the stale "Configuration" bullet in
  ``docs/architecture/services/publish_service.md`` that listed it as a
  publish_service read. Nothing assembles the name dynamically either: no
  ``post_publish_delay`` fragment appears anywhere.
* On prod (read-only) the row still holds its seeded ``180`` with a NULL
  ``last_read_at`` and an ``updated_at`` of 2026-04-10, while live keys beside
  it (``video_feed_name``, ``newsletter_batch_delay_seconds``) were stamped in
  the last two days. ``last_read_at`` cannot see raw-SQL reads, so this
  corroborates the grep rather than replacing it.

It is in neither ``settings_defaults.py`` nor the free-tier brain seed's 80
keys, so this migration plus the ``0000_baseline.seeds.sql`` edit in the same
commit are the whole fix. Removing seeded data edits the seed source too
(``feedback_seed_data_in_baseline_not_new_migrations`` in reverse), which
``scripts/ci/settings_seed_drift_lint.py`` enforces for every key this file
lists in ``ORPHANED_KEYS``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Listed explicitly rather than by prefix/LIKE so this can never widen to a key
# that is still live.
ORPHANED_KEYS = ("short_video_post_publish_delay_seconds",)


async def up(pool) -> None:
    """Delete the orphaned row. No-op on installs that never had it."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration drop_the_orphaned_short_video_post_publish_delay_seconds_setting: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the row with the value it was seeded with.

    Structure-only restore: no code has read this key since #893 removed the
    11d short-video hook, so the value is inert. Re-adding it is a no-op for
    behaviour -- there is nothing left to re-wire it to.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            VALUES
              ('short_video_post_publish_delay_seconds', '180', 'general',
               'RETIRED 2026-09-27 -- no reader. Its only call site, the '
               '"11d" short-video hook in publish_service.publish_post_from_task, '
               'was deleted by #893 (2026-06-01), when podcast/video/short '
               'generation left the publish path. Restored by a migration '
               'rollback; safe to delete.',
               false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info(
        "Migration drop_the_orphaned_short_video_post_publish_delay_seconds_setting: "
        "reverted (%d orphaned key(s) restored)",
        len(ORPHANED_KEYS),
    )
