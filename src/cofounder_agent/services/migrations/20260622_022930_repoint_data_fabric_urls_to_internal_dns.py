"""Repoint DataFabric observability URLs to internal docker DNS.

Follow-up to PR #1827 (the in-container ``localhost`` footgun).

``data_fabric_{prometheus,loki,tempo,pyroscope}_url`` were seeded
(20260607_181951) with ``http://localhost:<port>`` defaults. Every DataFabric
client (``services/data_fabric/*.py``) runs *inside* the worker/brain
containers, where ``localhost`` is the container itself — the exact class of
bug PR #1827 fixed for the GPU-metrics URL and the ``nvidia_exporter_url``
retirement. Repoint to compose-service DNS so the clients reach the real
services over the docker network (and skip the host wslrelay port-forward that
can wedge on Windows).

Guarded read-modify-write: a row is only rewritten when it still holds the old
``localhost`` default, so an operator's custom override is preserved (mirrors
20260608_012805's discipline). Idempotent — once converged the guard matches
nothing. The canonical defaults now live in ``settings_defaults.DEFAULTS`` +
each client's ``DEFAULT_URL`` constant; this migration just converges
already-seeded rows on prod / replayed history.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# (key, old localhost default, new internal-DNS value, new description)
_REPOINTS: list[tuple[str, str, str, str]] = [
    (
        "data_fabric_prometheus_url",
        "http://localhost:9091",
        "http://prometheus:9090",
        "Prometheus HTTP API base URL used by DataFabric.PrometheusClient. "
        "Defaults to compose-service DNS (prometheus listens on 9090 "
        "internally; host-published 9091) so it resolves from inside the "
        "worker/brain containers.",
    ),
    (
        "data_fabric_loki_url",
        "http://localhost:3100",
        "http://loki:3100",
        "Loki HTTP API base URL used by DataFabric.LokiClient. Defaults to "
        "compose-service DNS so it resolves from inside the containers.",
    ),
    (
        "data_fabric_tempo_url",
        "http://localhost:3200",
        "http://tempo:3200",
        "Tempo HTTP API base URL used by DataFabric.TempoClient. Defaults to "
        "compose-service DNS so it resolves from inside the containers.",
    ),
    (
        "data_fabric_pyroscope_url",
        "http://localhost:4040",
        "http://pyroscope:4040",
        "Pyroscope HTTP API base URL used by DataFabric.PyroscopeClient. "
        "Defaults to compose-service DNS so it resolves from inside the "
        "containers.",
    ),
]


async def up(pool) -> None:
    """Repoint each still-default URL (value + stale description) to its
    compose-service DNS form. Rows an operator already customised are left
    untouched by the ``value = $4`` guard."""
    repointed = 0
    async with pool.acquire() as conn:
        for key, old, new, desc in _REPOINTS:
            status = await conn.execute(
                """
                UPDATE app_settings
                   SET value = $2, description = $3
                 WHERE key = $1 AND value = $4
                """,
                key, new, desc, old,
            )
            if isinstance(status, str) and status.endswith(" 1"):
                repointed += 1
    logger.info(
        "repoint_data_fabric_urls_to_internal_dns: repointed %d url(s) "
        "(operator-overridden rows left untouched)",
        repointed,
    )


async def down(pool) -> None:
    """Revert the value to the old localhost default (guarded on the new
    value so a fresh operator override survives a down-migration too)."""
    async with pool.acquire() as conn:
        for key, old, new, _desc in _REPOINTS:
            await conn.execute(
                "UPDATE app_settings SET value = $2 WHERE key = $1 AND value = $3",
                key, old, new,
            )
    logger.info(
        "repoint_data_fabric_urls_to_internal_dns down: reverted to localhost",
    )
