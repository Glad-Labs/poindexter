"""Unit tests — verify_published_posts audit_log mirror-write visibility.

Silent-excepts OLD-debt burn-down (batch 17). The job mirrors each detected
verification failure into ``audit_log`` (``publish_verify_failed`` /
``publish_verify_edge_blocked``). Both inserts sit in per-item loops whose
handlers swallowed at DEBUG only — invisible in prod (Loki ships INFO+).

Two things make this case unusual, and they shape the fix:

1. The business signal is NOT lost. The same events are surfaced by sibling
   ``emit_finding`` calls in the same function — ``post_verification_failure``
   (severity ``critical``) and ``verify_blocked_by_edge`` (``warning``) — each
   carrying the full item list. So the swallow loses a duplicate dashboard row,
   not the alert.
2. **A finding cannot report this failure.** ``emit_finding`` itself writes to
   ``audit_log`` (via ``audit_log_bg``), so if that table is broken the finding
   vanishes with it. The only surface that survives an audit_log outage is the
   logger — hence ``logger.warning`` (Loki) rather than ``emit_finding`` here.
   (Contrast batch 16, where the failing write was ``cost_logs`` — a different
   table — so a finding was the right signal.)

Fix: each handler increments a counter (an assignment is a visibility signal to
the lint), and ONE aggregate ``logger.warning`` fires after the loops — never
one per iteration (write/log amplification, per the batch-5 aggregate pattern).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.verify_published_posts import VerifyPublishedPostsJob

_LOGGER_NAME = "services.jobs.verify_published_posts"


def _sc(site_url: str = "https://gladlabs.io") -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda k, d=None: site_url if k == "site_url" else d
    return sc


def _make_pool(rows: list[dict] | None = None) -> Any:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class _FakeResp:
    def __init__(self, status_code: int = 200, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}


def _patched_client(status_map: dict[str, Any]):
    async def _get(url: str, timeout: float = 10) -> _FakeResp:
        outcome = status_map.get(url, 200)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResp(status_code=outcome)

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(side_effect=_get)
    return client


def _audit_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and "audit_log" in r.getMessage()
    ]


@pytest.mark.asyncio
async def test_audit_insert_failures_emit_one_aggregate_warning(caplog):
    """Two failed mirror-writes produce ONE warning carrying the count."""
    pool, conn = _make_pool([
        {"id": "p1", "title": "A", "slug": "a"},
        {"id": "p2", "title": "B", "slug": "b"},
    ])
    conn.execute = AsyncMock(side_effect=RuntimeError("audit insert boom"))
    client = _patched_client({
        "https://gladlabs.io/posts/a": 404,
        "https://gladlabs.io/posts/b": 500,
    })

    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    with patch(
        "services.jobs.verify_published_posts.httpx.AsyncClient", return_value=client,
    ), patch(
        "services.jobs.verify_published_posts.emit_finding", new=MagicMock(),
    ):
        job = VerifyPublishedPostsJob()
        result = await job.run(pool, {"_site_config": _sc()})

    # The job still completes — mirroring is best-effort.
    assert result.metrics["posts_failed"] == 2

    warnings = _audit_warnings(caplog)
    # Exactly ONE aggregate warning, not one per failed insert.
    assert len(warnings) == 1
    assert "2" in warnings[0]


@pytest.mark.asyncio
async def test_successful_audit_inserts_emit_no_warning(caplog):
    """Healthy mirror-writes stay quiet — no aggregate warning."""
    pool, _conn = _make_pool([
        {"id": "p1", "title": "A", "slug": "a"},
    ])
    client = _patched_client({"https://gladlabs.io/posts/a": 404})

    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    with patch(
        "services.jobs.verify_published_posts.httpx.AsyncClient", return_value=client,
    ), patch(
        "services.jobs.verify_published_posts.emit_finding", new=MagicMock(),
    ):
        job = VerifyPublishedPostsJob()
        result = await job.run(pool, {"_site_config": _sc()})

    assert result.metrics["posts_failed"] == 1
    assert _audit_warnings(caplog) == []
