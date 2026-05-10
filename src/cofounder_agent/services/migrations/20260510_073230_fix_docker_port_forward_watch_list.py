"""Fix wrong internal ports in ``docker_port_forward_watch_list``.

The probe (``brain/docker_port_forward_probe.py``) hits each service
on its INTERNAL container port from inside the docker network,
compares against the same hostname:port reachable from the host,
and emits a ``probe.docker_port_forward_service_down`` audit row
when the internal probe fails.

Two entries had wrong internal ports — they pointed at ports the
container doesn't even bind, so every poll cycle wrote
"service down" rows for healthy services. Found while investigating
audit_log noise during the LangGraph cutover stress test
(2026-05-10).

Wrong  | Right | Container
-------|-------|----------
13000  | 3000  | poindexter-langfuse-web (verified: docker port → 3000/tcp)
5480   | 80    | poindexter-pgadmin       (verified: docker port → 80/tcp)

Idempotent: applying twice is a no-op because the second run sees
the value already correct.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


# The full corrected list. Easier to overwrite the whole CSV than
# JSON-patch one entry — the `value` column is jsonb-as-text.
_CORRECTED = [
    {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
    {"container": "poindexter-glitchtip-web", "port": 8080, "path": "/"},
    {"container": "poindexter-alertmanager", "port": 9093, "path": "/-/healthy"},
    {"container": "poindexter-pgadmin", "port": 80, "path": "/misc/ping"},
    {"container": "poindexter-grafana", "port": 3000, "path": "/api/health"},
    {
        "container": "poindexter-prometheus",
        "port": 9090,
        "path": "/-/healthy",
        "host_port": 9091,
    },
    {"container": "poindexter-loki", "port": 3100, "path": "/ready"},
    {
        "container": "poindexter-langfuse-web",
        "port": 3000,
        "path": "/api/public/health",
    },
    {"container": "poindexter-clickhouse", "port": 8123, "path": "/ping"},
    {"container": "poindexter-minio", "port": 9000, "path": "/minio/health/live"},
    {"container": "poindexter-redis-insight", "port": 5540, "path": "/healthcheck"},
    {
        "container": "poindexter-pyroscope-grafana-frontend",
        "port": 4045,
        "path": "/",
    },
]


async def run_migration(conn) -> None:
    await conn.execute(
        "UPDATE app_settings SET value = $1 "
        "WHERE key = 'docker_port_forward_watch_list'",
        json.dumps(_CORRECTED),
    )
    logger.info(
        "20260510_073230: docker_port_forward_watch_list corrected "
        "(langfuse-web 13000→3000, pgadmin 5480→80)"
    )
