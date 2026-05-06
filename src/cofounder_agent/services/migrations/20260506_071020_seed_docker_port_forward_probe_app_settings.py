"""Migration 20260506_071020: seed docker port forward probe app_settings

ISSUE: Glad-Labs/poindexter#222 (auto-detect + recover stale Docker
Desktop port forwards).

Seeds the seven tunables ``brain/docker_port_forward_probe.py`` reads
on every cycle. The probe detects the Windows wslrelay →
``com.docker.backend`` forwarding chain getting stuck (TCP handshake
succeeds but HTTP requests get an empty reply) and auto-recovers by
``docker restart``-ing the affected container.

Defaults bake in the 12 mission-control linked services we know are
prone to the failure (Pyroscope, GlitchTip, Alertmanager, pgAdmin,
Grafana, Prometheus, Loki, Langfuse, ClickHouse, MinIO, Redis Insight,
the Pyroscope-Grafana frontend). Containers in this list that aren't
actually running are silently skipped (``unwatched``) so operators
who've stripped a service don't see a probe alert about it.

Restart cap (default 3 restarts per 60 minutes per container) prevents
a runaway restart loop in case the underlying issue isn't actually a
stuck port forward — when the cap fires the probe writes an
``alert_events`` row so the operator hears about it instead of the
brain quietly thrashing the container.

The probe re-reads each cycle so an operator can re-tune via
``poindexter set <key> <value>`` without restarting the brain.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Default watch list — the 12 mission-control linked services from the
# issue. Stored as JSON so an operator can extend or trim it via
# ``poindexter set docker_port_forward_watch_list '<json>'``. The
# probe derives the internal-DNS hostname from the container name by
# stripping the ``poindexter-`` prefix (per the issue's example), but
# each entry can override that explicitly via ``internal_hostname``.
_DEFAULT_WATCH_LIST_JSON = (
    '['
    '{"container": "poindexter-pyroscope", "port": 4040, "path": "/"},'
    '{"container": "poindexter-glitchtip-web", "port": 8080, "path": "/"},'
    '{"container": "poindexter-alertmanager", "port": 9093, "path": "/-/healthy"},'
    '{"container": "poindexter-pgadmin", "port": 5480, "path": "/misc/ping"},'
    '{"container": "poindexter-grafana", "port": 3000, "path": "/api/health"},'
    '{"container": "poindexter-prometheus", "port": 9090, "path": "/-/healthy"},'
    '{"container": "poindexter-loki", "port": 3100, "path": "/ready"},'
    '{"container": "poindexter-langfuse-web", "port": 13000, "path": "/api/public/health"},'
    '{"container": "poindexter-clickhouse", "port": 8123, "path": "/ping"},'
    '{"container": "poindexter-minio", "port": 9000, "path": "/minio/health/live"},'
    '{"container": "poindexter-redis-insight", "port": 5540, "path": "/healthcheck"},'
    '{"container": "poindexter-pyroscope-grafana-frontend", "port": 4045, "path": "/"}'
    ']'
)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES
                ('docker_port_forward_probe_enabled', 'true', 'monitoring',
                 'Master switch for the brain Docker port-forward stuck-state probe (#222). When false the probe short-circuits without scanning containers.',
                 false, true),
                ('docker_port_forward_poll_interval_minutes', '5', 'monitoring',
                 'Cadence at which the brain runs the port-forward probe. Default 5 min matches the brain cycle so it runs every cycle.',
                 false, true),
                ('docker_port_forward_watch_list', $1, 'monitoring',
                 'JSON array of services to probe. Each entry: {container, port, path, [internal_hostname]}. internal_hostname defaults to container name with the poindexter- prefix stripped.',
                 false, true),
                ('docker_port_forward_probe_timeout_seconds', '3', 'monitoring',
                 'Per-HTTP-probe timeout in seconds. Kept tight (3s) so a stuck service can''t block the brain cycle on probes.',
                 false, true),
                ('docker_port_forward_recovery_wait_seconds', '5', 'monitoring',
                 'How long the probe waits after a docker restart before re-probing to confirm recovery. Default 5s lets Docker Desktop re-establish the wslrelay forward.',
                 false, true),
                ('docker_port_forward_restart_cap_per_window', '3', 'monitoring',
                 'Maximum number of times a single container may be restarted within the rolling window. Prevents runaway restart loops when the underlying issue is not actually a stuck port forward.',
                 false, true),
                ('docker_port_forward_restart_cap_window_minutes', '60', 'monitoring',
                 'Rolling window length in minutes for the per-container restart cap. Default 60 min — combined with the cap of 3 means at most 3 restarts of any one container per hour.',
                 false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            _DEFAULT_WATCH_LIST_JSON,
        )
        logger.info(
            "Migration 20260506_071020: applied (7 docker_port_forward_* settings)"
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'docker_port_forward_probe_enabled',
                'docker_port_forward_poll_interval_minutes',
                'docker_port_forward_watch_list',
                'docker_port_forward_probe_timeout_seconds',
                'docker_port_forward_recovery_wait_seconds',
                'docker_port_forward_restart_cap_per_window',
                'docker_port_forward_restart_cap_window_minutes'
            )
            """
        )
        logger.info("Migration 20260506_071020: reverted")
