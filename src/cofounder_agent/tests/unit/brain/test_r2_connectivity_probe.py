"""Unit tests for ``brain/health_probes.py:probe_r2_connectivity``.

The probe used to read ``os.getenv("R2_PUBLIC_URL", "<one operator's bucket>")``
and ignore its pool. That variable was documented and wired nowhere, and the
default sat in public code, so every install without it probed the operator's
bucket and reported the result as its own: a green R2 signal for a bucket the
install never writes to. It now reads ``app_settings.storage_public_url``, the
setting the worker builds every public object URL from, and has no default.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import health_probes
from poindexter.brain.health_probes import probe_r2_connectivity

_REACHABLE = {"ok": True, "status_code": 200, "detail": "R2 CDN reachable (HTTP 200)"}


def _pool(value=None, *, error=None):
    """asyncpg pool stub whose ``fetchval`` answers the settings read."""
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=value, side_effect=error)
    return pool


@pytest.fixture
def asked(monkeypatch):
    """Replace the network call. The list records each URL the probe asked for."""
    urls: list[str] = []

    def fake_check(url: str) -> dict:
        urls.append(url)
        return dict(_REACHABLE)

    monkeypatch.setattr(health_probes, "_check_r2_sync", fake_check)
    return urls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_probes_the_bucket_the_setting_names(asked):
    pool = _pool("https://cdn.example.com")
    result = await probe_r2_connectivity(pool)
    assert asked == ["https://cdn.example.com"]
    assert result["ok"] is True
    # The key is bound as a parameter, not spliced into the SQL.
    sql, key = pool.fetchval.await_args.args
    assert key == "storage_public_url"
    assert "storage_public_url" not in sql


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["https://cdn.example.com/", "  https://cdn.example.com/  "])
async def test_a_trailing_slash_and_surrounding_space_are_stripped(asked, raw):
    await probe_r2_connectivity(_pool(raw))
    assert asked == ["https://cdn.example.com"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "", "   "])
async def test_an_unset_setting_is_not_a_fault_and_probes_nothing(asked, raw):
    result = await probe_r2_connectivity(_pool(raw))
    assert result["ok"] is True
    assert result["status"] == "not_configured"
    assert "storage_public_url" in result["detail"]
    assert asked == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_bucket_that_fails_still_fails(monkeypatch):
    monkeypatch.setattr(
        health_probes,
        "_check_r2_sync",
        lambda url: {"ok": False, "detail": "R2 CDN unreachable: timed out"},
    )
    result = await probe_r2_connectivity(_pool("https://cdn.example.com"))
    assert result["ok"] is False
    assert "unreachable" in result["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_unreadable_setting_fails_loudly_instead_of_raising(asked):
    result = await probe_r2_connectivity(_pool(error=RuntimeError("connection closed")))
    assert result["ok"] is False
    assert "storage_public_url" in result["detail"]
    assert "connection closed" in result["detail"]
    assert asked == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_environment_no_longer_picks_the_bucket(asked, monkeypatch):
    monkeypatch.setenv("R2_PUBLIC_URL", "https://env.example.com")
    await probe_r2_connectivity(_pool("https://db.example.com"))
    assert asked == ["https://db.example.com"]

    # And it cannot stand in for an unset setting either.
    asked.clear()
    result = await probe_r2_connectivity(_pool(None))
    assert result["status"] == "not_configured"
    assert asked == []


@pytest.mark.unit
def test_the_public_module_carries_no_bucket_host():
    """A literal here is the defect itself, even if it is the current host.

    Any host would do for the behaviour tests above, so nothing else notices a
    default creeping back in. R2's public dev hosts are ``pub-<32 hex>.r2.dev``.
    """
    source = Path(health_probes.__file__).read_text(encoding="utf-8")
    assert re.findall(r"pub-[0-9a-f]{32}\.r2\.dev", source) == []


@pytest.fixture
def bucket(monkeypatch):
    """Stand in for the bucket at the urllib seam, so no test opens a socket.

    ``bucket.requests`` records each request the probe makes and
    ``bucket.status`` is what the next one answers.
    """
    state = SimpleNamespace(requests=[], status=206)

    def fake_urlopen(req, timeout=None):
        state.requests.append(req)
        if state.status >= 400:
            raise urllib.error.HTTPError(req.full_url, state.status, "error", {}, None)
        return SimpleNamespace(status=state.status)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return state


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_request_is_the_setting_plus_the_podcast_feed_path(bucket):
    """No mocks between the setting and the request: the URL the probe fetches
    is the setting's value plus /podcast/feed.xml, and nothing else."""
    result = await probe_r2_connectivity(_pool("https://cdn.example.com/"))
    assert [r.full_url for r in bucket.requests] == [
        "https://cdn.example.com/podcast/feed.xml"
    ]
    assert bucket.requests[0].get_header("Range") == "bytes=0-64"
    assert result["ok"] is True
    assert result["status_code"] == 206


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "ok"),
    [
        (206, True),
        # The bucket answered: the feed just is not there (yet), which is not
        # an R2 outage. R2 answers 403 to a GET for a key that does not exist.
        (404, True),
        (403, True),
        (500, False),
        (503, False),
    ],
)
async def test_which_bucket_answers_count_as_reachable(bucket, status, ok):
    bucket.status = status
    result = await probe_r2_connectivity(_pool("https://cdn.example.com"))
    assert result["ok"] is ok
    assert result["status_code"] == status
