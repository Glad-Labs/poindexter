"""Migration 20260711_193000: delete 6 more zero-reader orphan app_settings keys.

Batch 2 of the zero-reader retirement. Batch 1
(``20260711_013742_drop_zero_reader_orphan_settings``) dropped 26 keys; because
``ProbeZeroReaderSettingsJob`` reports only the oldest
``settings_zero_reader_max_report`` (50) of a much larger zero-reader pool,
retiring batch 1 simply surfaced the next-oldest slice. A full-repo grep
(``src/`` + ``brain/`` + ``scripts/`` + ``mcp-server*/`` + ``console/`` +
``web/`` + ``skills/`` + ``infrastructure/``) confirmed these 6 are read by
nothing — not via ``SiteConfig``/``SettingsService``, not via
``DatabaseService.get_setting_value`` or a raw-SQL helper, not via the brain
daemon's asyncpg process, not via a prompt template or the frontend.

Grouped by why they're dead:

  * **``staging_mode``** — no longer gates publish. ``routes/task_publishing_routes``
    comments that approval goes live immediately; the setting was "reserved for a
    future scheduling feature" that never materialised. That feature will define
    its own key when built.
  * **``newsletter_email``** — the seed itself marks it "(legacy)". The live
    sender key is ``newsletter_from_email`` (kept — a raw-SQL blind-spot).
  * **``local_database_url``** — the app_settings row is vestigial. The brain DB
    URL resolves from bootstrap.toml / env via ``brain.bootstrap`` (you can't read
    your own DSN out of the DB), and ``config.local_database_url`` /
    ``DatabaseService(local_database_url=...)`` read ``os.getenv("LOCAL_DATABASE_URL")``
    — never this row. (It also parked a Docker-internal DSN in a non-secret row.)
  * **``repo_root``** — the app_settings row is unread. The container path comes
    from the environment; every ``repo_root`` in code is a local ``Path`` variable.
  * **``site_description`` / ``site_tagline``** — redundant metadata. Neither the
    backend nor the Next.js frontend reads them from app_settings (the frontend
    renders its own ``metadata``).

**Explicitly NOT dropped** (flagged by the same probe but verified live or
intentional): the raw-SQL / brain / job-config blind-spots that read-telemetry
can't see (now largely closed by stamping ``admin_db.get_setting`` + the raw
``_get_setting`` helpers in the same change), the intentional operator-identity
keys (``operator_id``, ``gpu_model``, ``privacy_email``, ``social_x_*``), and the
per-operator ``writing_style_reference`` (wired into the writer in a follow-up).

**Why this migration survives the squash.** A baseline only ever
``INSERT ... ON CONFLICT DO NOTHING`` — a no-op on installs that already carry
the rows — so a baseline that simply omits these keys leaves prod carrying them
while fresh installs lack them. This is the convergence step:

  * Fresh installs — the seed sources (``0000_baseline.seeds.sql``,
    ``brain/seed_app_settings.json``; none are in ``settings_defaults.DEFAULTS``)
    no longer emit these rows, so the DELETE matches nothing (no-op).
  * Existing installs (prod, seeded from the pre-removal baseline) — this
    performs the real delete.

Same one-way posture as ``20260711_013742_drop_zero_reader_orphan_settings`` and
``20260623_202131_drop_dead_model_settings``. The next squash can fold this away
once every install has run it.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEAD_KEYS = (
    "staging_mode",
    "newsletter_email",
    "local_database_url",
    "repo_root",
    "site_description",
    "site_tagline",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_DEAD_KEYS),
        )
    logger.info(
        "drop_zero_reader_orphan_settings_batch2 up: removed %d zero-reader keys (%s)",
        len(_DEAD_KEYS),
        result,
    )


async def down(_pool) -> None:
    # No-op: these keys are retired zero-reader orphans with no reader to
    # restore. Re-seeding them would only reintroduce set-but-never-read config.
    # Same one-way posture as 20260711_013742_drop_zero_reader_orphan_settings.
    logger.info(
        "drop_zero_reader_orphan_settings_batch2 down: no-op "
        "(refusing to re-seed retired zero-reader keys)"
    )
