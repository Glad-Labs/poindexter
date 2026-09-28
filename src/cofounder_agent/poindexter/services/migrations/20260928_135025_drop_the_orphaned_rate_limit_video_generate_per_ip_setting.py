"""Migration 20260928_135025: drop rate_limit_video_generate_per_ip, orphaned
when #2254 deleted POST /api/video/generate/{post_id}

``rate_limit_video_generate_per_ip`` has had no reader since 2026-07-10.

It was the slowapi limit on ``POST /api/video/generate/{post_id}``, one of the
five expensive endpoints #1439 (poindexter#748, 2026-06-11) put behind
settings-driven limits. That route was the manual operator trigger for the
legacy :9837 host-slideshow server. #2254 (64d41fa40, "retire the legacy :9837
host-slideshow lane") deleted it together with its
``@limiter.limit(_settings_limit("rate_limit_video_generate_per_ip", ...))``
decorator, the key's only read. #2254 retired ``video_server_url`` with its own
migration (``20260710_193000``, since folded into the Phase G baseline) but
missed this key, whose one seed line in
``settings_defaults.DEFAULTS`` stayed behind; #2967 then derived a METADATA
entry for it with no ``owner``, because no module claims it.
``poindexter/routes/video_routes.py`` now serves only ``GET /api/video/feed.xml``
(the ``/api/video/episodes`` routes went in Glad-Labs/poindexter#1087).

Verified 2026-09-28, before writing this:

* A grep for the key across every tracked file finds no reader: no Python, SQL,
  console JS, MCP server, Grafana panel or doc. The only hits were its own
  ``DEFAULTS`` line and ``METADATA`` entry in ``settings_defaults.py``, both
  removed in this commit. Nothing assembles the name dynamically either: no
  ``video_generate`` fragment and no computed ``rate_limit_`` name appears
  anywhere.
* On prod (read-only) the row still holds its seeded ``5/minute``, with
  ``updated_at`` equal to its 2026-06-12 ``created_at`` (never tuned) and a
  NULL ``last_read_at``. That corroborates the grep but cannot stand in for it
  here: ``_settings_limit`` reads its key only when a request reaches the
  decorated route, so a live limit on a rarely-hit route reads NULL too. The
  podcast twin below is NULL on prod, while ``rate_limit_token_per_ip``, read on
  every token mint, was stamped the same day.

``rate_limit_podcast_generate_per_ip`` looks like this key's twin and is LIVE:
``podcast_routes.py`` limits ``POST /api/podcast/generate/{post_id}`` through
``_settings_limit``. It is not touched.

Unlike its precedent (``short_video_post_publish_delay_seconds``, seeded only by
the baseline), this key was only ever seeded by ``settings_defaults.DEFAULTS``.
Neither ``0000_baseline.seeds.sql`` nor the free-tier brain seed carries it, and
neither does ``scripts/settings_defaults_extract.json``. So this migration plus the
``settings_defaults.py`` edit in the same commit are the whole fix:
``seed_all_defaults`` runs on every boot with ``ON CONFLICT DO NOTHING``, so a
``DEFAULTS`` line left behind would re-insert the row on the first boot after
this DELETE. ``scripts/ci/settings_seed_drift_lint.py`` counts ``DEFAULTS`` as
a seed source from this commit on (it used to exempt it), so it now fails CI
if the key is re-seeded while this migration still deletes it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Listed explicitly rather than by prefix/LIKE so this can never widen to a key
# that is still live: every other ``rate_limit_*`` key has a reader.
ORPHANED_KEYS = ("rate_limit_video_generate_per_ip",)


async def up(pool) -> None:
    """Delete the orphaned row. No-op on installs that never had it."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration drop_the_orphaned_rate_limit_video_generate_per_ip_setting: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the row with the value and category it was seeded with.

    Structure-only restore: nothing has read this key since #2254 deleted
    ``POST /api/video/generate/{post_id}``, so the value is inert. Re-adding it
    changes no behaviour; there is no route left to limit.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active, value_type)
            VALUES
              ('rate_limit_video_generate_per_ip', '5/minute', 'infrastructure',
               'RETIRED 2026-09-28 -- no reader. Its only call site, the '
               'slowapi limit on POST /api/video/generate/{post_id}, was '
               'deleted by #2254 (2026-07-10) with the legacy :9837 '
               'host-slideshow lane. Restored by a migration rollback; safe '
               'to delete.',
               false, true, 'string')
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info(
        "Migration drop_the_orphaned_rate_limit_video_generate_per_ip_setting: "
        "reverted (%d orphaned key(s) restored)",
        len(ORPHANED_KEYS),
    )
