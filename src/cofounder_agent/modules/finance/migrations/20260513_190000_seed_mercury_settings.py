"""Seed the finance/Mercury app_settings keys.

FinanceModule F1 migration #1 (2026-05-13). Per-module migration —
runs via Phase 2's ``services.module_migrations.run_module_migrations``
after substrate migrations land at boot.

Why this is a module migration and not a substrate one: the
``finance_mercury_*`` keys exist BECAUSE FinanceModule exists. They
travel with the module. If FinanceModule is ever uninstalled (e.g.
a future operator deployment that doesn't bank with Mercury), the
keys can be torn down by ``down()`` without affecting substrate.

Seeds:
- ``finance_mercury_api_token`` — secret, empty default. Operator
  fills via ``poindexter settings set ... --secret`` once they've
  minted a Read-Only token at Mercury dashboard → Settings → API.
- ``finance_mercury_enabled`` — public, 'false'. Gates the future
  polling job (F2) so partial deployments don't accidentally start
  calling Mercury without the operator's go-ahead.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_UP_SQL = """
INSERT INTO app_settings
    (key, value, category, description, is_secret, is_active, updated_at)
VALUES
    ('finance_mercury_api_token', '', 'finance',
     'Mercury Banking API token (Read-Only scope). Mint at Mercury '
     'dashboard → Settings → API. FinanceModule F1, #490 module v1.',
     TRUE, TRUE, NOW()),
    ('finance_mercury_enabled', 'false', 'finance',
     'Master switch for Mercury integration. Set to true once '
     'finance_mercury_api_token is populated so F2''s polling job '
     'starts fetching balance + transactions.',
     FALSE, TRUE, NOW())
ON CONFLICT (key) DO NOTHING;
"""

_DOWN_SQL = """
DELETE FROM app_settings WHERE key IN (
    'finance_mercury_api_token',
    'finance_mercury_enabled'
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
