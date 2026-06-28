"""Unit tests for the Langfuse trace proxy read-service (services/traces_read.py)."""
from __future__ import annotations

import base64

import httpx
import pytest

from services.traces_read import LangfuseNotConfigured, map_trace, read_traces

_TRACE = {
    "id": "abc123",
    "name": "qa_pass",
    "timestamp": "2026-06-27T10:00:00Z",
    "latency": 2.5,
    "totalCost": 0.0123,
    "metadata": {"model": "gemma-4-31b", "task_id": "task-9"},
    "scores": [{"name": "g_eval", "value": 87}],
}


@pytest.mark.unit
def test_map_trace_builds_row_and_deeplink():
    row = map_trace(_TRACE, "http://localhost:3010")
    assert row["id"] == "abc123"
    assert row["model"] == "gemma-4-31b"
    assert row["latency_ms"] == 2500
    assert row["cost_usd"] == 0.0123
    assert row["qa_score"] == 87
    assert row["task_id"] == "task-9"
    assert row["web_url"] == "http://localhost:3010/trace/abc123"


@pytest.mark.unit
async def test_read_traces_raises_when_unconfigured():
    async with httpx.AsyncClient() as client:
        with pytest.raises(LangfuseNotConfigured):
            await read_traces(client, host="", public_key="", secret_key="")


@pytest.mark.unit
async def test_read_traces_sends_basic_auth_and_maps():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [_TRACE]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await read_traces(
            client, host="http://localhost:3010", public_key="pk", secret_key="sk", limit=10
        )
    expect = "Basic " + base64.b64encode(b"pk:sk").decode()
    assert seen["auth"] == expect
    assert "/api/public/traces" in seen["url"]
    assert out["stats"]["count"] == 1
    assert out["traces"][0]["web_url"].endswith("/trace/abc123")
