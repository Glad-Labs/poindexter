"""Unit tests for ``services/video_providers/wan2_1.py``.

GitHub issue #124 — Wan 2.1 T2V 1.3B (Apache-2.0). The provider speaks
to a separate Wan inference server (default port 9840) and mirrors the
SDXL/FLUX sidecars' response shapes (raw video bytes OR JSON with
``video_path``). Tests mock ``httpx.AsyncClient`` so we never touch a
real server (and the operator hasn't stood one up yet — that's
explicitly out of scope for this PR).

License-conformance smoke test below double-checks the metadata claims
Apache-2.0. The 14B sibling is a separate ticket; tests here lock the
1.3B identity down so a future "rename to wan2.1" PR doesn't
accidentally absorb the 14B path's heavier VRAM footprint.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.video_provider import VideoProvider, VideoResult
from plugins.registry import clear_registry_cache, get_video_providers
from services.video_providers.wan2_1 import (
    Wan21Provider,
    _resolve_negative,
    _resolve_server_url,
)


# ---------------------------------------------------------------------------
# Helpers — mirror the FluxSchnell test fixtures.
# ---------------------------------------------------------------------------


def _video_response(
    content: bytes = b"\x00\x00\x00\x18ftypmp42 wan",
    elapsed: str = "32.4",
):
    """Fake httpx Response representing a successful Wan server reply."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "video/mp4", "X-Elapsed-Seconds": elapsed}
    resp.content = content
    return resp


def _error_response(status: int = 500, text: str = "internal error"):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "text/plain"}
    resp.text = text
    resp.content = b""
    return resp


def _html_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/html"}
    resp.text = "<html>error</html>"
    resp.content = b""
    return resp


def _json_sidecar_response(
    video_path: str,
    width: int = 832,
    height: int = 480,
    elapsed_ms: int = 32400,
):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json = MagicMock(
        return_value={
            "video_path": video_path,
            "filename": video_path.rsplit("/", 1)[-1],
            "width": width,
            "height": height,
            "model": "wan2.1-1.3b",
            "generation_time_ms": elapsed_ms,
            "seed": 42,
        },
    )
    resp.text = ""
    return resp


@contextmanager
def _mock_httpx_post(response):
    """Patch httpx.AsyncClient to yield a client whose .post returns
    ``response``. When ``response`` is an Exception it's raised — used
    to simulate an unreachable inference server.
    """
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if isinstance(response, Exception):
        client.post = AsyncMock(side_effect=response)
    else:
        client.post = AsyncMock(return_value=response)
    with patch(
        "services.video_providers.wan2_1.httpx.AsyncClient",
        return_value=client,
    ):
        yield client


class _StubSiteConfig:
    """Minimal site_config double — supports .get() with a mapping."""

    def __init__(self, mapping: dict | None = None) -> None:
        self._mapping = mapping or {}

    def get(self, key: str, default=None):
        return self._mapping.get(key, default)


# ---------------------------------------------------------------------------
# Metadata / Protocol conformance / License sanity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWan21ProviderMetadata:
    def test_name_locked_to_1_3b(self):
        # The name MUST be wan2.1-1.3b, not wan2.1 or wan2.1-14b. Test
        # locks the entry_point key down so a future "consolidate to
        # wan2.1" PR doesn't accidentally absorb the 14B path's heavier
        # VRAM footprint (see GH#124 out-of-scope notes).
        assert Wan21Provider.name == "wan2.1-1.3b"

    def test_kind_is_generate(self):
        # Wan 2.1 is true text-to-video — kind="generate", not "compose".
        assert Wan21Provider.kind == "generate"

    def test_no_wan_14b_provider_alias(self):
        """Sanity check: there is no ``wan2.1-14b`` sibling in this
        module. The 14B variant is a separate follow-up ticket once
        1.3B is producing — adding a sibling here without the VRAM
        validation would create a regression footgun. Explicit guard
        so a future contributor copy-pasting this file gets a clear
        test failure."""
        from services import video_providers
        assert not hasattr(video_providers, "wan2_1_14b"), (
            "wan2.1-14b is intentionally not shipped in this PR — "
            "see GH#124 follow-up ticket"
        )


@pytest.mark.unit
class TestContract:
    def test_conforms_to_video_provider_protocol(self):
        provider = Wan21Provider()
        assert isinstance(provider, VideoProvider)

    def test_fetch_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(Wan21Provider.fetch)


# ---------------------------------------------------------------------------
# Config resolution helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServerUrlResolution:
    def test_per_call_config_wins(self):
        sc = _StubSiteConfig({
            "wan_server_url": "http://from-flat:9840",
            "plugin.video_provider.wan2.1-1.3b.server_url":
                "http://from-nested:9840",
        })
        assert _resolve_server_url(
            {"server_url": "http://override:9999"}, sc,
        ) == "http://override:9999"

    def test_flat_site_config_key_used(self):
        sc = _StubSiteConfig({"wan_server_url": "http://flat:9840"})
        assert _resolve_server_url({}, sc) == "http://flat:9840"

    def test_nested_plugin_namespace_used(self):
        sc = _StubSiteConfig({
            "plugin.video_provider.wan2.1-1.3b.server_url":
                "http://nested:9840",
        })
        assert _resolve_server_url({}, sc) == "http://nested:9840"

    def test_default_when_nothing_configured(self):
        assert _resolve_server_url({}, None) == (
            "http://host.docker.internal:9840"
        )

    def test_default_when_site_config_get_raises(self):
        class Boom:
            def get(self, *a, **kw):
                raise RuntimeError("db down")
        assert _resolve_server_url({}, Boom()) == (
            "http://host.docker.internal:9840"
        )


@pytest.mark.unit
class TestNegativeResolution:
    def test_per_call_config_wins(self):
        sc = _StubSiteConfig({"video_negative_prompt": "from-site"})
        assert _resolve_negative(
            {"negative_prompt": "from-call"}, sc,
        ) == "from-call"

    def test_falls_back_to_site_config(self):
        sc = _StubSiteConfig({"video_negative_prompt": "no people"})
        assert _resolve_negative({}, sc) == "no people"

    def test_empty_when_neither_set(self):
        assert _resolve_negative({}, None) == ""


# ---------------------------------------------------------------------------
# Wan21Provider.fetch — happy path + failure matrix
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestWan21ProviderFetch:
    async def test_empty_prompt_returns_empty(self):
        assert await Wan21Provider().fetch("", {}) == []

    async def test_whitespace_prompt_returns_empty(self):
        assert await Wan21Provider().fetch("   ", {}) == []

    async def test_server_success_returns_file_url(self, tmp_path):
        output_path = str(tmp_path / "out.mp4")
        with _mock_httpx_post(_video_response(content=b"\x00\x00MP4_BYTES")):
            results = await Wan21Provider().fetch(
                "a robot pouring coffee",
                {"output_path": output_path},
            )

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, VideoResult)
        assert r.file_url == f"file://{output_path}"
        assert r.file_path == output_path
        assert r.source == "wan2.1-1.3b"
        assert r.prompt == "a robot pouring coffee"
        assert r.codec == "h264"
        assert r.format == "mp4"
        assert r.duration_s == 5  # Wan 2.1 1.3B default
        assert r.width == 832  # Wan 2.1 1.3B native
        assert r.height == 480
        assert r.fps == 16
        assert r.metadata["local_path"] == output_path
        assert r.metadata["model"] == "wan2.1-1.3b"
        assert r.metadata["model_repo"] == "Wan-AI/Wan2.1-T2V-1.3B"
        assert r.metadata["license"] == "apache-2.0"
        assert r.metadata["file_size_bytes"] > 0
        # Bytes were actually persisted
        assert (tmp_path / "out.mp4").read_bytes() == b"\x00\x00MP4_BYTES"

    async def test_server_unreachable_returns_empty(self, tmp_path):
        """Connection refused → log error, return empty list. Operator
        sees a clear, actionable message; no silent crash. This is the
        documented failure mode per GH#124 — DO NOT actually stand up
        the Wan inference server in CI."""
        with _mock_httpx_post(RuntimeError("connection refused")):
            results = await Wan21Provider().fetch(
                "x", {"output_path": str(tmp_path / "x.mp4")},
            )
        assert results == []
        # No file written
        assert not (tmp_path / "x.mp4").exists()

    async def test_server_500_returns_empty(self, tmp_path):
        with _mock_httpx_post(_error_response(500)):
            results = await Wan21Provider().fetch(
                "x", {"output_path": str(tmp_path / "x.mp4")},
            )
        assert results == []

    async def test_server_html_response_returns_empty(self, tmp_path):
        """200 with text/html is an inference-server error page."""
        with _mock_httpx_post(_html_response()):
            results = await Wan21Provider().fetch(
                "x", {"output_path": str(tmp_path / "x.mp4")},
            )
        assert results == []

    async def test_tempfile_used_when_output_path_not_set(self):
        with _mock_httpx_post(_video_response(content=b"\x00\x00MP4_TMP")):
            results = await Wan21Provider().fetch("x", {})

        assert len(results) == 1
        assert results[0].file_url.startswith("file://")
        assert "local_path" in results[0].metadata

    async def test_steps_guidance_duration_dimensions_forwarded(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _video_response(content=b"\x00\x00MP4")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.video_providers.wan2_1.httpx.AsyncClient",
            return_value=client,
        ):
            await Wan21Provider().fetch(
                "a scene",
                {
                    "output_path": str(tmp_path / "o.mp4"),
                    "num_inference_steps": 80,
                    "guidance_scale": 7.5,
                    "duration_s": 8,
                    "width": 1024,
                    "height": 576,
                    "fps": 24,
                },
            )

        assert captured["json"]["steps"] == 80
        assert captured["json"]["guidance_scale"] == 7.5
        assert captured["json"]["duration_s"] == 8
        assert captured["json"]["width"] == 1024
        assert captured["json"]["height"] == 576
        assert captured["json"]["fps"] == 24
        # Server URL respects the default when no override is provided
        assert captured["url"].startswith("http://host.docker.internal:9840")
        # Wan-specific field — explicit model selector
        assert captured["json"]["model"] == "wan2.1-1.3b"

    async def test_default_steps_guidance(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["json"] = json
            return _video_response(content=b"\x00\x00MP4")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.video_providers.wan2_1.httpx.AsyncClient",
            return_value=client,
        ):
            await Wan21Provider().fetch(
                "a scene", {"output_path": str(tmp_path / "o.mp4")},
            )

        # Wan 2.1 is full-precision diffusion, not a distilled fast
        # model — defaults are higher than SDXL Lightning / FLUX.1-schnell.
        assert captured["json"]["steps"] == 50
        assert captured["json"]["guidance_scale"] == 5.0
        assert captured["json"]["duration_s"] == 5
        assert captured["json"]["width"] == 832
        assert captured["json"]["height"] == 480
        assert captured["json"]["fps"] == 16

    async def test_negative_prompt_from_config_wins(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["json"] = json
            return _video_response(content=b"\x00\x00MP4")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.video_providers.wan2_1.httpx.AsyncClient",
            return_value=client,
        ):
            await Wan21Provider().fetch(
                "a scene",
                {
                    "output_path": str(tmp_path / "o.mp4"),
                    "negative_prompt": "blurry, low quality",
                },
            )

        assert captured["json"]["negative_prompt"] == "blurry, low quality"

    async def test_server_url_override_from_config(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["url"] = url
            return _video_response(content=b"\x00\x00MP4")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.video_providers.wan2_1.httpx.AsyncClient",
            return_value=client,
        ):
            await Wan21Provider().fetch(
                "x",
                {
                    "output_path": str(tmp_path / "o.mp4"),
                    "server_url": "http://wan-staging:9840",
                },
            )

        assert captured["url"] == "http://wan-staging:9840/generate"

    async def test_upload_to_cloudinary_triggers_upload(self, tmp_path):
        with _mock_httpx_post(_video_response(content=b"\x00\x00MP4")), patch(
            "services.video_providers.wan2_1._upload_to_cloudinary",
            new=AsyncMock(return_value="https://cdn.cloudinary/x.mp4"),
        ) as up:
            results = await Wan21Provider().fetch(
                "x",
                {
                    "output_path": str(tmp_path / "o.mp4"),
                    "upload_to": "cloudinary",
                },
            )

        assert results[0].file_url == "https://cdn.cloudinary/x.mp4"
        up.assert_awaited_once()

    async def test_upload_to_r2_triggers_upload(self, tmp_path):
        with _mock_httpx_post(_video_response(content=b"\x00\x00MP4")), patch(
            "services.video_providers.wan2_1._upload_to_r2",
            new=AsyncMock(return_value="https://cdn.r2/x.mp4"),
        ) as up:
            results = await Wan21Provider().fetch(
                "x",
                {
                    "output_path": str(tmp_path / "o.mp4"),
                    "upload_to": "r2",
                },
            )

        assert results[0].file_url == "https://cdn.r2/x.mp4"
        up.assert_awaited_once()

    async def test_cloudinary_upload_failure_falls_back_to_file_url(
        self, tmp_path,
    ):
        output_path = str(tmp_path / "o.mp4")
        with _mock_httpx_post(_video_response(content=b"\x00\x00MP4")), patch(
            "services.video_providers.wan2_1._upload_to_cloudinary",
            new=AsyncMock(side_effect=RuntimeError("auth failed")),
        ):
            results = await Wan21Provider().fetch(
                "x",
                {"output_path": output_path, "upload_to": "cloudinary"},
            )

        assert len(results) == 1
        assert results[0].file_url == f"file://{output_path}"

    async def test_sidecar_json_response_materializes_file(self, tmp_path):
        sidecar_src = tmp_path / "wan_abc.mp4"
        sidecar_src.write_bytes(b"\x00\x00WAN_SIDECAR")
        output_path = str(tmp_path / "caller-out.mp4")

        resp = _json_sidecar_response(str(sidecar_src))
        with _mock_httpx_post(resp):
            results = await Wan21Provider().fetch(
                "a scene", {"output_path": output_path},
            )

        assert len(results) == 1
        assert results[0].file_url == f"file://{output_path}"
        assert (tmp_path / "caller-out.mp4").read_bytes() == (
            b"\x00\x00WAN_SIDECAR"
        )

    async def test_sidecar_json_accepts_file_path_alias(self, tmp_path):
        """Operators may write a sidecar that emits ``file_path``
        instead of ``video_path`` — accept both for compatibility with
        the FLUX/SDXL sidecar shape."""
        sidecar_src = tmp_path / "wan_alias.mp4"
        sidecar_src.write_bytes(b"\x00\x00ALIAS")
        output_path = str(tmp_path / "alias-out.mp4")

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json = MagicMock(
            return_value={"file_path": str(sidecar_src), "width": 832},
        )
        resp.text = ""

        with _mock_httpx_post(resp):
            results = await Wan21Provider().fetch(
                "a scene", {"output_path": output_path},
            )

        assert len(results) == 1
        assert (tmp_path / "alias-out.mp4").read_bytes() == b"\x00\x00ALIAS"

    async def test_sidecar_json_missing_path_returns_empty(self, tmp_path):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json = MagicMock(return_value={"error": "oom"})
        resp.text = '{"error": "oom"}'

        with _mock_httpx_post(resp):
            results = await Wan21Provider().fetch(
                "x", {"output_path": str(tmp_path / "x.mp4")},
            )
        assert results == []

    async def test_sidecar_json_source_file_missing_returns_empty(
        self, tmp_path,
    ):
        resp = _json_sidecar_response("/nonexistent/path/video.mp4")
        with _mock_httpx_post(resp):
            results = await Wan21Provider().fetch(
                "x", {"output_path": str(tmp_path / "x.mp4")},
            )
        assert results == []


# ---------------------------------------------------------------------------
# Plugin discovery — entry-point + core-sample registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPluginDiscovery:
    def test_wan21_discovered_via_get_video_providers(self):
        """Acceptance criterion #1 from GH#124: the VideoProvider
        Protocol file lands AND ``get_video_providers()`` exposes a
        Wan21Provider instance.

        The registry merges entry_points + core samples; in the
        in-tree test layout the core-samples loader is the one that
        picks up the provider until the package is installed via
        ``pip install``. Either path satisfies the contract.
        """
        clear_registry_cache()
        try:
            from plugins.registry import get_core_samples
            ep_providers = get_video_providers()
            sample_providers = get_core_samples().get("video_providers", [])
            all_providers = list(ep_providers) + list(sample_providers)
            assert any(
                p.__class__.__name__ == "Wan21Provider"
                for p in all_providers
            ), (
                "Wan21Provider must be discoverable via either the "
                "poindexter.video_providers entry-point group or the "
                "core-samples loader. Found: "
                f"{[p.__class__.__name__ for p in all_providers]}"
            )
        finally:
            clear_registry_cache()
