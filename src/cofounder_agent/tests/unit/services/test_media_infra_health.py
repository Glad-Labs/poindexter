"""Unit tests for ``services/media_infra_health.py``.

The probe pass gates Stage-2 media dispatch + the reconciliation cap-reset
self-heal (2026-07-03): the configured hero animator (wan-server ``/health``,
or ComfyUI ``/system_stats`` since 2026-09-25) + image-gen ``/health`` + a DNS
canary. All network is mocked — the http_client_factory seam takes a fake
client, and the DNS canary is patched at ``socket.getaddrinfo``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import media_infra_health as mih
from poindexter.services.site_config import SiteConfig


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
    status_by_url: dict[str, int],
    *,
    raise_for: frozenset[str] | set[str] = frozenset(),
    seen: list[str] | None = None,
):
    """Fake ``httpx.AsyncClient`` factory: GET returns the mapped status, or
    raises ConnectionError for URLs in ``raise_for``. ``seen`` collects every
    URL requested, for tests that pin what is NOT probed."""

    class _FakeClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            if seen is not None:
                seen.append(url)
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
            "poindexter.services.render_vram.render_gpu_free_vram_gb",
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
            "poindexter.services.render_vram.render_gpu_free_vram_gb",
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
            "poindexter.services.render_vram.render_gpu_free_vram_gb",
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
        with patch("poindexter.services.render_vram.render_gpu_free_vram_gb", new=probe):
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


_COMFYUI_HEALTH = "http://comfy.test:8188/system_stats"


@pytest.mark.unit
class TestAnimatorProbe:
    """The gate probes the animator the render's hero clips will call
    (2026-09-25). Under ComfyUI, wan-server renders nothing: it is only the
    renderer's live VRAM probe, which falls back to Prometheus without it.
    Probing wan unconditionally deferred every render during a wan outage the
    render would never have noticed, and never probed ComfyUI, whose outage
    turns every hero into a still and every presenter into a brand card."""

    @pytest.mark.asyncio
    async def test_comfyui_animator_probes_comfyui_and_not_wan(self):
        seen: list[str] = []
        factory = _client_factory(
            {_COMFYUI_HEALTH: 200, _IMAGE_GEN_HEALTH: 200},
            raise_for={_WAN_HEALTH},
            seen=seen,
        )
        out = await mih.check_media_infra_health(
            _sc(
                video_generative_provider="comfyui",
                video_comfyui_server_url="http://comfy.test:8188",
            ),
            http_client_factory=factory,
        )
        assert out.healthy is True, out.detail
        assert _COMFYUI_HEALTH in seen
        assert _WAN_HEALTH not in seen

    @pytest.mark.asyncio
    async def test_comfyui_down_defers_and_names_the_restart(self):
        factory = _client_factory(
            {_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200},
            raise_for={_COMFYUI_HEALTH},
        )
        out = await mih.check_media_infra_health(
            _sc(
                video_generative_provider="comfyui",
                video_comfyui_server_url="http://comfy.test:8188/",
            ),
            http_client_factory=factory,
        )
        assert out.healthy is False
        assert "comfyui http://comfy.test:8188/system_stats unreachable" in out.detail
        assert "--profile comfyui" in out.detail
        assert out.vram_insufficient is False  # a reclaim can't start a sidecar

    @pytest.mark.asyncio
    async def test_comfyui_http_error_defers(self):
        factory = _client_factory({_COMFYUI_HEALTH: 500, _IMAGE_GEN_HEALTH: 200})
        out = await mih.check_media_infra_health(
            _sc(
                video_generative_provider="comfyui",
                video_comfyui_server_url="http://comfy.test:8188",
            ),
            http_client_factory=factory,
        )
        assert out.healthy is False
        assert "returned HTTP 500" in out.detail

    @pytest.mark.asyncio
    async def test_wan_animator_probes_wan_and_not_comfyui(self):
        """The default install: wan renders the heroes, so a wan outage still
        defers, and ComfyUI (profile-gated, absent on most installs) is never
        asked about."""
        seen: list[str] = []
        factory = _client_factory(
            {_WAN_HEALTH: 503, _IMAGE_GEN_HEALTH: 200},
            raise_for={_COMFYUI_HEALTH},
            seen=seen,
        )
        out = await mih.check_media_infra_health(
            _sc(video_comfyui_server_url="http://comfy.test:8188"),
            http_client_factory=factory,
        )
        assert out.healthy is False
        assert "wan-server" in out.detail
        assert _COMFYUI_HEALTH not in seen

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("comfyui", "comfyui"),
            (" ComfyUI ", "comfyui"),
            ("wan21", "wan-server"),
            ("", "wan-server"),
            # No such provider: the renderer builds Wan21Provider for it, so
            # the gate must watch wan too.
            ("ltx", "wan-server"),
        ],
    )
    def test_gate_and_renderer_resolve_the_same_animator(self, value, expected):
        """Derived, not hand-listed: the probe follows the same reading the
        renderer uses to pick the provider."""
        from poindexter.services.video_renderers import shot_list_renderer as slr

        sc = _sc(video_generative_provider=value)
        name, _url = mih._resolve_animator_probe(sc)
        assert name == expected
        assert (name == "comfyui") is slr._hero_animator_is_comfyui(sc)

    def test_comfyui_health_url_default(self):
        sc = SiteConfig(initial_config={})
        assert (
            mih._resolve_comfyui_health_url(sc)
            == "http://comfyui:8188/system_stats"
        )


_CHATTERBOX_HEALTH = "http://chatterbox:8000/health"
_SPEACHES_HEALTH = "http://speaches:8000/health"


@pytest.mark.unit
class TestTtsGate:
    """TTS-engine probe (2026-08-15): a down TTS sidecar must defer dispatch
    instead of letting the fail-soft narration ship silent, caption-less
    videos (the 08-13 chatterbox two-day outage)."""

    @pytest.mark.asyncio
    async def test_chatterbox_down_is_unhealthy_with_remediation(self):
        factory = _client_factory(
            {_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200},
            raise_for={_CHATTERBOX_HEALTH},
        )
        out = await mih.check_media_infra_health(
            _sc(podcast_tts_enabled="true", podcast_tts_engine="chatterbox"),
            http_client_factory=factory,
        )
        assert out.healthy is False
        assert "tts-chatterbox" in out.detail
        assert "tts-hq" in out.detail  # names the exact restart command
        assert out.vram_insufficient is False  # a reclaim can't fix TTS

    @pytest.mark.asyncio
    async def test_speaches_down_is_unhealthy(self):
        factory = _client_factory(
            {_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200},
            raise_for={_SPEACHES_HEALTH},
        )
        out = await mih.check_media_infra_health(
            _sc(podcast_tts_enabled="true"),  # engine unset → speaches
            http_client_factory=factory,
        )
        assert out.healthy is False
        assert "tts-speaches" in out.detail
        assert "poindexter-speaches" in out.detail

    @pytest.mark.asyncio
    async def test_tts_healthy_passes(self):
        factory = _client_factory(
            {_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200, _CHATTERBOX_HEALTH: 200},
        )
        out = await mih.check_media_infra_health(
            _sc(podcast_tts_enabled="true", podcast_tts_engine="chatterbox"),
            http_client_factory=factory,
        )
        assert out.healthy is True

    @pytest.mark.asyncio
    async def test_tts_disabled_install_is_never_gated(self):
        """podcast_tts_enabled off = silent renders are the operator's
        choice; the TTS endpoint must not even be probed."""
        factory = _client_factory(
            {_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200},
            raise_for={_CHATTERBOX_HEALTH, _SPEACHES_HEALTH},
        )
        out = await mih.check_media_infra_health(
            _sc(podcast_tts_enabled="false", podcast_tts_engine="chatterbox"),
            http_client_factory=factory,
        )
        assert out.healthy is True

    @pytest.mark.asyncio
    async def test_gate_switch_off_skips_probe(self):
        factory = _client_factory(
            {_WAN_HEALTH: 200, _IMAGE_GEN_HEALTH: 200},
            raise_for={_CHATTERBOX_HEALTH},
        )
        out = await mih.check_media_infra_health(
            _sc(
                podcast_tts_enabled="true",
                podcast_tts_engine="chatterbox",
                media_tts_gate_enabled="false",
            ),
            http_client_factory=factory,
        )
        assert out.healthy is True

    def test_resolve_url_chatterbox_strips_v1(self):
        sc = _sc(podcast_tts_engine="chatterbox")
        assert mih.resolve_tts_health_url(sc) == ("chatterbox", _CHATTERBOX_HEALTH)

    def test_resolve_url_chatterbox_empty_base_falls_to_default(self):
        """Prod seeds plugin.tts_provider.chatterbox.base_url as '' (unset);
        the resolver must fall through to the provider default, mirroring
        ChatterboxTTSProvider."""
        sc = _sc(
            podcast_tts_engine="chatterbox",
            **{"plugin.tts_provider.chatterbox.base_url": ""},
        )
        assert mih.resolve_tts_health_url(sc) == ("chatterbox", _CHATTERBOX_HEALTH)

    def test_resolve_url_default_engine_is_speaches(self):
        assert mih.resolve_tts_health_url(_sc()) == ("speaches", _SPEACHES_HEALTH)

    def test_resolve_url_custom_speaches_base(self):
        sc = _sc(podcast_tts_base_url="http://tts.internal:9999/v1")
        assert mih.resolve_tts_health_url(sc) == (
            "speaches", "http://tts.internal:9999/health",
        )


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
