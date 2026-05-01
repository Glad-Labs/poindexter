"""Migration 0122: seed ``migration_drift_auto_recover_enabled`` kill-switch.

Pairs with the brain's migration-drift probe shipped in
Glad-Labs/poindexter#228. The probe queries the worker's ``/api/health``
``migrations`` block (#270 / #313) every 5-min cycle and detects when
new ``.py`` files exist in ``services/migrations/`` that haven't been
applied to ``schema_migrations``. Drift is the standard
"container shipped a new migration but the worker wasn't restarted to
apply it" failure mode — it surfaces today as a Prometheus alert
(``poindexter_unapplied_migrations_count > 0`` from #227) and a
Telegram ping after 30 minutes, which the operator has to manually
fix with ``docker restart poindexter-worker``.

This migration introduces a single boolean knob the brain probe reads
to decide whether to automate that restart:

- ``migration_drift_auto_recover_enabled`` (default ``"false"``) —
  when ``"false"``, the probe ONLY notifies the operator about
  drift (capped at one notify per drift-count change so a stuck
  pending count doesn't blast Telegram every cycle). When
  ``"true"``, the probe runs ``docker restart poindexter-worker``
  via the Docker socket the brain container already has mounted at
  ``/var/run/docker.sock``, waits up to 60s for the worker to report
  healthy, then re-checks drift. If drift cleared, the probe emits
  ``probe.migration_drift_recovered`` and is done. If the restart
  failed or drift persists, it escalates via ``notify_operator``.

Default is ``"false"`` for safety — auto-restarting on every
detected drift would mask a bad migration that's actively crash-
looping the worker. Operators flip it on with
``poindexter set migration_drift_auto_recover_enabled true`` once
they're confident about their migration pipeline. Trade-off: an
auto-restart may interrupt a small number of in-flight requests
(<60s downtime), but a worker on a stale schema is worse — every
write against the new tables fails.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — re-running the
migration leaves any operator-set value alone.

Cross-references:
- Brain probe: brain/migration_drift_probe.py
- Wiring: brain/brain_daemon.py run_cycle (alongside operator_url_probe)
- Existing drift surfaces: services/main.py /api/health migrations block
  (#270 / #313), services/metrics_exporter.py
  poindexter_unapplied_migrations_count (#227)
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_KEY = "migration_drift_auto_recover_enabled"
_VALUE = "false"
_CATEGORY = "monitoring"
_DESCRIPTION = (
    "Brain migration-drift probe behavior (#228). When 'false' (default, "
    "safe), the probe ONLY notifies the operator when it detects "
    "unapplied migrations on the worker — the operator runs the manual "
    "fix (`docker restart poindexter-worker`). When 'true', the probe "
    "runs the restart automatically via the Docker socket, waits 60s "
    "for the worker to come back healthy, and re-checks drift. "
    "Trade-off: auto-restart may interrupt in-flight requests for ~60s "
    "but stale schema is worse (writes against new tables fail). "
    "Default 'false' so a bad migration that's crash-looping the worker "
    "isn't masked by an auto-restart loop. Flip to 'true' once you're "
    "confident in your migration pipeline."
)


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
                "Table 'app_settings' missing — skipping migration 0122"
            )
            return

        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret)
            VALUES ($1, $2, $3, $4, FALSE)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY, _VALUE, _CATEGORY, _DESCRIPTION,
        )
        if result == "INSERT 0 1":
            logger.info(
                "Migration 0122: seeded %s=%s (brain migration-drift "
                "auto-recover, default OFF for safety)",
                _KEY, _VALUE,
            )
        else:
            logger.info(
                "Migration 0122: %s already set, leaving operator value alone",
                _KEY,
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            _KEY,
        )
        logger.info("Migration 0122 rolled back: removed %s", _KEY)
