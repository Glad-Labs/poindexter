"""DataFabric — thin async read-only query helpers for every observability store.

Gives the brain and worker direct Python access to Prometheus, Loki, Tempo,
and Pyroscope without mirroring data into Postgres.  Each client is created
lazily on first access and cached on the DataFabric instance.

Usage::

    fabric = DataFabric(site_config=site_config)
    result = await fabric.prometheus.query('up{job="poindexter-worker"}')
    logs   = await fabric.loki.query('{service="poindexter-worker"}')
"""

from __future__ import annotations

import httpx

from .errors import DataFabricError
from .loki import LokiClient
from .prometheus import PrometheusClient
from .pyroscope import PyroscopeClient
from .tempo import TempoClient

__all__ = [
    "DataFabric",
    "DataFabricError",
    "PrometheusClient",
    "LokiClient",
    "TempoClient",
    "PyroscopeClient",
]


class DataFabric:
    """Lazy-initialised facade over every data store.

    Pass ``site_config`` so each client picks up its URL from ``app_settings``.
    Pass ``http_client`` to inject a shared ``httpx.AsyncClient`` (useful in
    tests and in the FastAPI lifespan where a shared client already exists).
    """

    def __init__(
        self,
        *,
        site_config=None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._site_config = site_config
        self._http_client = http_client
        self._prometheus: PrometheusClient | None = None
        self._loki: LokiClient | None = None
        self._tempo: TempoClient | None = None
        self._pyroscope: PyroscopeClient | None = None

    # ------------------------------------------------------------------
    # Lazy properties
    # ------------------------------------------------------------------

    @property
    def prometheus(self) -> PrometheusClient:
        if self._prometheus is None:
            self._prometheus = PrometheusClient(
                site_config=self._site_config,
                http_client=self._http_client,
            )
        return self._prometheus

    @property
    def loki(self) -> LokiClient:
        if self._loki is None:
            self._loki = LokiClient(
                site_config=self._site_config,
                http_client=self._http_client,
            )
        return self._loki

    @property
    def tempo(self) -> TempoClient:
        if self._tempo is None:
            self._tempo = TempoClient(
                site_config=self._site_config,
                http_client=self._http_client,
            )
        return self._tempo

    @property
    def pyroscope(self) -> PyroscopeClient:
        if self._pyroscope is None:
            self._pyroscope = PyroscopeClient(
                site_config=self._site_config,
                http_client=self._http_client,
            )
        return self._pyroscope
