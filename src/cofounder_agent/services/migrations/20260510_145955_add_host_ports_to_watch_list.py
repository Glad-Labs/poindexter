"""Add ``host_port`` overrides for langfuse-web + pgadmin in the
docker_port_forward_watch_list.

The earlier port-fix migration (``20260510_073230``) corrected the
internal ports for these two services (langfuse-web 13000→3000,
pgadmin 5480→80) but missed that they also publish on DIFFERENT
host ports than their internal ports:

  - langfuse-web: internal 3000 → host 3010
  - pgadmin:      internal 80   → host 18443

The probe code in ``brain/docker_port_forward_probe.py`` line 649
has:

  host_port = service.get("host_port", port)

So when ``host_port`` isn't specified, the probe uses the internal
port externally — testing ``http://host.docker.internal:3000`` and
``http://host.docker.internal:80``, both of which the host doesn't
expose. The probe then diagnoses "stuck port forward" and restarts
the containers in a loop.

Caught the morning after the previous migration when Matt got
spammed with restart-cap alerts. Both services were genuinely
healthy on their actual host ports (3010, 18443). Adding
``host_port`` overrides matches the existing pattern used by
prometheus (internal 9090 → host 9091).

Idempotent — overwrites the watch_list value; running twice is a
no-op.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


_FIXED = [
    {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
    {"container": "poindexter-glitchtip-web", "port": 8080, "path": "/"},
    {"container": "poindexter-alertmanager", "port": 9093, "path": "/-/healthy"},
    {
        "container": "poindexter-pgadmin",
        "port": 80,
        "host_port": 18443,
        "path": "/misc/ping",
    },
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
        "host_port": 3010,
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
        json.dumps(_FIXED),
    )
    logger.info(
        "20260510_145955: docker_port_forward_watch_list updated with "
        "host_port overrides for langfuse-web (3010) + pgadmin (18443) "
        "— probe now tests the actual host port instead of the internal "
        "port that the host doesn't expose."
    )
