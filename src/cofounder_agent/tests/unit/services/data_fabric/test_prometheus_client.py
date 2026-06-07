"""Unit tests for DataFabric PrometheusClient."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.data_fabric.errors import DataFabricError
from services.data_fabric.prometheus import DEFAULT_URL, PrometheusClient


def _make_response(payload: dict, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


@pytest.fixture()
def mock_client():
    """Return a PrometheusClient with a patched internal httpx client."""
    client = PrometheusClient()
    client._client = AsyncMock(spec=httpx.AsyncClient)
    return client


class TestQueryParses:
    """query() correctly unwraps a success response."""

    @pytest.mark.asyncio
    async def test_returns_result_list(self, mock_client):
        payload = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"__name__": "up"}, "value": [1234567890, "1"]}
                ],
            },
        }
        mock_client._client.get.return_value = _make_response(payload)

        result = await mock_client.query('up{job="test"}')

        assert len(result) == 1
        assert result[0]["metric"]["__name__"] == "up"
        assert result[0]["value"][1] == "1"


class TestQueryRaisesOnHttpError:
    """query() raises DataFabricError for non-200 responses."""

    @pytest.mark.asyncio
    async def test_500_raises(self, mock_client):
        mock_client._client.get.return_value = _make_response(
            {"error": "internal"}, status_code=500
        )

        with pytest.raises(DataFabricError) as exc_info:
            await mock_client.query("up")

        err = exc_info.value
        assert err.store == "prometheus"
        assert err.status_code == 500

    @pytest.mark.asyncio
    async def test_404_raises(self, mock_client):
        mock_client._client.get.return_value = _make_response(
            {}, status_code=404
        )

        with pytest.raises(DataFabricError) as exc_info:
            await mock_client.query("up")

        assert exc_info.value.status_code == 404


class TestQueryRaisesOnErrorStatus:
    """query() raises DataFabricError when JSON status == 'error'."""

    @pytest.mark.asyncio
    async def test_error_status_in_json(self, mock_client):
        payload = {
            "status": "error",
            "errorType": "bad_data",
            "error": "invalid query",
        }
        mock_client._client.get.return_value = _make_response(payload)

        with pytest.raises(DataFabricError) as exc_info:
            await mock_client.query("invalid[[[")

        err = exc_info.value
        assert err.store == "prometheus"
        assert "invalid query" in str(err)


class TestDefaultUrl:
    """PrometheusClient uses DEFAULT_URL when no config is provided."""

    def test_no_args_uses_default(self):
        client = PrometheusClient()
        assert client._url == DEFAULT_URL

    def test_site_config_none_uses_default(self):
        client = PrometheusClient(site_config=None)
        assert client._url == DEFAULT_URL

    def test_explicit_url_overrides(self):
        client = PrometheusClient(url="http://custom:9999")
        assert client._url == "http://custom:9999"

    def test_site_config_get_used(self):
        sc = MagicMock()
        sc.get.return_value = "http://from-config:9091"
        client = PrometheusClient(site_config=sc)
        sc.get.assert_called_once_with("data_fabric_prometheus_url", DEFAULT_URL)
        assert client._url == "http://from-config:9091"
