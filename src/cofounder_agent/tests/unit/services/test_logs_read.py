"""Unit tests for the Loki log proxy read-service (services/logs_read.py)."""
from __future__ import annotations

import httpx
import pytest

from services.logs_read import build_logql, flatten_streams, read_logs

_LOKI_PAYLOAD = {
    "status": "success",
    "data": {
        "resultType": "streams",
        "result": [
            {
                "stream": {"service": "poindexter-worker", "level": "error"},
                "values": [
                    ["1719500000000000000", "boom one"],
                    ["1719500001000000000", "boom two"],
                ],
            },
            {
                "stream": {"service": "poindexter-brain", "level": "info"},
                "values": [["1719500002000000000", "tick"]],
            },
        ],
    },
}


@pytest.mark.unit
def test_build_logql_defaults_to_service_selector():
    assert build_logql("", "", "") == '{service=~".+"}'


@pytest.mark.unit
def test_build_logql_appends_service_and_level_matchers():
    out = build_logql("", "poindexter-worker", "error")
    assert out == '{service=~".+",service="poindexter-worker",level="error"}'


@pytest.mark.unit
def test_build_logql_passthrough_when_full_query_given():
    assert build_logql('{container="x"}', "ignored", "ignored") == '{container="x"}'


@pytest.mark.unit
def test_flatten_streams_maps_sorts_desc_and_caps():
    rows = flatten_streams(_LOKI_PAYLOAD, limit=2)
    assert len(rows) == 2
    # newest first
    assert rows[0]["line"] == "tick"
    assert rows[0]["service"] == "poindexter-brain"
    assert rows[0]["level"] == "info"
    assert rows[0]["ts"].startswith("2024-06-27T")  # ns → ISO (1719500002s = 2024-06-27)


@pytest.mark.unit
async def test_read_logs_proxies_and_clamps_limit():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_LOKI_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await read_logs(
            client, loki_url="http://loki:3100", level="error", since="2h", limit=99999
        )
    assert out["stats"]["count"] == len(out["lines"])
    assert "limit=1000" in seen["url"]  # hard cap
    assert "since=2h" in seen["url"]
    assert "/loki/api/v1/query_range" in seen["url"]
