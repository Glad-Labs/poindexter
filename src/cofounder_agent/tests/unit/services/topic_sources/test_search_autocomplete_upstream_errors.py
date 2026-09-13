"""An upstream 5xx from Google's suggest endpoint is a skipped probe, not an
exception. ``asyncio.gather(return_exceptions=True)`` already hid the raise
from the caller, but Sentry's asyncio integration still reported every
Google hiccup as an error (GlitchTip #1767). The probe now returns ``None``
and the caller counts it.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from poindexter.services.topic_sources import search_autocomplete as mod
from poindexter.services.topic_sources.search_autocomplete import SearchAutocompleteSource


class _Resp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = httpx.Request("GET", mod._ENDPOINT)
            raise httpx.HTTPStatusError(f"{self.status_code}", request=req, response=httpx.Response(self.status_code, request=req))

    def json(self) -> Any:
        return self._body


class _Client:
    def __init__(self, plan: dict[str, Any]):
        self._plan = plan

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _url: str, params: dict[str, str]):
        got = self._plan[params["q"]]
        if isinstance(got, int):
            return _Resp(got)
        if isinstance(got, Exception):
            raise got
        return _Resp(200, ["q", got])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_5xx_429_and_timeouts_are_counted_not_raised(monkeypatch, caplog):
    plan = {
        "kv cache quantization how": 500,
        "kv cache quantization": 429,
        "llm routing": httpx.ReadTimeout("slow"),
        "gpu offload": ["gpu offload tips", "gpu offload vram"],
    }
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _Client(plan))
    src = SearchAutocompleteSource.__new__(SearchAutocompleteSource)
    probes = [("kv cache quantization", p) for p in plan if p.startswith("kv")] + [("llm routing", "llm routing"), ("gpu offload", "gpu offload")]
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        out = await src._fetch_all(probes, concurrency=2)
    assert [s for _, s in out] == ["gpu offload tips", "gpu offload vram"]
    assert any("3/4 probes failed" in r.getMessage() for r in caplog.records)
