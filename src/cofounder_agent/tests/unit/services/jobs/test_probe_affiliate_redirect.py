"""Unit tests for ``services/jobs/probe_affiliate_redirect.py``.

The job is the active outage detector for the ``/go/<code>`` affiliate
redirect Worker — the thing that was missing when a clobbered ``LINKS_URL``
could have taken every affiliate link down silently (glad-labs-stack#3520).

The tests that matter here are the ones that pin the *distinctions*, because
before #3520 every failure mode looked identical from outside:

* a 502/503 from the Worker must be treated as an outage, not as a normal
  unknown-code fallback;
* a real code resolving to the homepage instead of its merchant URL is the
  stale/foreign-link-map signature and must fail even though the status code
  is a perfectly ordinary 302;
* an install with no affiliate links must stay HEALTHY — absence of config is
  not an outage;
* the cheap tier must never resolve a real code, since the Worker only writes
  an Analytics Engine click once a target resolves. A regression there would
  silently start inflating affiliate click counts.

Mirrors test_probe_cloudflare_beacon.py — SiteConfig DI seam, fake ``httpx``
patched into the job module, gauge read via ``REGISTRY.get_sample_value``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prometheus_client import REGISTRY

from services.jobs.probe_affiliate_redirect import (
    ProbeAffiliateRedirectJob,
    build_probe_url,
)

_GAUGE = "poindexter_affiliate_redirect_healthy"
_SITE = "https://www.example.com"
_MERCHANT = "https://merchant.example/r/abc"


def _sc(site_url: str = _SITE, base: str = "/go", deep_hours: str = "0") -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "site_url": site_url,
        "affiliate_redirect_base_url": base,
        "affiliate_redirect_probe_deep_interval_hours": deep_hours,
    }.get(key, default)
    return sc


def _pool(link_row: dict[str, Any] | None = None, watermark: str = "") -> MagicMock:
    """Pool whose fetchrow answers both the watermark and the link lookup."""
    conn = AsyncMock()

    async def _fetchrow(sql: str, *args: Any) -> Any:
        if "app_settings" in sql:
            return {"value": watermark}
        return link_row

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.execute = AsyncMock()

    class _Acquire:
        async def __aenter__(self) -> Any:
            return conn

        async def __aexit__(self, *a: Any) -> None:
            return None

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Acquire())
    return pool


def _fake_httpx(responses: list[Any]):
    """Fake httpx whose AsyncClient.get returns each queued response in turn.

    A queued entry may be an Exception, which is raised instead.
    """
    calls: list[tuple[str, dict[str, str]]] = []
    queue = list(responses)

    async def _get(url: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        calls.append((url, headers or {}))
        nxt = queue.pop(0) if queue else queue_default()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def queue_default() -> Any:
        return _resp(302, _SITE + "/")

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_get)

    class _AsyncClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return client

        async def __aexit__(self, *a: Any) -> None:
            return None

    fake = MagicMock()
    fake.AsyncClient = _AsyncClient
    fake.Timeout = MagicMock(return_value=None)
    return fake, calls


def _resp(status: int, location: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = {"location": location} if location else {}
    return r


async def _run(job: ProbeAffiliateRedirectJob, pool: Any, sc: Any, fake: Any):
    with patch.dict("sys.modules", {"httpx": fake}):
        return await job.run(pool, {"_site_config": sc})


# ---------------------------------------------------------------- helpers
class TestBuildProbeUrl:
    def test_path_form_hangs_on_site_url(self) -> None:
        assert build_probe_url("/go", _SITE, "x") == f"{_SITE}/go/x"

    def test_absolute_base_ignores_site_url(self) -> None:
        assert build_probe_url("https://go.example.com", _SITE, "x") == (
            "https://go.example.com/x"
        )

    def test_trailing_slashes_do_not_double_up(self) -> None:
        assert build_probe_url("/go/", _SITE + "/", "x") == f"{_SITE}/go/x"

    def test_path_form_without_site_url_is_unresolvable(self) -> None:
        assert build_probe_url("/go", "", "x") is None


# ---------------------------------------------------------------- cheap tier
@pytest.mark.asyncio
class TestCheapTier:
    async def test_unknown_code_redirecting_home_is_healthy(self) -> None:
        fake, calls = _fake_httpx([_resp(302, _SITE + "/")])
        res = await _run(ProbeAffiliateRedirectJob(), _pool(), _sc(), fake)
        assert res.ok
        assert "healthy" in res.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 1

    async def test_503_is_an_outage_not_a_normal_fallback(self) -> None:
        fake, _ = _fake_httpx([_resp(503)])
        with patch("services.jobs.probe_affiliate_redirect.emit_finding") as ef:
            res = await _run(ProbeAffiliateRedirectJob(), _pool(), _sc(), fake)
        assert res.ok  # probe ran; the Worker is what's broken
        assert "UNHEALTHY" in res.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 0
        assert ef.call_args.kwargs["kind"] == "affiliate_redirect_unhealthy"

    async def test_502_map_unloadable_is_an_outage(self) -> None:
        fake, _ = _fake_httpx([_resp(502)])
        with patch("services.jobs.probe_affiliate_redirect.emit_finding"):
            res = await _run(ProbeAffiliateRedirectJob(), _pool(), _sc(), fake)
        assert "UNHEALTHY" in res.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 0

    async def test_transport_failure_is_an_outage(self) -> None:
        fake, _ = _fake_httpx([RuntimeError("connection refused")])
        with patch("services.jobs.probe_affiliate_redirect.emit_finding"):
            await _run(ProbeAffiliateRedirectJob(), _pool(), _sc(), fake)
        assert REGISTRY.get_sample_value(_GAUGE) == 0

    async def test_cheap_tier_probes_a_code_that_cannot_resolve(self) -> None:
        """The sentinel must not be a real code — a resolving probe would log
        an Analytics Engine click on every cycle and inflate click counts."""
        fake, calls = _fake_httpx([_resp(302, _SITE + "/")])
        await _run(ProbeAffiliateRedirectJob(), _pool(), _sc(), fake)
        url, headers = calls[0]
        assert "__poindexter_liveness_probe__" in url
        # UA must stay bot-classifiable by affiliate_click_bot_ua_pattern.
        assert "monitor" in headers["User-Agent"]


# ---------------------------------------------------------------- deep tier
@pytest.mark.asyncio
class TestDeepTier:
    async def test_real_code_reaching_its_merchant_is_healthy(self) -> None:
        fake, calls = _fake_httpx(
            [_resp(302, _SITE + "/"), _resp(302, _MERCHANT)]
        )
        pool = _pool({"code": "mercury", "url": _MERCHANT})
        res = await _run(ProbeAffiliateRedirectJob(), pool, _sc(deep_hours="24"), fake)
        assert res.ok and "healthy" in res.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 1
        assert res.metrics["deep_check_ran"] == 1
        assert "mercury" in calls[1][0]

    async def test_real_code_falling_through_to_home_is_the_clobber_signature(
        self,
    ) -> None:
        """A stale/foreign link map: status is an ordinary 302, but the code
        resolves to the homepage rather than the merchant. This is precisely
        the case an unknown-slug probe cannot see."""
        fake, _ = _fake_httpx([_resp(302, _SITE + "/"), _resp(302, _SITE + "/")])
        pool = _pool({"code": "mercury", "url": _MERCHANT})
        with patch("services.jobs.probe_affiliate_redirect.emit_finding") as ef:
            res = await _run(
                ProbeAffiliateRedirectJob(), pool, _sc(deep_hours="24"), fake
            )
        assert "UNHEALTHY" in res.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 0
        assert "stale" in ef.call_args.kwargs["body"] or "wrong" in (
            ef.call_args.kwargs["body"]
        )

    async def test_no_affiliate_links_stays_healthy(self) -> None:
        """Absence of configuration must never read as an outage."""
        fake, _ = _fake_httpx([_resp(302, _SITE + "/")])
        pool = _pool(None)
        res = await _run(ProbeAffiliateRedirectJob(), pool, _sc(deep_hours="24"), fake)
        assert res.ok
        assert REGISTRY.get_sample_value(_GAUGE) == 1

    async def test_deep_tier_is_skipped_when_interval_is_zero(self) -> None:
        fake, calls = _fake_httpx([_resp(302, _SITE + "/")])
        pool = _pool({"code": "mercury", "url": _MERCHANT})
        res = await _run(ProbeAffiliateRedirectJob(), pool, _sc(deep_hours="0"), fake)
        assert res.metrics["deep_check_ran"] == 0
        assert len(calls) == 1  # cheap tier only — no click logged

    async def test_deep_tier_skipped_while_watermark_is_fresh(self) -> None:
        from datetime import datetime, timezone

        fresh = datetime.now(timezone.utc).isoformat()
        fake, calls = _fake_httpx([_resp(302, _SITE + "/")])
        pool = _pool({"code": "mercury", "url": _MERCHANT}, watermark=fresh)
        res = await _run(ProbeAffiliateRedirectJob(), pool, _sc(deep_hours="24"), fake)
        assert res.metrics["deep_check_ran"] == 0
        assert len(calls) == 1


# ---------------------------------------------------------------- skips
@pytest.mark.asyncio
class TestSkips:
    async def test_no_site_config_skips_and_holds_gauge_healthy(self) -> None:
        res = await ProbeAffiliateRedirectJob().run(_pool(), {})
        assert res.ok and "skipping" in res.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 1

    async def test_path_base_without_site_url_skips(self) -> None:
        fake, calls = _fake_httpx([_resp(302)])
        res = await _run(
            ProbeAffiliateRedirectJob(), _pool(), _sc(site_url=""), fake
        )
        assert res.ok and "nothing to probe" in res.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 1
        assert calls == []
