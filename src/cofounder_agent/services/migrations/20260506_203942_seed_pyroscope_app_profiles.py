"""Migration 20260506_203942: flip ``enable_pyroscope`` default to true.

ISSUE: Glad-Labs/poindexter#406

Why
----
Pyroscope server is running and the ``local-pyroscope`` Grafana datasource
is provisioned, but no application-level profiles have been shipped — the
only data the server ingests is its own Go-runtime self-profile. The
gap was that ``enable_pyroscope`` defaulted to ``false`` AND
``services/profiling.py:setup_pyroscope`` was never wired into the worker
/ brain / voice-agent startup paths AND the worker Dockerfile didn't
install the ``[profiling]`` poetry extra so ``pyroscope-io`` was missing
from the image regardless.

This migration is the configuration half of the fix: flip the master
switch so that, once the rebuilt images roll out, profiles start
flowing without an operator having to log in and toggle a setting.

What this migration does
------------------------
- Inserts ``enable_pyroscope=true`` if the row doesn't exist.
- Updates ``description`` + ``category`` on conflict so an operator
  who already set the value (in either direction) keeps their choice.
  Per ``feedback_app_settings_value_not_null``, the seed row uses the
  literal string ``'true'`` (NOT NULL column; empty string would be
  the unset sentinel).

Idempotent — safe to re-run; the smoke test
(``scripts/ci/migrations_smoke.py``) exercises that.

Out of scope
------------
- The wiring (Dockerfile.worker extras + main.py / brain_daemon.py /
  voice_agent_*.py setup_pyroscope calls + Grafana per-service flame
  panel + CLAUDE.md note) is in the same PR but lives in code, not
  app_settings.
- ``enable_tracing`` flip — tracked under the audit's separate issue.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_KEY = "enable_pyroscope"
_VALUE = "true"
_CATEGORY = "observability"
_DESCRIPTION = (
    "When true, services/profiling.py:setup_pyroscope() configures the "
    "pyroscope-io agent at worker / brain / voice-agent startup so each "
    "process ships CPU profiles to the Pyroscope server at "
    "pyroscope_server_url (default http://pyroscope:4040). Default "
    "flipped to true in Glad-Labs/poindexter#406. Set to false to "
    "disable continuous profiling fleet-wide without rebuilding any "
    "images — the agent picks up the change on next service restart."
)


async def up(pool) -> None:
    """Apply: seed enable_pyroscope=true (idempotent UPSERT)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
              (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, $3, $4, false, true)
            ON CONFLICT (key) DO UPDATE
              SET description = EXCLUDED.description,
                  category = EXCLUDED.category,
                  updated_at = NOW()
            """,
            _KEY, _VALUE, _CATEGORY, _DESCRIPTION,
        )
        logger.info(
            "Migration 20260506_203942: seeded %s=%s (operator overrides preserved)",
            _KEY, _VALUE,
        )


async def down(pool) -> None:
    """Revert: restore the pre-#406 default of 'false' on the seeded row.

    Down doesn't DELETE the row — other observability code paths
    (settings_defaults.py, the /admin panel) expect the key to exist.
    Just flip the value back to the legacy default.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
               SET value = 'false', updated_at = NOW()
             WHERE key = $1
            """,
            _KEY,
        )
        logger.info(
            "Migration 20260506_203942 down: reset %s to 'false'", _KEY,
        )
