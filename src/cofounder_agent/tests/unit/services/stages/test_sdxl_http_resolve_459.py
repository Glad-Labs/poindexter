"""Regression tests for Glad-Labs/poindexter#459.

The SDXL server and the worker both run as ``appuser`` (uid 1001) in
their respective containers, with ephemeral in-container ``$HOME`` of
``/home/appuser``. The docker-compose volume mount that was supposed to
bridge them lands on ``/root/.poindexter/`` in both containers — but
the SDXL server writes to ``~/.poindexter/generated-images/`` which
resolves to ``/home/appuser/.poindexter/generated-images/`` and is
*not* on the shared mount. Result: the SDXL server returned a JSON
``image_path`` the worker could not see, ``os.path.exists`` returned
False, and every featured-image render silently fell back to Pexels.

Fix: the worker no longer trusts the in-container path. Instead it
calls ``GET <sdxl_url>/images/<filename>`` (which the SDXL server
already exposes) and materialises the bytes to a worker-local
tempfile. No filesystem coupling between containers; the SDXL server
is now a self-contained HTTP service.

These tests pin the new contract: both stages must (a) extract the
filename from the JSON response, (b) GET the bytes via the SDXL
server, and (c) materialise them on the worker's local disk before
returning the path.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.stages.replace_inline_images import (
    _resolve_sdxl_response,
)
from services.stages.source_featured_image import (
    _resolve_sdxl_featured_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_resp(payload: dict) -> MagicMock:
    """Fake an httpx.Response carrying SDXL's JSON generate-output."""
    resp = MagicMock(spec=httpx.Response)
    resp.headers = {"content-type": "application/json"}
    resp.json = MagicMock(return_value=payload)
    return resp


def _bytes_resp(content: bytes) -> MagicMock:
    """Fake an httpx.Response carrying raw image bytes."""
    resp = MagicMock(spec=httpx.Response)
    resp.headers = {"content-type": "image/png"}
    resp.content = content
    return resp


def _get_client_returning(status_code: int, content: bytes = b"") -> AsyncMock:
    """AsyncClient context-manager whose ``.get`` returns one response."""
    get_resp = MagicMock(spec=httpx.Response)
    get_resp.status_code = status_code
    get_resp.content = content

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=get_resp)
    return client


# ---------------------------------------------------------------------------
# Inline stage — replace_inline_images._resolve_sdxl_response
# ---------------------------------------------------------------------------


class TestInlineResolverFetchesViaHttp:
    """Closes #459 for the inline-image path."""

    @pytest.mark.asyncio
    async def test_json_response_triggers_http_fetch(self, tmp_path):
        """JSON response → GET /images/<filename> → bytes on local disk.

        The SDXL container's filesystem is not shared with the worker,
        so the worker must download via HTTP rather than reading the
        returned ``image_path``. Pins that contract.
        """
        sdxl_resp = _json_resp({
            "image_path": "/home/appuser/.poindexter/generated-images/sdxl_abc.png",
            "filename": "sdxl_abc.png",
        })
        client = _get_client_returning(200, content=b"\x89PNG\r\n\x1a\n--bytes--")

        with patch(
            "services.stages.replace_inline_images.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.stages.replace_inline_images._generated_images_dir",
            return_value=str(tmp_path),
        ):
            result = await _resolve_sdxl_response(
                sdxl_resp, sdxl_url="http://sdxl.internal:9836",
            )

        # The result must be a real, worker-visible local path with the
        # downloaded bytes — not the SDXL container's in-container path.
        assert os.path.exists(result)
        assert result.startswith(str(tmp_path))
        with open(result, "rb") as f:
            assert f.read() == b"\x89PNG\r\n\x1a\n--bytes--"

        # The fetch must hit /images/<filename>, not /generate or anything
        # else the server happens to expose.
        client.get.assert_awaited_once()
        get_url = client.get.await_args.args[0]
        assert get_url == "http://sdxl.internal:9836/images/sdxl_abc.png"

    @pytest.mark.asyncio
    async def test_filename_derived_from_image_path_when_omitted(self, tmp_path):
        """Older SDXL builds returned only ``image_path`` — basename still works.

        Forward-compat: a server that doesn't surface ``filename`` as a
        separate field shouldn't break the worker. Using basename of
        ``image_path`` is enough.
        """
        sdxl_resp = _json_resp({
            "image_path": "/whatever/dir/sdxl_legacy_99.png",
        })
        client = _get_client_returning(200, content=b"bytes")

        with patch(
            "services.stages.replace_inline_images.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.stages.replace_inline_images._generated_images_dir",
            return_value=str(tmp_path),
        ):
            await _resolve_sdxl_response(
                sdxl_resp, sdxl_url="http://sdxl:9836",
            )

        get_url = client.get.await_args.args[0]
        assert get_url.endswith("/images/sdxl_legacy_99.png")

    @pytest.mark.asyncio
    async def test_get_failure_raises_runtimeerror(self):
        """A non-200 from /images is a hard failure for the inline path.

        ``_try_sdxl`` catches it and falls back to Pexels — but the
        resolver itself must surface the error so the caller can log it
        properly. Silent None would mask the regression.
        """
        sdxl_resp = _json_resp({"filename": "sdxl_missing.png"})
        client = _get_client_returning(404)

        with patch(
            "services.stages.replace_inline_images.httpx.AsyncClient",
            return_value=client,
        ):
            with pytest.raises(RuntimeError, match="404"):
                await _resolve_sdxl_response(
                    sdxl_resp, sdxl_url="http://sdxl:9836",
                )

    @pytest.mark.asyncio
    async def test_missing_filename_raises_runtimeerror(self):
        """JSON response with no filename and no image_path → RuntimeError.

        The resolver has no way to know what to GET, so refuse to make
        up a name. Same surface as other broken-server cases.
        """
        sdxl_resp = _json_resp({})
        with pytest.raises(RuntimeError, match="filename"):
            await _resolve_sdxl_response(
                sdxl_resp, sdxl_url="http://sdxl:9836",
            )

    @pytest.mark.asyncio
    async def test_filename_basename_stripped_to_block_path_traversal(self, tmp_path):
        """``filename`` from the server is treated as basename only.

        Even though the SDXL server is internal, the response is still
        external input from the worker's POV. Stripping to basename
        prevents a malicious / buggy server from steering ``GET`` to
        an unrelated path like ``../../etc/passwd``.
        """
        sdxl_resp = _json_resp({"filename": "../../etc/passwd"})
        client = _get_client_returning(200, content=b"x")

        with patch(
            "services.stages.replace_inline_images.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.stages.replace_inline_images._generated_images_dir",
            return_value=str(tmp_path),
        ):
            await _resolve_sdxl_response(
                sdxl_resp, sdxl_url="http://sdxl:9836",
            )

        get_url = client.get.await_args.args[0]
        assert get_url == "http://sdxl:9836/images/passwd"

    @pytest.mark.asyncio
    async def test_image_content_type_still_writes_bytes_locally(self, tmp_path):
        """Legacy bytes-content path must keep working post-fix.

        Some SDXL configurations stream PNG bytes directly. The
        resolver should write them to the worker-local generated-images
        dir without ever touching the network.
        """
        resp = _bytes_resp(b"\x89PNG-direct-bytes")

        with patch(
            "services.stages.replace_inline_images._generated_images_dir",
            return_value=str(tmp_path),
        ):
            result = await _resolve_sdxl_response(
                resp, sdxl_url="http://unused:9836",
            )

        assert os.path.exists(result)
        with open(result, "rb") as f:
            assert f.read() == b"\x89PNG-direct-bytes"


# ---------------------------------------------------------------------------
# Featured-image stage — source_featured_image._resolve_sdxl_featured_response
# ---------------------------------------------------------------------------


class TestFeaturedResolverFetchesViaHttp:
    """Same contract as the inline resolver, with degrade-to-None semantics.

    The featured-image stage prefers a graceful fallback (Pexels) over a
    crash, so the resolver returns ``None`` instead of raising — but the
    HTTP-fetch behavior itself must match.
    """

    @pytest.mark.asyncio
    async def test_json_response_triggers_http_fetch(self, tmp_path):
        sdxl_resp = _json_resp({
            "image_path": "/home/appuser/.poindexter/generated-images/sdxl_feat.png",
            "filename": "sdxl_feat.png",
            "generation_time_ms": 1234,
        })
        client = _get_client_returning(200, content=b"feature-bytes")

        with patch(
            "services.stages.source_featured_image.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.stages.source_featured_image._featured_generated_images_dir",
            return_value=str(tmp_path),
        ):
            result = await _resolve_sdxl_featured_response(
                sdxl_resp, sdxl_url="http://sdxl.internal:9836",
            )

        assert result is not None
        assert os.path.exists(result)
        assert result.startswith(str(tmp_path))
        with open(result, "rb") as f:
            assert f.read() == b"feature-bytes"

        get_url = client.get.await_args.args[0]
        assert get_url == "http://sdxl.internal:9836/images/sdxl_feat.png"

    @pytest.mark.asyncio
    async def test_get_failure_returns_none(self):
        """Featured path degrades to None on fetch failure → Pexels fallback.

        Differs from the inline resolver (which raises) because the
        featured-image stage already has a built-in Pexels fallback at
        the stage level — surfacing the error as None lets that path
        kick in cleanly without an exception traceback in every log.
        """
        sdxl_resp = _json_resp({"filename": "sdxl_feat.png"})
        client = _get_client_returning(500)

        with patch(
            "services.stages.source_featured_image.httpx.AsyncClient",
            return_value=client,
        ):
            result = await _resolve_sdxl_featured_response(
                sdxl_resp, sdxl_url="http://sdxl:9836",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_filename_returns_none(self):
        """JSON response with neither filename nor image_path → None."""
        sdxl_resp = _json_resp({})
        result = await _resolve_sdxl_featured_response(
            sdxl_resp, sdxl_url="http://sdxl:9836",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_filename_basename_stripped_to_block_path_traversal(self, tmp_path):
        """Featured-side filename hygiene — same guarantee as inline."""
        sdxl_resp = _json_resp({"filename": "/etc/passwd"})
        client = _get_client_returning(200, content=b"x")

        with patch(
            "services.stages.source_featured_image.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.stages.source_featured_image._featured_generated_images_dir",
            return_value=str(tmp_path),
        ):
            await _resolve_sdxl_featured_response(
                sdxl_resp, sdxl_url="http://sdxl:9836",
            )

        get_url = client.get.await_args.args[0]
        assert get_url == "http://sdxl:9836/images/passwd"

    @pytest.mark.asyncio
    async def test_image_content_type_still_writes_bytes_locally(self, tmp_path):
        """Legacy bytes-content path returns the local tempfile."""
        resp = _bytes_resp(b"feature-bytes-direct")

        with patch(
            "services.stages.source_featured_image._featured_generated_images_dir",
            return_value=str(tmp_path),
        ):
            result = await _resolve_sdxl_featured_response(
                resp, sdxl_url="http://unused:9836",
            )

        assert result is not None
        assert os.path.exists(result)
        with open(result, "rb") as f:
            assert f.read() == b"feature-bytes-direct"

    @pytest.mark.asyncio
    async def test_unknown_content_type_returns_none(self):
        """An unexpected content-type degrades to None (caller → Pexels)."""
        resp = MagicMock(spec=httpx.Response)
        resp.headers = {"content-type": "text/html"}
        result = await _resolve_sdxl_featured_response(
            resp, sdxl_url="http://sdxl:9836",
        )
        assert result is None
