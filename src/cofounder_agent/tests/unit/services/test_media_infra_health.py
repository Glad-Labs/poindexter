"""Unit tests for ``services/media_infra_health.py``.

The probe pass gates Stage-2 media dispatch + the reconciliation cap-reset
self-heal (2026-07-03): wan-server ``/health`` + image-gen ``/health`` + a DNS
canary. All network is mocked — the http_client_factory seam takes a fake
client, and the DNS canary is patched at ``socket.getaddrinfo``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import media_infra_health as mih
from services.site_config import SiteConfig


def _sc(**overrides):
    base = {
        # Explicit URLs so the resolvers never fall through to module
        # defaults in tests.
        "wan_server_url": "http://wan.test:9840",
        "image_gen_server_url": "http://imagegen.test:9836",
        # No canary by default: host unset + storage_public_url unset → skip.
        "media_infra_dns_canary_host": "",
        "storage_public_url": "",
        # VRAM gate off by default here so the wan/image-gen/DNS probe tests
        # aren't coupled to a Prometheus read; TestVramGate enables it.
        "media_render_vram_gate_enabled": "false",
    }
    base.update(overrides)
    return SiteConfig(initial_config=base)


def _client_factory(
    status_by_url: dict[str, int], *, raise_for: frozenset[str] | set[str] = frozenset(),
):
    """Fake ``httpx.AsyncClient`` factory: GET returns the mapped status, or
    raises ConnectionError for URLs in ``raise_for``."""

    class _FakeClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            if url in raise_for:
                raise ConnectionError(f"refused: {url}")
            resp = MagicMock()
            resp.status_code = status_by_url.get(url, 404)
            return resp

    return _FakeClient


_WAN_HEALTH = "http://wan.test:9840/health"
_IMAGE_GEN_HEALTH = "http://imagegen.test:9836/health"


@pytest.mark.unit
class TestVramGate:
    """Render-GPU free-VRAM preflight (2026-07-12 desktop-lockup fix).

    Patches ``render_gpu_free_vram_gb`` at its source module (the health check
    imports it locally, so the source binding is what's resolved at call time).
    """

    @pytest.mark.asyncio
    async def test_insufficient_vram_defers(self):
        factory = _client_factory({_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200})
        with patch(
            "services.render_vram.render_gpu_free_vram_gb",
            new=AsyncMock(return_value=20.0),
        ):
            out = await mih.check_media_infra_health(
                _sc(
                    media_render_vram_gate_enabled="true",
                    media_render_min_free_vram_gb="25",
                ),
                http_client_factory=factory,
            )
        assert out.healthy is False
        assert out.vram_insufficient is True
        assert "VRAM" in out.detail

    @pytest.mark.asyncio
    async def test_sufficient_vram_is_healthy(self):
        factory = _client_factory({_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200})
        with patch(
            "services.render_vram.render_gpu_free_vram_gb",
            new=AsyncMock(return_value=27.0),
        ):
            out = await mih.check_media_infra_health(
                _sc(
                    media_render_vram_gate_enabled="true",
                    media_render_min_free_vram_gb="25",
                ),
                http_client_factory=factory,
            )
        assert out.healthy is True
        assert out.vram_insufficient is False

    @pytest.mark.asyncio
    async def test_unreadable_vram_fails_closed(self):
        factory = _client_factory({_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200})
        with patch(
            "services.render_vram.render_gpu_free_vram_gb",
            new=AsyncMock(return_value=None),
        ):
            out = await mih.check_media_infra_health(
                _sc(media_render_vram_gate_enabled="true"),
                http_client_factory=factory,
            )
        assert out.healthy is False
        assert out.vram_insufficient is True

    @pytest.mark.asyncio
    async def test_gate_disabled_skips_vram_probe(self):
        factory = _client_factory({_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200})
        probe = AsyncMock(return_value=1.0)
        with patch("services.render_vram.render_gpu_free_vram_gb", new=probe):
            out = await mih.check_media_infra_health(
                _sc(media_render_vram_gate_enabled="false"),
                http_client_factory=factory,
            )
        assert out.healthy is True
        probe.assert_not_called()


@pytest.mark.unit
class TestCheckMediaInfraHealth:

    @pytest.mark.asyncio
    async def test_all_probes_pass_is_healthy(self):
        factory = _client_factory({_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200})
        out = await mih.check_media_infra_health(_sc(), http_client_factory=factory)
        assert out.healthy is True

    @pytest.mark.asyncio
    async def test_wan_5xx_is_unhealthy_with_detail(self):
        factory = _client_factory({_WAN_HEALTH: 503, _IMAGE_GEN_HEALTH: 200})
        out = await mih.check_media_infra_health(_sc(), http_client_factory=factory)
        assert out.healthy is False
        assert "wan-server" in out.detail
        assert "503" in out.detail

    @pytest.mark.asyncio
    async def test_image_gen_unreachable_is_unhealthy(self):
        factory = _client_factory({_WAN_HEALTH: 200}, raise_for={_IMAGE_GEN_HEALTH})
        out = await mih.check_media_infra_health(_sc(), http_client_factory=factory)
        assert out.healthy is False
        assert "image-gen" in out.detail
        assert "unreachable" in out.detail

    @pytest.mark.asyncio
    async def test_both_down_reports_both(self):
        factory = _client_factory({}, raise_for={_WAN_HEALTH, _IMAGE_GEN_HEALTH})
        out = await mih.check_media_infra_health(_sc(), http_client_factory=factory)
        assert out.healthy is False
        assert "wan-server" in out.detail and "image-gen" in out.detail

    @pytest.mark.asyncio
    async def test_master_switch_off_short_circuits_healthy(self):
        """Disabled → healthy without any HTTP (OSS forks without a wan
        sidecar must not have dispatch bricked by a probe)."""
        factory = MagicMock(side_effect=AssertionError("must not build a client"))
        out = await mih.check_media_infra_health(
            _sc(media_infra_healthcheck_enabled="false"),
            http_client_factory=factory,
        )
        assert out.healthy is True
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_site_config_is_healthy_skip(self):
        out = await mih.check_media_infra_health(None)
        assert out.healthy is True
        assert "skipped" in out.detail

    @pytest.mark.asyncio
    async def test_dns_canary_failure_is_unhealthy(self):
        factory = _client_factory({_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200})
        sc = _sc(media_infra_dns_canary_host="r2.example.dev")
        with patch.object(
            mih.socket, "getaddrinfo", side_effect=OSError("no resolver"),
        ):
            out = await mih.check_media_infra_health(sc, http_client_factory=factory)
        assert out.healthy is False
        assert "DNS canary" in out.detail
        assert "r2.example.dev" in out.detail

    @pytest.mark.asyncio
    async def test_dns_canary_pass_is_healthy(self):
        factory = _client_factory({_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200})
        sc = _sc(media_infra_dns_canary_host="r2.example.dev")
        with patch.object(mih.socket, "getaddrinfo", return_value=[("stub",)]):
            out = await mih.check_media_infra_health(sc, http_client_factory=factory)
        assert out.healthy is True

    @pytest.mark.asyncio
    async def test_dns_canary_skipped_when_unconfigured(self):
        """No explicit canary host AND no storage_public_url → the DNS probe
        is skipped entirely (never resolves anything)."""
        factory = _client_factory({_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200})
        getaddrinfo = MagicMock(side_effect=AssertionError("must not resolve"))
        with patch.object(mih.socket, "getaddrinfo", getaddrinfo):
            out = await mih.check_media_infra_health(
                _sc(), http_client_factory=factory,
            )
        assert out.healthy is True
        getaddrinfo.assert_not_called()

    @pytest.mark.asyncio
    async def test_dns_canary_derived_from_storage_public_url(self):
        """Canary host unset → derive the host from storage_public_url (the
        render's delivery target)."""
        factory = _client_factory({_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200})
        sc = _sc(storage_public_url="https://media.gladlabs.io/assets")
        seen: list[str] = []

        def _fake_getaddrinfo(host, *_a, **_kw):
            seen.append(host)
            return [("stub",)]

        with patch.object(mih.socket, "getaddrinfo", _fake_getaddrinfo):
            out = await mih.check_media_infra_health(sc, http_client_factory=factory)
        assert out.healthy is True
        assert seen == ["media.gladlabs.io"]


@pytest.mark.unit
class TestUrlResolution:

    def test_wan_health_url_prefers_flat_setting(self):
        assert mih._resolve_wan_health_url(_sc()) == _WAN_HEALTH

    def test_wan_health_url_falls_back_to_module_default(self):
        sc = SiteConfig(initial_config={})
        assert (
            mih._resolve_wan_health_url(sc)
            == "http://host.docker.internal:9840/health"
        )

    def test_image_gen_health_url_default(self):
        sc = SiteConfig(initial_config={})
        assert (
            mih._resolve_image_gen_health_url(sc)
            == "http://image-gen-server:9836/health"
        )

    def test_canary_explicit_wins_over_storage_url(self):
        sc = _sc(
            media_infra_dns_canary_host="canary.test",
            storage_public_url="https://media.gladlabs.io",
        )
        assert mih._resolve_dns_canary_host(sc) == "canary.test"
