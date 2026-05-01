"""Migration 0123: seed compose-spec drift probe settings.

Pairs with the brain's compose-drift probe shipped in
Glad-Labs/poindexter#213. The probe reads ``docker-compose.local.yml``
every 5-min cycle, ``docker inspect``s each service's container, and
flags drift when bind mounts / env keys / published ports / image tags
in the YAML are missing from the running container — the standard
"compose was edited but the container was never recreated" failure
mode that surfaces silently as "panel went empty" or "feature not
working" hours / days later.

This migration introduces three knobs the brain probe reads:

- ``compose_drift_auto_recover_enabled`` (default ``"false"``) —
  when ``"false"``, the probe only emits per-service ``audit_log``
  events plus a single deduped ``notify_operator`` ping per drift
  fingerprint. When ``"true"``, the probe runs
  ``docker compose -f <path> up -d <drifted-services>`` via the same
  Docker socket that ``migration_drift_probe`` uses, waits for the
  recreated containers to settle, and re-checks. If drift cleared, it
  emits ``probe.compose_drift_recovered``. If it didn't, it escalates
  via ``notify_operator``.

- ``compose_spec_path`` (default ``"/app/docker-compose.local.yml"``) —
  path to the compose file inside the brain container. The brain
  Dockerfile's bind-mount in ``docker-compose.local.yml`` lands the
  host's compose file here read-only. Configurable so an operator with
  a non-standard layout can point us at a different file (or at
  ``docker-compose.yml`` if they want to monitor the default-name
  variant).

- ``compose_drift_skip_services`` (default ``""``) — comma-separated
  list of service names to skip. Useful for sidecars that get
  hot-patched in place during a rollout, or for services with
  intentional spec drift (e.g. a manually-edited container that the
  operator doesn't want to recreate).

Default for auto-recover is ``"false"`` for the same reason the
migration-drift probe defaults off (#228, migration 0122): a buggy
detector that auto-recreates would cause cascading restarts. Operators
flip it on with
``poindexter set compose_drift_auto_recover_enabled true`` once
they're confident in the probe's accuracy on their particular stack.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — re-running the
migration leaves operator-set values alone.

Cross-references:
- Brain probe: brain/compose_drift_probe.py
- Wiring: brain/brain_daemon.py run_cycle (alongside migration_drift_probe)
- Docker bind-mount: docker-compose.local.yml brain-daemon volumes block
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SETTINGS = [
    {
        "key": "compose_drift_auto_recover_enabled",
        "value": "false",
        "category": "monitoring",
        "description": (
            "Brain compose-drift probe auto-recover toggle (#213). "
            "When 'false' (default, safe), the probe only notifies the "
            "operator when it detects drift between docker-compose.local.yml "
            "and the running containers — the operator runs the manual "
            "fix (`docker compose -f docker-compose.local.yml up -d "
            "<drifted-services>`). When 'true', the probe runs the "
            "recreate automatically via the Docker socket, waits for the "
            "containers to come back, and re-checks drift. Trade-off: "
            "auto-recreate cycles in-flight requests on the affected "
            "services for the recreate window, but stale spec is worse "
            "(missing mounts cause silent feature failures). Default "
            "'false' so a buggy probe can't cause cascading recreates. "
            "Flip to 'true' once you're confident in the probe's accuracy."
        ),
    },
    {
        "key": "compose_spec_path",
        "value": "/app/docker-compose.local.yml",
        "category": "monitoring",
        "description": (
            "Path to the docker-compose.yml the brain compose-drift probe "
            "(#213) reads. The brain container bind-mounts the host's "
            "./docker-compose.local.yml into this path read-only. Change "
            "this if your stack ships its compose file at a different "
            "path (e.g. /app/docker-compose.yml for the default-name "
            "variant). Setting an unreadable path degrades the probe to "
            "a structured 'unknown' status without crashing the brain."
        ),
    },
    {
        "key": "compose_drift_skip_services",
        "value": "",
        "category": "monitoring",
        "description": (
            "Comma-separated list of compose service names the brain "
            "drift probe (#213) should skip. Useful for services with "
            "intentional spec drift (a sidecar hot-patched in place, a "
            "manually-edited container the operator doesn't want to "
            "recreate). Empty by default — every service with a "
            "container_name in compose gets checked."
        ),
    },
]


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
                "Table 'app_settings' missing — skipping migration 0123"
            )
            return

        for setting in _SETTINGS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (key) DO NOTHING
                """,
                setting["key"],
                setting["value"],
                setting["category"],
                setting["description"],
            )
            if result == "INSERT 0 1":
                logger.info(
                    "Migration 0123: seeded %s=%r",
                    setting["key"], setting["value"],
                )
            else:
                logger.info(
                    "Migration 0123: %s already set, leaving operator value alone",
                    setting["key"],
                )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for setting in _SETTINGS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                setting["key"],
            )
        logger.info(
            "Migration 0123 rolled back: removed %d compose-drift settings",
            len(_SETTINGS),
        )
