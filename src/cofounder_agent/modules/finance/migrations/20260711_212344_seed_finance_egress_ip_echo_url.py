"""Seed the finance egress-IP reflector URL.

FinanceModule migration (2026-07-11). Adds ``finance_egress_ip_echo_url`` —
the reflector the poll-staleness probe queries to name the worker's current
public egress IPv4 in the auth-lost alert, so the operator can paste it
straight into Mercury's token IP allowlist. The residential IPv4 the worker
egresses to Mercury rotates via DHCP; a 401 is almost always that IP falling
off the allowlist (not a bad token), so the alert names the current IP.

Per-module migration — travels with the module (``down()`` removes the key),
mirroring ``20260513_190000_seed_mercury_settings``. ``ON CONFLICT DO NOTHING``
so an operator-tuned reflector URL is never clobbered. Kept in a stripped
module migration (not ``settings_defaults.py``) so this finance key never
ships to the public mirror. The probe also carries the same default in code
(``DEFAULT_EGRESS_IP_ECHO_URL``) so it behaves sanely even before this seed
runs — the seeded row exists for operator discoverability + tuning.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_UP_SQL = """
INSERT INTO app_settings
    (key, value, category, description, is_secret, is_active, updated_at)
VALUES
    ('finance_egress_ip_echo_url', 'https://api.ipify.org', 'finance',
     'Reflector URL the finance poll-staleness probe queries to report the '
     'worker''s current public egress IPv4 in the auth-lost alert (so the '
     'operator can paste it into Mercury''s token IP allowlist). '
     'Operator-tunable; the residential IPv4 rotates via DHCP.',
     FALSE, TRUE, NOW())
ON CONFLICT (key) DO NOTHING;
"""

_DOWN_SQL = """
DELETE FROM app_settings WHERE key = 'finance_egress_ip_echo_url';
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_UP_SQL)
        logger.info("FinanceModule: seeded finance_egress_ip_echo_url")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DOWN_SQL)
        logger.info("FinanceModule: removed finance_egress_ip_echo_url")
