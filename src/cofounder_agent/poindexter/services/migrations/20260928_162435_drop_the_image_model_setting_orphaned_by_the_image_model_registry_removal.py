"""Migration 20260928_162435: drop image_model, orphaned when the worker's
image model registry was removed

``image_model`` never chose what the stack renders. Every image comes from the
image-gen HTTP server (``scripts/image-gen-server.py``), which reads
``image_generation_model``, a different key. ``image_model`` belonged to the
worker's in-process diffusers path. Its only reader was
``get_default_image_model`` in ``poindexter.services.image_providers._image_models``.

#4114 and #4157 (both 2026-09-28) deleted that path's last callers, the
``ImageService._initialize_model`` warm-up and the "Strategy 2" fallback tail
of ``_generate_image_impl``. The commit that adds this migration deletes the
resolver with the rest of the registry module (``IMAGE_MODEL_REGISTRY``,
``ImageModelConfig``) and ``ImageService.list_available_models``.
``ImageModel`` stays in ``image_service`` as the type of the deprecated,
ignored ``model=`` parameter.

Verified 2026-09-28, before writing this:

* A quoted-key grep over every tracked file finds no reader. The key appears
  only in its seed and metadata sites (``settings_defaults.DEFAULTS`` +
  ``METADATA``, ``0000_baseline.seeds.sql``,
  ``scripts/settings_defaults_extract.json``), its row in the generated
  ``docs/reference/app-settings.md``, ``StartupManager``'s non-Ollama
  model-key list, the resolver, and tests. All are removed or rewritten in
  this commit. It is not in the free-tier brain seed or ``settings_categories``.
* On prod (read-only) the row holds ``z_image_turbo`` and was last read
  2026-09-23 22:28 UTC. Loki shows what read it: at 22:27:36 the image-gen
  server answered 503 "CUDA out of memory", and the fallback tail logged
  "First generation request detected - initializing z_image_turbo...". The
  value named a model in a log line after the server had already failed.
  That was the key's whole job.

Removing seeded data edits the seed sources too
(``feedback_seed_data_in_baseline_not_new_migrations`` in reverse), and
``scripts/ci/settings_seed_drift_lint.py`` enforces that for every key listed
in ``ORPHANED_KEYS``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Listed explicitly rather than by prefix/LIKE so this can never widen to a key
# that is still live. ``image_generation_model``, the key the image-gen server
# reads, shares the prefix and must survive.
ORPHANED_KEYS = ("image_model",)


async def up(pool) -> None:
    """Delete the orphaned row. No-op on installs that never had it."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration drop_the_image_model_setting_orphaned_by_the_image_model_registry_removal: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the row with the value it was seeded with.

    Structure-only restore: nothing reads this key, so the value is inert and
    re-adding it changes no behaviour. The model the image-gen server renders
    is ``image_generation_model``.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active, value_type)
            VALUES
              ('image_model', 'z_image_turbo', 'media',
               'RETIRED 2026-09-28 -- no reader. It named the model for the '
               'worker''s in-process diffusers path, deleted with its registry '
               'and resolver; it never chose what renders. The image-gen server '
               'reads image_generation_model. Restored by a migration rollback; '
               'safe to delete.',
               false, true, 'model')
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info(
        "Migration drop_the_image_model_setting_orphaned_by_the_image_model_registry_removal: "
        "reverted (%d orphaned key(s) restored)",
        len(ORPHANED_KEYS),
    )
