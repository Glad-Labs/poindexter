"""Migration 20260928_140518: drop enable_image_gen_warmup, orphaned when
the startup image-gen warmup was deleted

``enable_image_gen_warmup`` had one reader: ``StartupManager._warmup_image_models``
in ``poindexter/utils/startup_manager.py``, deleted in the same commit as this
migration. That step could never do useful work, for three independent reasons:

* It read the key before the DB was loaded. ``main.py``'s lifespan runs
  ``startup_manager.initialize_all_services()`` (which contains the warmup)
  ahead of ``_site_cfg.load(pool)``, and that order has held since at least
  2026-06-25. So the SiteConfig it asked held env + code defaults only, and the
  stored value was invisible. Prod set the row to ``'true'`` on 2026-06-26, and
  every worker boot since has still logged "image-gen warmup: Skipped" (three
  boots on 2026-09-28 alone). Only an ``ENABLE_IMAGE_GEN_WARMUP`` env var could
  switch it on.
* Past the flag it required ``torch.cuda.is_available()`` in the worker.
  ``pyproject.toml`` pins torch to the ``pytorch-cpu`` wheel source (prod runs
  ``2.14.0+cpu``, pulled in by sentence-transformers), so a stock install
  returned there too.
* It warmed nothing. Renders happen in the image-gen HTTP server, which
  lazy-loads its model on the first ``/generate`` and unloads it once idle
  (``IDLE_TIMEOUT``, 60 s in ``scripts/image-gen-server.py``). A startup render
  would only have taken ``gpu.lock("image_gen")`` and evicted Ollama on every
  worker restart.

Verified 2026-09-28, before writing this:

* A grep over every tracked file finds the key only in its seed and metadata
  sites (``settings_defaults.DEFAULTS`` + ``METADATA``, ``settings_categories``,
  ``0000_baseline.seeds.sql``, ``scripts/settings_defaults_extract.json``), its
  row in the generated ``docs/reference/app-settings.md``, and the warmup
  itself. All are removed in this commit. It is not in the free-tier brain seed.
* On prod the row's ``last_read_at`` IS stamped (2026-09-28 13:43), unlike
  #4111's orphan. That is not a live reader: ``SiteConfig.get`` records a read
  wherever it resolves, and this one resolved to the empty code default before
  the DB load. The stamp is the pre-load read described above.

Removing seeded data edits the seed sources too
(``feedback_seed_data_in_baseline_not_new_migrations`` in reverse), which
``scripts/ci/settings_seed_drift_lint.py`` enforces for every key this file
lists in ``ORPHANED_KEYS``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Listed explicitly rather than by prefix/LIKE so this can never widen to a key
# that is still live.
ORPHANED_KEYS = ("enable_image_gen_warmup",)


async def up(pool) -> None:
    """Delete the orphaned row. No-op on installs that never had it."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration drop_the_enable_image_gen_warmup_setting_orphaned_by_the_warmup_removal: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the row with the value it was seeded with.

    Structure-only restore: the warmup step that read this key is gone, so the
    value is inert. Re-adding it is a no-op for behaviour.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            VALUES
              ('enable_image_gen_warmup', '', 'media',
               'RETIRED 2026-09-28 -- no reader. Its only reader, '
               'StartupManager._warmup_image_models, was deleted: it read the key '
               'before the DB load, and the image-gen server unloads its model '
               'when idle, so a startup render warmed nothing. Restored by a '
               'migration rollback; safe to delete.',
               false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info(
        "Migration drop_the_enable_image_gen_warmup_setting_orphaned_by_the_warmup_removal: "
        "reverted (%d orphaned key(s) restored)",
        len(ORPHANED_KEYS),
    )
