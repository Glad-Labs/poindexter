"""Unit tests for ``services/jobs/probe_wan_ip_change.py``.

The job records this host's public egress IP and emits a finding when it
moves, so that an IP-allowlisted integration returning 401 (Mercury) reads as
"the WAN address changed" rather than as an expired token.

The invariants worth protecting are all about *not lying*:

  - the first run seeds a baseline and stays SILENT — an empty baseline means
    "not yet observed", never "it changed";
  - a lookup failure is not a change;
  - a non-IP payload (banner, HTML error, rate-limit text) must neither alert
    nor overwrite the stored baseline, or one bad response fakes a change and
    poisons the comparison for every run after it;
  - the dedup key is per-destination-IP, so a second change during a cooldown
    cannot be collapsed into silence.

Mirrors test_probe_cloudflare_beacon.py — SiteConfig DI seam plus a fake
``httpx`` module patched into ``sys.modules``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.probe_wan_ip_change import ProbeWanIpChangeJob, _looks_like_ip

_MOD = "services.jobs.probe_wan_ip_change"


def _sc(last_seen: str = "", enabled: str = "true",
        url: str = "https://ip.example") -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "wan_ip_probe_enabled": enabled,
        "wan_ip_probe_url": url,
        "wan_ip_last_seen": last_seen,
    }.get(key, default)
    return sc


def _fake_httpx(text: str = "72.82.6.67", raises: Exception | None = None):
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()

    client = AsyncMock()
    if raises is not None:
        client.get = AsyncMock(side_effect=raises)
    else:
        client.get = AsyncMock(return_value=resp)

    class _AsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return client

        async def __aexit__(self, *args: Any) -> None:
            return None

    fake = MagicMock()
    fake.AsyncClient = _AsyncClient
    fake.Timeout = MagicMock()
    return fake


def _pool() -> MagicMock:
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    pool._conn = conn
    return pool


@pytest.mark.unit
class TestMetadata:
    def test_name(self):
        assert ProbeWanIpChangeJob.name == "probe_wan_ip_change"

    def test_idempotent(self):
        assert ProbeWanIpChangeJob.idempotent is True

    def test_schedule(self):
        assert "every" in ProbeWanIpChangeJob.schedule.lower()


@pytest.mark.unit
class TestIpGuard:
    @pytest.mark.parametrize("value", ["72.82.6.67", "2001:db8::1", "8.8.8.8"])
    def test_accepts_bare_ips(self, value):
        assert _looks_like_ip(value) is True

    @pytest.mark.parametrize(
        "value",
        ["", "  ", "rate limited", "<html>error</html>", "999.1.1.1",
         "72.82.6.67 (cached)", "not an ip", "x" * 50],
    )
    def test_rejects_everything_else(self, value):
        """This guard is what stands between a flaky echo service and a
        poisoned baseline — it must be strict."""
        assert _looks_like_ip(value) is False


@pytest.mark.unit
class TestProbeBehaviour:
    @pytest.mark.asyncio
    async def test_first_run_seeds_baseline_silently(self):
        """No stored baseline means 'not yet observed'. Emitting a change
        finding here would fire on every fresh install and on any wipe of the
        setting — training the operator to ignore the one alert that matters."""
        pool = _pool()
        with patch.dict("sys.modules", {"httpx": _fake_httpx("72.82.6.67")}), \
             patch(f"{_MOD}.emit_finding") as emit:
            r = await ProbeWanIpChangeJob().run(pool, {"_site_config": _sc(last_seen="")})
        assert r.ok is True
        emit.assert_not_called()
        assert "baseline" in r.detail
        pool._conn.execute.assert_awaited()  # baseline persisted

    @pytest.mark.asyncio
    async def test_unchanged_ip_is_silent_and_writes_nothing(self):
        pool = _pool()
        with patch.dict("sys.modules", {"httpx": _fake_httpx("72.82.6.67")}), \
             patch(f"{_MOD}.emit_finding") as emit:
            r = await ProbeWanIpChangeJob().run(
                pool, {"_site_config": _sc(last_seen="72.82.6.67")}
            )
        assert r.ok is True
        assert r.changes_made == 0
        emit.assert_not_called()
        pool._conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_changed_ip_emits_finding_and_persists(self):
        pool = _pool()
        with patch.dict("sys.modules", {"httpx": _fake_httpx("203.0.113.9")}), \
             patch(f"{_MOD}.emit_finding") as emit:
            r = await ProbeWanIpChangeJob().run(
                pool, {"_site_config": _sc(last_seen="72.82.6.67")}
            )
        assert r.ok is True
        assert r.changes_made == 1
        emit.assert_called_once()
        kw = emit.call_args.kwargs
        assert kw["kind"] == "wan_ip_changed"
        assert kw["severity"] == "warn"
        # Both addresses must appear — the old one is what makes the finding
        # correlatable with whatever started 401ing.
        assert "72.82.6.67" in kw["title"] and "203.0.113.9" in kw["title"]
        assert "Mercury" in kw["body"]
        pool._conn.execute.assert_awaited()

    @pytest.mark.asyncio
    async def test_dedup_key_is_per_destination_ip(self):
        """A kind-level dedup key would collapse a SECOND change during the
        window into silence — exactly the fire you need to see, because the
        allowlist you just edited is already stale."""
        pool = _pool()
        with patch.dict("sys.modules", {"httpx": _fake_httpx("203.0.113.9")}), \
             patch(f"{_MOD}.emit_finding") as emit:
            await ProbeWanIpChangeJob().run(
                pool, {"_site_config": _sc(last_seen="72.82.6.67")}
            )
        assert emit.call_args.kwargs["dedup_key"] == "wan_ip_changed:203.0.113.9"

    @pytest.mark.asyncio
    async def test_lookup_failure_is_not_a_change(self):
        """A flaky echo service must not alert, must not overwrite the
        baseline, and must not mark the job failed (which would trip
        apscheduler back-off on a healthy probe)."""
        pool = _pool()
        fake = _fake_httpx(raises=RuntimeError("connection reset"))
        with patch.dict("sys.modules", {"httpx": fake}), \
             patch(f"{_MOD}.emit_finding") as emit:
            r = await ProbeWanIpChangeJob().run(
                pool, {"_site_config": _sc(last_seen="72.82.6.67")}
            )
        assert r.ok is True
        emit.assert_not_called()
        pool._conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_ip_payload_neither_alerts_nor_poisons_baseline(self):
        """The failure this prevents: an echo service returns an HTML error
        page, the probe reads it as 'the IP changed to <html>...', alerts, AND
        stores it — so the next real change compares against garbage."""
        pool = _pool()
        with patch.dict("sys.modules", {"httpx": _fake_httpx("<html>502</html>")}), \
             patch(f"{_MOD}.emit_finding") as emit:
            r = await ProbeWanIpChangeJob().run(
                pool, {"_site_config": _sc(last_seen="72.82.6.67")}
            )
        assert r.ok is True
        emit.assert_not_called()
        pool._conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_switch_skips(self):
        pool = _pool()
        with patch.dict("sys.modules", {"httpx": _fake_httpx()}), \
             patch(f"{_MOD}.emit_finding") as emit:
            r = await ProbeWanIpChangeJob().run(
                pool, {"_site_config": _sc(enabled="false")}
            )
        assert r.ok is True
        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_unset_url_skips_rather_than_failing(self):
        """Absence of config must never read as a fault."""
        pool = _pool()
        with patch.dict("sys.modules", {"httpx": _fake_httpx()}), \
             patch(f"{_MOD}.emit_finding") as emit:
            r = await ProbeWanIpChangeJob().run(pool, {"_site_config": _sc(url="")})
        assert r.ok is True
        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_site_config_skips(self):
        pool = _pool()
        with patch(f"{_MOD}.emit_finding") as emit:
            r = await ProbeWanIpChangeJob().run(pool, {})
        assert r.ok is True
        emit.assert_not_called()
