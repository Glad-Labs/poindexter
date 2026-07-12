"""Migration 20260712_211523: drop dead cosyvoice2 tts app_settings

ISSUE: Glad-Labs/poindexter#845

CosyVoice2 was fully removed by #2355 (chore(tts): remove CosyVoice2 bake-off
sidecar) — the provider class, sidecar server, both compose blocks, and its
four ``app_settings`` defaults were deleted from ``settings_defaults.DEFAULTS``.

Why a forward-only migration is needed: removing keys from the seed dict only
stops *re-seeding* them going forward (``seed_all_defaults`` uses
``INSERT ... ON CONFLICT DO NOTHING``) — it doesn't retroactively delete rows
an existing install already carries, and the squashed baseline can only
``CREATE ... IF NOT EXISTS`` (add, never drop). Same pattern as #844
(``20260712_203614_drop_dead_model_app_settings.py``).

Net effect on existing installs: the brain daemon's ``operator_url_probe``
auto-probes every ``app_settings`` key ending in ``_url``
(``collect_app_setting_urls``) and kept finding
``plugin.tts_provider.cosyvoice2.base_url`` pointing at
``http://cosyvoice2:8000/v1`` — a container that no longer exists — paging a
weekly-ish ``ConnectTimeout`` warning for a service intentionally retired.

Idempotent: deleting absent rows is a no-op, and the keys are gone from every
seed source (``0000_baseline.seeds.sql`` / ``brain/seed_app_settings.json``)
and from ``settings_defaults.DEFAULTS``, so nothing re-seeds them on boot and
the removal sticks (guarded by ``scripts/ci/settings_seed_drift_lint.py`` and
``tests/unit/services/migrations/test_drop_dead_cosyvoice2_settings.py``).

NOTE: the CI ``migrations-smoke`` job runs against a throwaway service-container
DB, not prod — it validates this DELETE applies cleanly to a fresh install but
does NOT touch the live rows. Prod only picks up this migration at the next
worker boot/deploy (``StartupManager._run_migrations``), same as any other
migration (see ``reference_migrations_smoke_hits_live_db`` — a local
``migrations_smoke.py`` run against ``bootstrap.toml``'s prod DSN is the one
thing that WOULD apply it early, so don't run it locally for this file).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Delete the four retired cosyvoice2 TTS settings from existing installs.

    Idempotent — ``DELETE`` of absent rows is a no-op, and nothing re-seeds
    these keys, so the removal is durable.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key IN ("
            "'plugin.tts_provider.cosyvoice2.base_url', "
            "'plugin.tts_provider.cosyvoice2.model', "
            "'plugin.tts_provider.cosyvoice2.instruct', "
            "'plugin.tts_provider.cosyvoice2.timeout_s'"
            ")"
        )
    logger.info("Migration drop_dead_cosyvoice2_tts_app_settings: applied (%s)", result)


async def down(pool) -> None:
    """One-way migration — intentionally not reverted.

    These keys have no production reader (the provider class and sidecar are
    deleted) and are absent from every seed source, so re-creating them on
    rollback would only re-introduce dead rows pointing at a container that
    no longer exists. Leave them gone.
    """
    return
