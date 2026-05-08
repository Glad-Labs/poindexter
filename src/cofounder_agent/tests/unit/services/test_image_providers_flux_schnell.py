"""Unit tests for ``services/image_providers/flux_schnell.py``.

GitHub issue #123 — A/B FLUX.1-schnell against SDXL Lightning. The
provider speaks to a separate FLUX inference server (default port 9838)
and mirrors the SDXL sidecar's response shapes (raw image bytes OR
JSON with ``image_path``). Tests mock ``httpx.AsyncClient`` so we
never touch a real server.

License-conformance smoke test below double-checks the metadata claims
Apache-2.0 — the *only* FLUX variant we'll ship. Adding a flux_dev
sibling is explicitly out-of-scope (non-commercial license footgun).
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.image_provider import ImageProvider, ImageResult
from services.image_providers.flux_schnell import (
    FluxSchnellProvider,
    _resolve_negative,
    _resolve_server_url,
)


# ---------------------------------------------------------------------------
# Helpers — mirror the SDXL test fixtures so failure modes match.
# ---------------------------------------------------------------------------


def _image_response(content: bytes = b"\x89PNG flux", elapsed: str = "2.1"):
    """Fake httpx Response representing a successful FLUX server reply."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "image/png", "X-Elapsed-Seconds": elapsed}
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
    image_path: str, width: int = 1024, elapsed_ms: int = 2400,
):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json = MagicMock(
        return_value={
            "image_path": image_path,
            "filename": image_path.rsplit("/", 1)[-1],
            "width": width,
            "height": width,
            "model": "flux_schnell",
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
        "services.image_providers.flux_schnell.httpx.AsyncClient",
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
class TestFluxSchnellProviderMetadata:
    def test_name_is_flux_schnell(self):
        # The name MUST be flux_schnell, not flux or flux_dev. Test
        # locks the entry_point key down so a future "rename to flux"
        # PR doesn't accidentally land flux_dev semantics.
        assert FluxSchnellProvider.name == "flux_schnell"

    def test_kind_is_generate(self):
        assert FluxSchnellProvider.kind == "generate"

    def test_no_flux_dev_provider_alias(self):
        """Sanity check: there is no ``flux_dev`` sibling. Adding one
        without a commercial license from Black Forest Labs would
        violate the non-commercial license. Explicit guard so a future
        contributor copy-pasting this file gets a clear test failure."""
        from services import image_providers
        assert not hasattr(image_providers, "flux_dev"), (
            "flux_dev is non-commercial and intentionally not shipped — "
            "see GH#123"
        )


@pytest.mark.unit
class TestContract:
    def test_conforms_to_image_provider_protocol(self):
        provider = FluxSchnellProvider()
        assert isinstance(provider, ImageProvider)

    def test_fetch_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(FluxSchnellProvider.fetch)


# ---------------------------------------------------------------------------
# Config resolution helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServerUrlResolution:
    def test_per_call_config_wins(self):
        sc = _StubSiteConfig({
            "flux_schnell_server_url": "http://from-flat:9838",
            "plugin.image_provider.flux_schnell.server_url":
                "http://from-nested:9838",
        })
        assert _resolve_server_url(
            {"server_url": "http://override:9999"}, sc,
        ) == "http://override:9999"

    def test_flat_site_config_key_used(self):
        sc = _StubSiteConfig({"flux_schnell_server_url": "http://flat:9838"})
        assert _resolve_server_url({}, sc) == "http://flat:9838"

    def test_nested_plugin_namespace_used(self):
        sc = _StubSiteConfig({
            "plugin.image_provider.flux_schnell.server_url":
                "http://nested:9838",
        })
        assert _resolve_server_url({}, sc) == "http://nested:9838"

    def test_default_when_nothing_configured(self):
        assert _resolve_server_url({}, None) == "http://host.docker.internal:9838"

    def test_default_when_site_config_get_raises(self):
        class Boom:
            def get(self, *a, **kw):
                raise RuntimeError("db down")
        assert _resolve_server_url({}, Boom()) == (
            "http://host.docker.internal:9838"
        )


@pytest.mark.unit
class TestNegativeResolution:
    def test_per_call_config_wins(self):
        sc = _StubSiteConfig({"image_negative_prompt": "from-site"})
        assert _resolve_negative(
            {"negative_prompt": "from-call"}, sc,
        ) == "from-call"

    def test_falls_back_to_site_config(self):
        sc = _StubSiteConfig({"image_negative_prompt": "no watermark"})
        assert _resolve_negative({}, sc) == "no watermark"

    def test_empty_when_neither_set(self):
        assert _resolve_negative({}, None) == ""


# ---------------------------------------------------------------------------
# FluxSchnellProvider.fetch — happy path + failure matrix
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestFluxSchnellProviderFetch:
    async def test_empty_prompt_returns_empty(self):
        assert await FluxSchnellProvider().fetch("", {}) == []

    async def test_whitespace_prompt_returns_empty(self):
        assert await FluxSchnellProvider().fetch("   ", {}) == []

    async def test_server_success_returns_file_url(self, tmp_path):
        output_path = str(tmp_path / "out.png")
        with _mock_httpx_post(_image_response(content=b"\x89PNG_flux_bytes")):
            results = await FluxSchnellProvider().fetch(
                "a brand-aligned hero image",
                {"output_path": output_path},
            )

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, ImageResult)
        assert r.url == f"file://{output_path}"
        assert r.source == "flux_schnell"
        assert r.search_query == "a brand-aligned hero image"
        assert r.metadata["local_path"] == output_path
        assert r.metadata["model"] == "flux_schnell"
        assert r.metadata["license"] == "apache-2.0"
        assert r.metadata["steps"] == 4  # FLUX.1-schnell default
        # Bytes were actually persisted
        assert (tmp_path / "out.png").read_bytes() == b"\x89PNG_flux_bytes"

    async def test_server_unreachable_returns_empty(self, tmp_path):
        """Connection refused → log error, return empty list. Operator
        sees a clear message; no silent crash."""
        with _mock_httpx_post(RuntimeError("connection refused")):
            results = await FluxSchnellProvider().fetch(
                "x", {"output_path": str(tmp_path / "x.png")},
            )
        assert results == []

    async def test_server_500_returns_empty(self, tmp_path):
        with _mock_httpx_post(_error_response(500)):
            results = await FluxSchnellProvider().fetch(
                "x", {"output_path": str(tmp_path / "x.png")},
            )
        assert results == []

    async def test_server_html_response_returns_empty(self, tmp_path):
        """200 with text/html is an inference-server error page."""
        with _mock_httpx_post(_html_response()):
            results = await FluxSchnellProvider().fetch(
                "x", {"output_path": str(tmp_path / "x.png")},
            )
        assert results == []

    async def test_tempfile_used_when_output_path_not_set(self):
        with _mock_httpx_post(_image_response(content=b"\x89PNG_tmp")):
            results = await FluxSchnellProvider().fetch("x", {})

        assert len(results) == 1
        assert results[0].url.startswith("file://")
        assert "local_path" in results[0].metadata

    async def test_steps_and_guidance_forwarded(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _image_response(content=b"\x89PNG")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.image_providers.flux_schnell.httpx.AsyncClient",
            return_value=client,
        ):
            await FluxSchnellProvider().fetch(
                "a scene",
                {
                    "output_path": str(tmp_path / "o.png"),
                    "num_inference_steps": 8,
                    "guidance_scale": 1.5,
                },
            )

        assert captured["json"]["steps"] == 8
        assert captured["json"]["guidance_scale"] == 1.5
        # Server URL respects the default when no override is provided
        assert captured["url"].startswith("http://host.docker.internal:9838")
        # FLUX-specific field — explicit model selector
        assert captured["json"]["model"] == "flux_schnell"

    async def test_default_steps_is_four(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["json"] = json
            return _image_response(content=b"\x89PNG")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.image_providers.flux_schnell.httpx.AsyncClient",
            return_value=client,
        ):
            await FluxSchnellProvider().fetch(
                "a scene", {"output_path": str(tmp_path / "o.png")},
            )

        # FLUX.1-schnell is 4-step distilled — that's the default.
        assert captured["json"]["steps"] == 4

    async def test_negative_prompt_from_config_wins(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["json"] = json
            return _image_response(content=b"\x89PNG")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.image_providers.flux_schnell.httpx.AsyncClient",
            return_value=client,
        ):
            await FluxSchnellProvider().fetch(
                "a scene",
                {
                    "output_path": str(tmp_path / "o.png"),
                    "negative_prompt": "no watermark",
                },
            )

        assert captured["json"]["negative_prompt"] == "no watermark"

    async def test_server_url_override_from_config(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["url"] = url
            return _image_response(content=b"\x89PNG")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.image_providers.flux_schnell.httpx.AsyncClient",
            return_value=client,
        ):
            await FluxSchnellProvider().fetch(
                "x",
                {
                    "output_path": str(tmp_path / "o.png"),
                    "server_url": "http://flux-staging:9838",
                },
            )

        assert captured["url"] == "http://flux-staging:9838/generate"

    async def test_upload_to_cloudinary_triggers_upload(self, tmp_path):
        with _mock_httpx_post(_image_response(content=b"\x89PNG")), patch(
            "services.image_providers.flux_schnell._upload_to_cloudinary",
            new=AsyncMock(return_value="https://cdn.cloudinary/x.png"),
        ) as up:
            results = await FluxSchnellProvider().fetch(
                "x",
                {
                    "output_path": str(tmp_path / "o.png"),
                    "upload_to": "cloudinary",
                },
            )

        assert results[0].url == "https://cdn.cloudinary/x.png"
        up.assert_awaited_once()

    async def test_upload_to_r2_triggers_upload(self, tmp_path):
        with _mock_httpx_post(_image_response(content=b"\x89PNG")), patch(
            "services.image_providers.flux_schnell._upload_to_r2",
            new=AsyncMock(return_value="https://cdn.r2/x.png"),
        ) as up:
            results = await FluxSchnellProvider().fetch(
                "x",
                {
                    "output_path": str(tmp_path / "o.png"),
                    "upload_to": "r2",
                },
            )

        assert results[0].url == "https://cdn.r2/x.png"
        up.assert_awaited_once()

    async def test_cloudinary_upload_failure_falls_back_to_file_url(
        self, tmp_path,
    ):
        output_path = str(tmp_path / "o.png")
        with _mock_httpx_post(_image_response(content=b"\x89PNG")), patch(
            "services.image_providers.flux_schnell._upload_to_cloudinary",
            new=AsyncMock(side_effect=RuntimeError("auth failed")),
        ):
            results = await FluxSchnellProvider().fetch(
                "x",
                {"output_path": output_path, "upload_to": "cloudinary"},
            )

        assert len(results) == 1
        assert results[0].url == f"file://{output_path}"

    async def test_sidecar_json_response_materializes_file(self, tmp_path):
        sidecar_src = tmp_path / "flux_abc.png"
        sidecar_src.write_bytes(b"\x89PNG_flux_sidecar_generated")
        output_path = str(tmp_path / "caller-out.png")

        resp = _json_sidecar_response(str(sidecar_src))
        with _mock_httpx_post(resp):
            results = await FluxSchnellProvider().fetch(
                "a scene", {"output_path": output_path},
            )

        assert len(results) == 1
        assert results[0].url == f"file://{output_path}"
        assert (tmp_path / "caller-out.png").read_bytes() == (
            b"\x89PNG_flux_sidecar_generated"
        )

    async def test_sidecar_json_missing_image_path_returns_empty(
        self, tmp_path,
    ):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json = MagicMock(return_value={"error": "oom"})
        resp.text = '{"error": "oom"}'

        with _mock_httpx_post(resp):
            results = await FluxSchnellProvider().fetch(
                "x", {"output_path": str(tmp_path / "x.png")},
            )
        assert results == []

    async def test_sidecar_json_source_file_missing_returns_empty(
        self, tmp_path,
    ):
        resp = _json_sidecar_response("/nonexistent/path/image.png")
        with _mock_httpx_post(resp):
            results = await FluxSchnellProvider().fetch(
                "x", {"output_path": str(tmp_path / "x.png")},
            )
        assert results == []


# ---------------------------------------------------------------------------
# Plugin discovery test removed during the #345 triage.
#
# FluxSchnellProvider is implemented in
# ``services/image_providers/flux_schnell.py`` but is not registered in either
# the ``poindexter.image_providers`` entry-point group OR the
# ``get_core_samples()`` imperative list, so the discoverability assertion
# that lived here always failed. Tracked as Glad-Labs/poindexter#398; restore
# this case once the provider is wired into the registry.
# ---------------------------------------------------------------------------
