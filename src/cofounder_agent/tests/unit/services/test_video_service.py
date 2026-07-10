"""Tests for video_service — the shared image-gen frame helper.

The legacy ``:9837`` slideshow lane (``generate_video_for_post`` /
``generate_short_video_for_post`` + the ``video-server.py`` host process) was
retired 2026-07. Only ``_consume_image_gen_response`` — materialising an
image-gen frame from either the raw-bytes or JSON response shape, used by the
shot-list renderer — remains here.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestConsumeImageGenResponse:
    """``_consume_image_gen_response`` — handles both raw-bytes and JSON image-gen responses.

    Production bug 2026-05-20: the image-gen server returns 200 with
    Content-Type: application/json + a JSON body {"filename": ...},
    but the video-slideshow path required Content-Type: image/* and
    treated every JSON response as a failure. Every image-gen frame was
    silently lost. Pin both branches so a future revert is caught.
    """

    @pytest.mark.asyncio
    async def test_image_bytes_branch_writes_directly(self, tmp_path):
        from services.video_service import _consume_image_gen_response

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "image/png"}
        resp.content = b"\x89PNG raw-bytes-here"

        out = str(tmp_path / "frame.png")
        got = await _consume_image_gen_response(
            resp, image_gen_url="http://image-gen-server:9836", output_path=out, frame_label="t",
        )
        assert got == out
        assert (tmp_path / "frame.png").read_bytes() == b"\x89PNG raw-bytes-here"

    @pytest.mark.asyncio
    async def test_json_branch_fetches_from_images_endpoint(self, tmp_path):
        """JSON shape with filename → GET <image-gen-server>/images/<filename> → write bytes."""
        from services.video_service import _consume_image_gen_response

        primary_resp = MagicMock()
        primary_resp.status_code = 200
        primary_resp.headers = {"content-type": "application/json"}
        primary_resp.json = MagicMock(return_value={
            "filename": "img_abc123.png",
            "image_path": "/home/appuser/.poindexter/generated-images/img_abc123.png",
            "model": "sdxl_lightning",
        })

        fetch_resp = MagicMock()
        fetch_resp.status_code = 200
        fetch_resp.content = b"fetched-image-bytes"

        fetch_client = AsyncMock()
        fetch_client.__aenter__ = AsyncMock(return_value=fetch_client)
        fetch_client.__aexit__ = AsyncMock(return_value=False)
        fetch_client.get = AsyncMock(return_value=fetch_resp)

        out = str(tmp_path / "frame.png")
        with patch(
            "services.video_service.httpx.AsyncClient",
            return_value=fetch_client,
        ):
            got = await _consume_image_gen_response(
                resp=primary_resp,
                image_gen_url="http://image-gen-server:9836",
                output_path=out,
                frame_label="t",
            )
        assert got == out
        assert (tmp_path / "frame.png").read_bytes() == b"fetched-image-bytes"
        # Confirm the fetch URL was the /images/<filename> endpoint
        fetch_client.get.assert_called_once_with(
            "http://image-gen-server:9836/images/img_abc123.png",
        )

    @pytest.mark.asyncio
    async def test_json_branch_falls_back_to_image_path_when_filename_missing(self, tmp_path):
        from services.video_service import _consume_image_gen_response

        primary_resp = MagicMock()
        primary_resp.status_code = 200
        primary_resp.headers = {"content-type": "application/json"}
        primary_resp.json = MagicMock(return_value={
            "image_path": "/home/appuser/.poindexter/generated-images/img_xyz.png",
        })
        fetch_resp = MagicMock()
        fetch_resp.status_code = 200
        fetch_resp.content = b"x"

        fetch_client = AsyncMock()
        fetch_client.__aenter__ = AsyncMock(return_value=fetch_client)
        fetch_client.__aexit__ = AsyncMock(return_value=False)
        fetch_client.get = AsyncMock(return_value=fetch_resp)

        with patch(
            "services.video_service.httpx.AsyncClient",
            return_value=fetch_client,
        ):
            got = await _consume_image_gen_response(
                resp=primary_resp, image_gen_url="http://image-gen-server:9836",
                output_path=str(tmp_path / "f.png"), frame_label="t",
            )
        assert got is not None
        # filename derived from os.path.basename(image_path)
        fetch_client.get.assert_called_once_with(
            "http://image-gen-server:9836/images/img_xyz.png",
        )

    @pytest.mark.asyncio
    async def test_json_branch_returns_none_when_filename_missing(self, tmp_path):
        from services.video_service import _consume_image_gen_response
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json = MagicMock(return_value={"model": "sdxl_lightning"})

        got = await _consume_image_gen_response(
            resp=resp, image_gen_url="http://image-gen-server:9836",
            output_path=str(tmp_path / "f.png"), frame_label="t",
        )
        assert got is None

    @pytest.mark.asyncio
    async def test_non_2xx_returns_none(self, tmp_path):
        from services.video_service import _consume_image_gen_response
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "internal error"
        got = await _consume_image_gen_response(
            resp=resp, image_gen_url="http://image-gen-server:9836",
            output_path=str(tmp_path / "f.png"), frame_label="t",
        )
        assert got is None
