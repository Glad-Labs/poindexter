"""Migration 20260527_024058: silence openclaw_gateway_url probe pending upstream fix

Why: ``brain.operator_url_probe`` checks ``openclaw_gateway_url``
(seeded to ``http://localhost:18789``) every 15 minutes. Glad-Labs/
glad-labs-stack#594 documents that the upstream OpenClaw CLI's
``gateway start`` has a false-positive port-busy check that prevents
the host-side watchdog from restoring service. As of 2026-05-27 the
gateway is genuinely down on the host (no process bound to 18789,
no openclaw.exe in tasklist), and the probe has logged 46
``operator_paged`` rows in 24 h with no recovery path.

This migration appends ``openclaw_gateway_url`` to
``operator_url_probe_skip_keys`` so the probe stops alerting until
upstream OpenClaw ships a fix. Once #594 is closed, remove this
entry via a follow-up migration.

Idempotent: the skip-keys field is a comma-separated string. The
update only writes when the key isn't already present.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SKIP_KEY_TO_ADD = "openclaw_gateway_url"


async def up(pool) -> None:
    """Append ``openclaw_gateway_url`` to ``operator_url_probe_skip_keys``."""
    async with pool.acquire() as conn:
        current = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'operator_url_probe_skip_keys'",
        )
        if current is None:
            logger.info(
                "Migration 20260527_024058: operator_url_probe_skip_keys row "
                "missing — skipping (probe must not be seeded on this DB)",
            )
            return

        keys = [k.strip() for k in current.split(",") if k.strip()]
        if _SKIP_KEY_TO_ADD in keys:
            logger.info(
                "Migration 20260527_024058: %s already in skip list — no-op",
                _SKIP_KEY_TO_ADD,
            )
            return

        keys.append(_SKIP_KEY_TO_ADD)
        keys.sort()  # match the existing-row convention of alphabetised entries
        new_value = ",".join(keys)
        await conn.execute(
            "UPDATE app_settings SET value = $1::text WHERE key = "
            "'operator_url_probe_skip_keys'",
            new_value,
        )
        logger.info(
            "Migration 20260527_024058: appended %s to operator_url_probe_skip_keys "
            "(pending Glad-Labs/glad-labs-stack#594 upstream OpenClaw fix)",
            _SKIP_KEY_TO_ADD,
        )


async def down(pool) -> None:
    """Remove ``openclaw_gateway_url`` from the skip list."""
    async with pool.acquire() as conn:
        current = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'operator_url_probe_skip_keys'",
        )
        if current is None:
            return

        keys = [k.strip() for k in current.split(",") if k.strip()]
        if _SKIP_KEY_TO_ADD not in keys:
            return

        keys = [k for k in keys if k != _SKIP_KEY_TO_ADD]
        await conn.execute(
            "UPDATE app_settings SET value = $1::text WHERE key = "
            "'operator_url_probe_skip_keys'",
            ",".join(keys),
        )
        logger.info(
            "Migration 20260527_024058 down: removed %s from skip list",
            _SKIP_KEY_TO_ADD,
        )
