"""Unit tests for DataFabric LokiClient."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.data_fabric.errors import DataFabricError
from services.data_fabric.loki import DEFAULT_URL, LokiClient


def _make_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


@pytest.fixture()
def mock_client():
    client = LokiClient()
    client._client = AsyncMock(spec=httpx.AsyncClient)
    return client


class TestQueryParsesStreams:
    """query() correctly unwraps a Loki streams response."""

    @pytest.mark.asyncio
    async def test_returns_streams(self, mock_client):
        payload = {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {"service": "poindexter-worker"},
                        "values": [["1718000000000000000", "log line here"]],
                    }
                ],
            },
        }
        mock_client._client.get.return_value = _make_response(payload)

        result = await mock_client.query('{service="poindexter-worker"}')

        assert len(result) == 1
        assert result[0]["stream"]["service"] == "poindexter-worker"
        assert result[0]["values"][0][1] == "log line here"

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_client):
        payload = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        mock_client._client.get.return_value = _make_response(payload)
        result = await mock_client.query('{service="missing"}')
        assert result == []


class TestQueryRaisesOnHttpError:
    """query() raises DataFabricError for non-200 responses."""

    @pytest.mark.asyncio
    async def test_500_raises(self, mock_client):
        mock_client._client.get.return_value = _make_response(
            {}, status_code=500
        )
        with pytest.raises(DataFabricError) as exc_info:
            await mock_client.query("{job=~'.+'}")
        assert exc_info.value.status_code == 500
        assert exc_info.value.store == "loki"

    @pytest.mark.asyncio
    async def test_error_status_json(self, mock_client):
        payload = {"status": "error", "error": "parse error at line 1"}
        mock_client._client.get.return_value = _make_response(payload)
        with pytest.raises(DataFabricError) as exc_info:
            await mock_client.query("{{{bad_logql")
        assert "parse error" in str(exc_info.value)


class TestDefaultUrl:
    """LokiClient falls back to DEFAULT_URL when no config supplied."""

    def test_no_args(self):
        assert LokiClient()._url == DEFAULT_URL

    def test_explicit_url(self):
        assert LokiClient(url="http://loki:9999")._url == "http://loki:9999"

    def test_site_config_used(self):
        sc = MagicMock()
        sc.get.return_value = "http://loki-custom:3100"
        client = LokiClient(site_config=sc)
        sc.get.assert_called_once_with("data_fabric_loki_url", DEFAULT_URL)
        assert client._url == "http://loki-custom:3100"
