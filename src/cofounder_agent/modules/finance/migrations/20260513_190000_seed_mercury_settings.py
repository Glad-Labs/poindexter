"""Seed the Mercury app_settings keys.

FinanceModule F1 migration #1 (2026-05-13). Per-module migration —
runs via Phase 2's ``services.module_migrations.run_module_migrations``
after substrate migrations land at boot.

Why this is a module migration and not a substrate one: the
``mercury_*`` keys exist BECAUSE FinanceModule exists. They travel
with the module. If FinanceModule is ever uninstalled (e.g. a
future operator deployment that doesn't bank with Mercury), the
keys can be torn down by ``down()`` without affecting substrate.

Seeds (ON CONFLICT DO NOTHING — does NOT overwrite an existing key
that the operator already populated by hand):
- ``mercury_api_token`` — secret, empty default. Operator fills via
  ``poindexter settings set ... --secret`` once they've minted a
  Read-Only token at Mercury dashboard → Settings → API.
- ``mercury_enabled`` — public, 'false'. Gates the future polling
  job (F2) so partial deployments don't accidentally start calling
  Mercury without the operator's go-ahead.

Naming note: keys are ``mercury_*`` (no module prefix), matching
the existing ``mercury_api_token`` row Matt seeded by hand. The
codebase already uses unprefixed integration keys (``sentry_dsn``,
``telegram_bot_token``, ``discord_ops_webhook_url``), so no
``finance_`` prefix needed here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_UP_SQL = """
INSERT INTO app_settings
    (key, value, category, description, is_secret, is_active, updated_at)
VALUES
    ('mercury_api_token', '', 'finance',
     'Mercury Banking API token (Read-Only scope). Mint at Mercury '
     'dashboard → Settings → API. FinanceModule F1, #490 module v1.',
     TRUE, TRUE, NOW()),
    ('mercury_enabled', 'false', 'finance',
     'Master switch for Mercury integration. Set to true once '
     'mercury_api_token is populated so F2''s polling job starts '
     'fetching balance + transactions.',
     FALSE, TRUE, NOW())
ON CONFLICT (key) DO NOTHING;
"""

_DOWN_SQL = """
DELETE FROM app_settings WHERE key IN (
    'mercury_api_token',
    'mercury_enabled'
);
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_UP_SQL)
        logger.info("FinanceModule: seeded Mercury app_settings keys")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DOWN_SQL)
        logger.info("FinanceModule: removed Mercury app_settings keys")
