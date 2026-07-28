"""Tests for the ``list_social_drafts`` MCP tool.

``GET /api/social/drafts`` is paged (poindexter#927) because
``social_post_drafts`` only grows and nothing prunes terminal rows. The tool
must send a bound and be able to page past it — a caller that cannot reach
``offset`` would silently see only the first window of a larger table.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import server  # noqa: E402


def _query(api_mock) -> dict[str, list[str]]:
    path = api_mock.await_args[0][1]
    return parse_qs(urlparse(path).query)


@pytest.mark.asyncio
async def test_sends_default_limit():
    with patch.object(server, "_api", AsyncMock(return_value={})) as api:
        await server.list_social_drafts()
    assert _query(api) == {"limit": ["50"]}


@pytest.mark.asyncio
async def test_paging_past_the_cap():
    with patch.object(server, "_api", AsyncMock(return_value={})) as api:
        await server.list_social_drafts(limit=100, offset=50)
    assert _query(api) == {"limit": ["100"], "offset": ["50"]}


@pytest.mark.asyncio
async def test_filters_compose_with_pagination():
    with patch.object(server, "_api", AsyncMock(return_value={})) as api:
        await server.list_social_drafts(
            post_id="p-1", task_id="t-1", status="pending", limit=10
        )
    assert _query(api) == {
        "post_id": ["p-1"],
        "task_id": ["t-1"],
        "status": ["pending"],
        "limit": ["10"],
    }


@pytest.mark.asyncio
async def test_returns_counts_so_callers_do_not_count_the_window():
    body = {
        "drafts": [{"id": "d-1", "status": "pending"}],
        "total": 77,
        "limit": 1,
        "offset": 0,
        "status_counts": {"pending": 10, "posted": 26, "rejected": 41},
    }
    with patch.object(server, "_api", AsyncMock(return_value=body)):
        out = json.loads(await server.list_social_drafts(limit=1))
    assert out["total"] == 77
    assert out["status_counts"]["rejected"] == 41
    assert len(out["drafts"]) == 1
