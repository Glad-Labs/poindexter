"""Migration: seed DataFabric store URL settings (poindexter#429).

Wires services/data_fabric/ into app_settings so each client picks up
its endpoint via site_config.get("data_fabric_<store>_url").  Defaults
match the Docker-local ports documented in CLAUDE.md.  All values are
non-secret and safe to ship in the public mirror.

To override a URL (e.g. when running inside Docker):
    poindexter settings set data_fabric_prometheus_url http://host.docker.internal:9091

Idempotent — ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEYS = [
    (
        "data_fabric_prometheus_url",
        "http://localhost:9091",
        "observability",
        "Prometheus HTTP API base URL used by DataFabric.PrometheusClient. "
        "Override to http://host.docker.internal:9091 when running inside Docker.",
    ),
    (
        "data_fabric_loki_url",
        "http://localhost:3100",
        "observability",
        "Loki HTTP API base URL used by DataFabric.LokiClient. "
        "Override to http://host.docker.internal:3100 when running inside Docker.",
    ),
    (
        "data_fabric_tempo_url",
        "http://localhost:3200",
        "observability",
        "Tempo HTTP API base URL used by DataFabric.TempoClient. "
        "Override to http://host.docker.internal:3200 when running inside Docker.",
    ),
    (
        "data_fabric_pyroscope_url",
        "http://localhost:4040",
        "observability",
        "Pyroscope HTTP API base URL used by DataFabric.PyroscopeClient. "
        "Override to http://host.docker.internal:4040 when running inside Docker.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description in _KEYS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
    logger.info(
        "Migration seed_data_fabric_settings: seeded %d keys", len(_KEYS),
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            [k for k, *_ in _KEYS],
        )
    logger.info("Migration seed_data_fabric_settings down: removed %d keys", len(_KEYS))
