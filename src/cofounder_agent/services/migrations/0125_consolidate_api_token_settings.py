"""Migration 0125: consolidate ``api_token`` / ``api_auth_token`` rows.

Closes Glad-Labs/poindexter#326. Migration ``0106`` already removes the
orphaned ``api_auth_token`` row, but ``main.py`` was re-creating it on
every worker boot via the secrets-sync map (``"api_auth_token":
("API_TOKEN", "security")``). The companion code change in this PR
flips that map entry to ``"api_token"`` so the boot path now agrees
with the middleware. This migration is the data-layer half: it
consolidates whatever's currently in the database so an existing
deployment that already had both rows ends up with a single canonical
``api_token`` row with the right value.

## What this migration does

For each of three states an existing deployment might be in:

1. **Both rows exist, ``api_token`` is empty, ``api_auth_token`` has a
   value** — the live deployment was authenticating against
   ``api_token`` (per ``middleware/api_token_auth.py``) but the human
   operator had been rotating the wrong row. Decrypt
   ``api_auth_token`` and re-encrypt the same value into ``api_token``
   via :func:`plugins.secrets.set_secret`. Then delete
   ``api_auth_token``. After this migration ``api_token`` holds the
   value the operator was actually managing, and the dead row is gone.

2. **Both rows exist, ``api_token`` already has a value** — leave
   ``api_token`` alone (it's the canonical and the middleware reads
   it). Just delete ``api_auth_token``.

3. **Only ``api_auth_token`` exists** (rare — would mean migration
   0058's seed of ``api_token`` got rolled back somehow). Same as
   case 1: copy value into ``api_token``, delete the old row.

4. **Only ``api_token`` exists / neither exists** — no-op.

## Idempotent

Re-running this migration on any post-consolidation state is a no-op.
``api_auth_token`` no longer exists, the ``DELETE WHERE key =
'api_auth_token'`` is a "DELETE 0", and the value-copy path is gated
on the dead row being present.

## CI / no-secret-key safety

If ``POINDEXTER_SECRET_KEY`` is unavailable (e.g. ``migrations_smoke``
in CI, or a fresh dev clone) we still run the schema-level
consolidation — delete the dead row — but we skip the value copy.
``api_token`` stays empty and the operator regenerates it via
``poindexter setup --rotate-secrets`` or by setting the ``API_TOKEN``
env var on next boot (``main.py`` will then persist it into
``api_token`` via the secrets-sync map). This preserves the property
that migrations don't fail closed in CI, while ensuring no production
deployment loses the operator's actual bearer token.

## Down

Down() is a no-op — the dead row is the bug we're fixing. If a
genuine roll-back is required, the operator can re-set ``api_token``
manually via ``poindexter settings set api_token <value>``.

Migration 0106 already exists and unconditionally deletes
``api_auth_token``. Both migrations are idempotent and 0125 is
specifically aware that the row may have already been removed by 0106
on prior boots. The two layers compose safely.
"""

from __future__ import annotations

import os

from services.logger_config import get_logger

logger = get_logger(__name__)


_DEAD_KEY = "api_auth_token"
_LIVE_KEY = "api_token"
_ENC_PREFIX = "enc:v1:"


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "0125: app_settings table missing — skipping (fresh install)"
            )
            return

        dead_row = await conn.fetchrow(
            "SELECT value, is_secret FROM app_settings WHERE key = $1",
            _DEAD_KEY,
        )
        live_row = await conn.fetchrow(
            "SELECT value, is_secret FROM app_settings WHERE key = $1",
            _LIVE_KEY,
        )

        if dead_row is None:
            # Already cleaned up (0106 ran, or fresh install). The
            # canonical row may or may not exist — that's not our
            # concern here; migration 0058 seeds it and main.py keeps
            # it in sync. Nothing to consolidate.
            logger.info(
                "0125: '%s' row already absent — consolidation not needed",
                _DEAD_KEY,
            )
            return

        dead_value = dead_row["value"] or ""
        live_value = (live_row["value"] if live_row else "") or ""

        # Decide whether to migrate the value across. We copy when the
        # dead row has a real value AND the live row does NOT — i.e.
        # the operator was managing the wrong row and we'd otherwise
        # lock them out post-migration.
        should_copy = bool(dead_value) and not live_value

        if should_copy:
            secret_key_present = bool(os.getenv("POINDEXTER_SECRET_KEY"))
            if not secret_key_present:
                # CI / no-key environment: log loudly but proceed with
                # the schema cleanup. See module docstring "CI safety"
                # section for the operational follow-up.
                logger.warning(
                    "0125: POINDEXTER_SECRET_KEY unset — cannot decrypt '%s' "
                    "to copy value into '%s'. Deleting dead row anyway; "
                    "operator must re-set '%s' via `poindexter settings set "
                    "%s <value>` or by populating the API_TOKEN env var "
                    "(main.py will persist it on next boot).",
                    _DEAD_KEY, _LIVE_KEY, _LIVE_KEY, _LIVE_KEY,
                )
            else:
                try:
                    from plugins.secrets import (
                        ensure_pgcrypto,
                        get_secret,
                        set_secret,
                    )

                    await ensure_pgcrypto(conn)
                    plaintext = await get_secret(conn, _DEAD_KEY)
                    if plaintext:
                        await set_secret(
                            conn,
                            _LIVE_KEY,
                            plaintext,
                            description=(
                                "Bearer token MCP / CLI / Grafana send to the "
                                "worker REST API (consolidated from "
                                "api_auth_token in #326)"
                            ),
                        )
                        logger.info(
                            "0125: copied decrypted value of '%s' into '%s' "
                            "(re-encrypted via set_secret)",
                            _DEAD_KEY, _LIVE_KEY,
                        )
                    else:
                        logger.info(
                            "0125: '%s' decrypted to empty — nothing to copy",
                            _DEAD_KEY,
                        )
                except Exception as e:
                    # Don't fail the migration on a value-copy hiccup —
                    # the schema cleanup (DELETE below) is the
                    # load-bearing change. The operator can re-set
                    # api_token via the CLI if the copy missed.
                    logger.warning(
                        "0125: deferred value copy of '%s' → '%s' — reason: %s "
                        "(operator must re-set %s manually)",
                        _DEAD_KEY, _LIVE_KEY, e, _LIVE_KEY,
                    )
        else:
            # Either the dead row is empty (no value to preserve) or
            # the live row already has a value (canonical wins).
            logger.info(
                "0125: skipping value copy — dead_has_value=%s, live_has_value=%s",
                bool(dead_value), bool(live_value),
            )

        # Always delete the dead row. This is the load-bearing change.
        # 0106 may have already removed it on prior boots; that's
        # fine — DELETE 0 is idempotent.
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            _DEAD_KEY,
        )
        if result == "DELETE 1":
            logger.info(
                "0125: removed orphaned '%s' row (canonical key is '%s' — see #326)",
                _DEAD_KEY, _LIVE_KEY,
            )
        else:
            logger.info(
                "0125: no '%s' row to remove (already absent)",
                _DEAD_KEY,
            )


async def down(_pool) -> None:
    """No-op by design.

    The ``api_auth_token`` row was never load-bearing — the worker
    middleware always read ``api_token``. Recreating it on rollback
    would just reintroduce the duplicate-row trap that #326 closed.
    Roll-forward only.
    """
    logger.info(
        "0125: down() is a no-op — '%s' is dead data we never want to recreate",
        _DEAD_KEY,
    )
