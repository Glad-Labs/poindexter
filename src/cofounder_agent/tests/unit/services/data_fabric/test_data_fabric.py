"""Unit tests for the DataFabric facade."""

from __future__ import annotations

from unittest.mock import MagicMock

from services.data_fabric import DataFabric
from services.data_fabric.loki import DEFAULT_URL as LOKI_DEFAULT_URL
from services.data_fabric.loki import LokiClient
from services.data_fabric.prometheus import DEFAULT_URL as PROMETHEUS_DEFAULT_URL
from services.data_fabric.prometheus import PrometheusClient
from services.data_fabric.pyroscope import DEFAULT_URL as PYROSCOPE_DEFAULT_URL
from services.data_fabric.pyroscope import PyroscopeClient
from services.data_fabric.tempo import DEFAULT_URL as TEMPO_DEFAULT_URL
from services.data_fabric.tempo import TempoClient


class TestPrometheusProperty:
    """DataFabric.prometheus returns a PrometheusClient."""

    def test_returns_prometheus_client(self):
        fabric = DataFabric()
        assert isinstance(fabric.prometheus, PrometheusClient)

    def test_lazy_cache_same_object(self):
        fabric = DataFabric()
        first = fabric.prometheus
        second = fabric.prometheus
        assert first is second

    def test_no_site_config_uses_default_url(self):
        fabric = DataFabric(site_config=None)
        assert fabric.prometheus._url == PROMETHEUS_DEFAULT_URL


class TestOtherClients:
    """All four lazy properties return the right type."""

    def test_loki_property(self):
        fabric = DataFabric()
        assert isinstance(fabric.loki, LokiClient)

    def test_tempo_property(self):
        fabric = DataFabric()
        assert isinstance(fabric.tempo, TempoClient)

    def test_pyroscope_property(self):
        fabric = DataFabric()
        assert isinstance(fabric.pyroscope, PyroscopeClient)

    def test_loki_lazy_cache(self):
        fabric = DataFabric()
        assert fabric.loki is fabric.loki

    def test_tempo_lazy_cache(self):
        fabric = DataFabric()
        assert fabric.tempo is fabric.tempo

    def test_pyroscope_lazy_cache(self):
        fabric = DataFabric()
        assert fabric.pyroscope is fabric.pyroscope


class TestSiteConfigPropagation:
    """site_config is forwarded to each client."""

    def test_site_config_passed_to_prometheus(self):
        sc = MagicMock()
        sc.get.return_value = "http://custom:9091"
        fabric = DataFabric(site_config=sc)
        client = fabric.prometheus
        assert client._url == "http://custom:9091"

    def test_site_config_passed_to_loki(self):
        sc = MagicMock()
        sc.get.return_value = "http://custom:3100"
        fabric = DataFabric(site_config=sc)
        client = fabric.loki
        assert client._url == "http://custom:3100"


class TestDefaultUrlsAreInternalDns:
    """DataFabric runs inside the worker/brain containers, so a ``localhost``
    default points at the container itself — the same in-container footgun
    PR #1827 fixed for the GPU-metrics URL (and retired ``nvidia_exporter_url``
    over). Defaults must use compose-service DNS so the clients resolve the
    real services over the docker network, sidestepping the host wslrelay
    port-forward that can wedge on Windows.
    """

    def test_defaults_never_use_localhost(self):
        for name, url in {
            "prometheus": PROMETHEUS_DEFAULT_URL,
            "loki": LOKI_DEFAULT_URL,
            "tempo": TEMPO_DEFAULT_URL,
            "pyroscope": PYROSCOPE_DEFAULT_URL,
        }.items():
            assert "localhost" not in url and "127.0.0.1" not in url, (
                f"{name} DEFAULT_URL={url!r} resolves to the container itself "
                "in-container — use compose-service DNS"
            )

    def test_defaults_match_compose_service_dns(self):
        # prometheus host-published on 9091 but listens internally on 9090
        # (matches gpu_metrics_prometheus_url set by #1827); the rest are 1:1.
        assert PROMETHEUS_DEFAULT_URL == "http://prometheus:9090"
        assert LOKI_DEFAULT_URL == "http://loki:3100"
        assert TEMPO_DEFAULT_URL == "http://tempo:3200"
        assert PYROSCOPE_DEFAULT_URL == "http://pyroscope:4040"
