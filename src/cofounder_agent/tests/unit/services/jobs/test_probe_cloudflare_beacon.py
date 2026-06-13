"""Unit tests for ``services/jobs/probe_cloudflare_beacon.py``.

The job is the active outage detector for the Cloudflare page-views beacon
Worker (the silent counterpart to SyncCloudflareAnalyticsJob's ingest). It
POSTs a side-effect-free health ping, publishes reachability on the
``poindexter_cloudflare_beacon_reachable`` gauge, and emits a finding when
the beacon is unreachable.

Mirrors the pattern in test_sync_cloudflare_analytics_job.py — SiteConfig DI
seam, fake ``httpx`` module patched into ``sys.modules``. The gauge is read
back via the public ``REGISTRY.get_sample_value`` accessor.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prometheus_client import REGISTRY

from services.jobs.probe_cloudflare_beacon import ProbeCloudflareBeaconJob

_GAUGE = "poindexter_cloudflare_beacon_reachable"


def _sc(beacon_url: str = "https://beacon.example.workers.dev") -> MagicMock:
    """Mock SiteConfig — wired through the job's `_site_config` config kwarg."""
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "cloudflare_beacon_url": beacon_url,
    }.get(key, default)
    return sc


def _fake_httpx(status: int = 204, raises: Exception | None = None):
    """Build a fake ``httpx`` module whose AsyncClient.post returns ``status``
    (or raises ``raises``)."""
    resp = MagicMock()
    resp.status_code = status

    client = AsyncMock()
    if raises is not None:
        client.post = AsyncMock(side_effect=raises)
    else:
        client.post = AsyncMock(return_value=resp)

    class _AsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return client

        async def __aexit__(self, *args: Any) -> None:
            return None

    fake = MagicMock()
    fake.AsyncClient = _AsyncClient
    return fake, client


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProbeCloudflareBeaconMetadata:
    def test_name(self):
        assert ProbeCloudflareBeaconJob.name == "probe_cloudflare_beacon"

    def test_idempotent(self):
        assert ProbeCloudflareBeaconJob.idempotent is True

    def test_schedule(self):
        assert "every" in ProbeCloudflareBeaconJob.schedule.lower()
        assert "5" in ProbeCloudflareBeaconJob.schedule


# ---------------------------------------------------------------------------
# Skip conditions — nothing to probe, gauge stays healthy, no finding
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeCloudflareBeaconSkips:
    async def test_skips_when_site_config_missing(self):
        with patch(
            "services.jobs.probe_cloudflare_beacon.emit_finding"
        ) as mock_finding:
            result = await ProbeCloudflareBeaconJob().run(MagicMock(), {})
        assert result.ok is True
        assert result.changes_made == 0
        assert "_site_config" in result.detail
        # Absence of config must read as healthy, never as an outage.
        assert REGISTRY.get_sample_value(_GAUGE) == 1.0
        mock_finding.assert_not_called()

    async def test_skips_when_beacon_url_unset(self):
        sc = _sc(beacon_url="")
        with patch(
            "services.jobs.probe_cloudflare_beacon.emit_finding"
        ) as mock_finding:
            result = await ProbeCloudflareBeaconJob().run(
                MagicMock(), {"_site_config": sc}
            )
        assert result.ok is True
        assert result.changes_made == 0
        assert "cloudflare_beacon_url" in result.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 1.0
        mock_finding.assert_not_called()

    async def test_skips_when_httpx_unavailable(self):
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": None}):
            result = await ProbeCloudflareBeaconJob().run(
                MagicMock(), {"_site_config": sc}
            )
        assert result.ok is False
        assert "httpx" in result.detail


# ---------------------------------------------------------------------------
# Reachable — 2xx ⇒ gauge 1, no finding
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeCloudflareBeaconReachable:
    @pytest.mark.parametrize("status", [200, 204, 299])
    async def test_2xx_sets_gauge_healthy_no_finding(self, status: int):
        sc = _sc()
        fake_httpx, client = _fake_httpx(status=status)
        with patch.dict("sys.modules", {"httpx": fake_httpx}), patch(
            "services.jobs.probe_cloudflare_beacon.emit_finding"
        ) as mock_finding:
            result = await ProbeCloudflareBeaconJob().run(
                MagicMock(), {"_site_config": sc}
            )
        assert result.ok is True
        assert "reachable" in result.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 1.0
        mock_finding.assert_not_called()
        # Health ping must be an empty-body POST (Worker returns 204, writes
        # nothing).
        client.post.assert_awaited_once()
        assert client.post.await_args.kwargs.get("json") == {}


# ---------------------------------------------------------------------------
# Unreachable — non-2xx or exception ⇒ gauge 0 + finding (severity warn)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeCloudflareBeaconUnreachable:
    async def test_non_2xx_sets_gauge_down_and_emits_finding(self):
        sc = _sc()
        fake_httpx, _ = _fake_httpx(status=500)
        with patch.dict("sys.modules", {"httpx": fake_httpx}), patch(
            "services.jobs.probe_cloudflare_beacon.emit_finding"
        ) as mock_finding:
            result = await ProbeCloudflareBeaconJob().run(
                MagicMock(), {"_site_config": sc}
            )
        # The probe RAN fine — an unreachable beacon is the observed result,
        # not a job crash, so the job is still ok=True (the gauge + finding
        # carry the outage; marking red would double-alert + back off a
        # working probe).
        assert result.ok is True
        assert "UNREACHABLE" in result.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 0.0
        mock_finding.assert_called_once()
        kwargs = mock_finding.call_args.kwargs
        assert kwargs["severity"] == "warn"
        assert kwargs["kind"] == "cloudflare_beacon_unreachable"
        assert kwargs["dedup_key"] == "cloudflare_beacon_unreachable"
        assert kwargs["source"] == "probe_cloudflare_beacon"

    async def test_connection_exception_sets_gauge_down_and_emits_finding(self):
        sc = _sc()
        fake_httpx, _ = _fake_httpx(raises=ConnectionError("DNS fail"))
        with patch.dict("sys.modules", {"httpx": fake_httpx}), patch(
            "services.jobs.probe_cloudflare_beacon.emit_finding"
        ) as mock_finding:
            result = await ProbeCloudflareBeaconJob().run(
                MagicMock(), {"_site_config": sc}
            )
        assert result.ok is True
        assert "UNREACHABLE" in result.detail
        assert REGISTRY.get_sample_value(_GAUGE) == 0.0
        mock_finding.assert_called_once()
        assert mock_finding.call_args.kwargs["severity"] == "warn"

    async def test_recovery_resets_gauge_to_healthy(self):
        """A down probe followed by a 2xx probe flips the gauge back to 1 —
        the self-healing path the PoindexterCloudflareBeaconDown `for:` window
        relies on to resolve."""
        sc = _sc()
        # First: down.
        fake_down, _ = _fake_httpx(status=503)
        with patch.dict("sys.modules", {"httpx": fake_down}), patch(
            "services.jobs.probe_cloudflare_beacon.emit_finding"
        ):
            await ProbeCloudflareBeaconJob().run(MagicMock(), {"_site_config": sc})
        assert REGISTRY.get_sample_value(_GAUGE) == 0.0
        # Then: recovered.
        fake_up, _ = _fake_httpx(status=204)
        with patch.dict("sys.modules", {"httpx": fake_up}):
            await ProbeCloudflareBeaconJob().run(MagicMock(), {"_site_config": sc})
        assert REGISTRY.get_sample_value(_GAUGE) == 1.0
