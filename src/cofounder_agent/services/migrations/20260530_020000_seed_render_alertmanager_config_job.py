"""Migration 20260530_020000_seed_render_alertmanager_config_job: seed dead-man's-switch render job config

Seeds ``plugin.job.render_alertmanager_config`` — the config row for
``RenderAlertmanagerConfigJob`` (Glad-Labs/poindexter#524).

Why
---
The delivery-plane dead-man's switch routes the
``BrainDeliveryDeadMansSwitch`` alert through Alertmanager's NATIVE
Telegram receiver, independent of the brain's Python alert dispatcher.
Alertmanager's config (``alertmanager.yml.tmpl``) carries a
``${ALERTMANAGER_TELEGRAM_CHAT_ID}`` placeholder because the file ships to
the public mirror and a real chat_id must never be committed. This job
renders the template with ``app_settings.telegram_chat_id`` and reloads
Alertmanager — so the config row must exist for the PluginScheduler to
boot the job.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — never clobbers an
operator-tuned value; a re-run on an up-to-date DB is a no-op. Fresh DBs
also get this key from ``brain/seed_app_settings.json``; first writer wins.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEY = "plugin.job.render_alertmanager_config"
_DEFAULT = (
    '{"enabled": true, "interval_seconds": 300, "config": '
    '{"template_path": "/etc/alertmanager/alertmanager.yml.tmpl", '
    '"output_path": "/etc/alertmanager/config/alertmanager.yml", '
    '"alertmanager_url": "http://alertmanager:9093", '
    '"reload_on_change": true}}'
)


async def up(pool) -> None:
    """Insert the render_alertmanager_config job config row if absent."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description)
            VALUES ($1, $2, 'plugins', $3)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY,
            _DEFAULT,
            "Config for RenderAlertmanagerConfigJob (#524) — renders "
            "alertmanager.yml.tmpl with telegram_chat_id and reloads "
            "Alertmanager (delivery-plane dead-man's switch)",
        )
        logger.info(
            "Migration seed_render_alertmanager_config_job: applied (%s)", result,
        )


async def down(pool) -> None:
    """Remove the seeded row when it still holds the seeded default."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1 AND value = $2",
            _KEY,
            _DEFAULT,
        )
        logger.info(
            "Migration seed_render_alertmanager_config_job down: removed default "
            "row (operator-tuned values preserved)"
        )
