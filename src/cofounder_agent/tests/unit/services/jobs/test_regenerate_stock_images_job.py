"""Unit tests for ``services/jobs/regenerate_stock_images.py``."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.regenerate_stock_images import RegenerateStockImagesJob, _build_sdxl_prompt


def _mock_sc() -> MagicMock:
    """SiteConfig mock for post-Phase-H job.run() kwarg."""
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": d
    sc.get_bool.side_effect = lambda k, d=False: d
    sc.get_int.side_effect = lambda k, d=0: d
    return sc


def _fake_asyncpg(pexels_rows: list[dict] | None = None):
    cloud = AsyncMock()
    cloud.fetch = AsyncMock(return_value=pexels_rows or [])
    cloud.execute = AsyncMock(return_value="UPDATE 1")
    cloud.close = AsyncMock(return_value=None)

    async def _connect(url: str) -> Any:
        return cloud

    fake = MagicMock()
    fake.connect = _connect
    return fake, cloud


def _pool(negative: str = "blurry, low-quality"):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=negative)
    return pool


@pytest.mark.unit
class TestRegenerateStockImagesJobMetadata:
    def test_name(self):
        assert RegenerateStockImagesJob.name == "regenerate_stock_images"

    def test_schedule(self):
        assert "6 hours" in RegenerateStockImagesJob.schedule


@pytest.mark.unit
@pytest.mark.asyncio
class TestRegenerateStockImagesJobRun:
    async def test_skips_when_no_database_url(self):
        job = RegenerateStockImagesJob()
        sc = _mock_sc()
        sc.get.side_effect = lambda k, d="": ""
        result = await job.run(MagicMock(), {}, site_config=sc)
        assert result.ok is True
        assert "no database_url" in result.detail

    async def test_no_pexels_posts_returns_zero(self):
        job = RegenerateStockImagesJob()
        fake_asyncpg, _ = _fake_asyncpg(pexels_rows=[])
        pool = _pool()
        sc = _mock_sc()
        sc.get.side_effect = lambda k, d="": "postgres://cloud" if k == "database_url" else d
        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}):
            result = await job.run(pool, {}, site_config=sc)
        assert result.ok is True
        assert result.changes_made == 0
        assert "no Pexels-backed posts" in result.detail

    async def test_regenerates_image_end_to_end(self, tmp_path):
        job = RegenerateStockImagesJob()
        rows = [{"id": "p1", "title": "Docker changed everything", "category": "tech"}]
        fake_asyncpg, cloud = _fake_asyncpg(pexels_rows=rows)
        pool = _pool()

        output_file = tmp_path / "out.png"
        output_file.write_bytes(b"fake image")

        svc = MagicMock()
        svc.generate_image = AsyncMock(return_value=True)

        fake_cloudinary_result = {"secure_url": "https://cloudinary/img.jpg"}
        fake_cloudinary = MagicMock()
        fake_cloudinary.config = MagicMock()
        fake_cloudinary.uploader = MagicMock()
        fake_cloudinary.uploader.upload = MagicMock(return_value=fake_cloudinary_result)

        sc = _mock_sc()
        sc.get.side_effect = lambda k, d="": "postgres://cloud" if k == "database_url" else ""
        with patch.dict("sys.modules", {
                 "asyncpg": fake_asyncpg,
                 "cloudinary": fake_cloudinary,
                 "cloudinary.uploader": fake_cloudinary.uploader,
             }), \
             patch("services.image_service.get_image_service", return_value=svc), \
             patch("tempfile.NamedTemporaryFile") as mock_tmp, \
             patch("os.path.exists", return_value=True), \
             patch("os.remove"), \
             patch("services.jobs.regenerate_stock_images._build_sdxl_prompt",
                   new=AsyncMock(return_value="a cinematic scene")):
            mock_tmp.return_value.__enter__ = MagicMock(
                return_value=MagicMock(name=str(output_file)),
            )
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            # Make the tmp file's ".name" attribute return the actual path.
            tmp_obj = MagicMock()
            tmp_obj.name = str(output_file)
            mock_tmp.return_value.__enter__.return_value = tmp_obj

            result = await job.run(pool, {}, site_config=sc)

        assert result.ok is True
        # We generated 1 image, uploaded it, updated the DB.
        assert result.changes_made == 1
        cloud.execute.assert_awaited_once()
        assert fake_cloudinary.uploader.upload.call_count == 1

    async def test_generation_failure_moves_to_next_post(self):
        job = RegenerateStockImagesJob()
        rows = [
            {"id": "p1", "title": "First", "category": "tech"},
            {"id": "p2", "title": "Second", "category": "tech"},
        ]
        fake_asyncpg, _ = _fake_asyncpg(pexels_rows=rows)
        pool = _pool()

        svc = MagicMock()
        # First call raises, second succeeds.
        svc.generate_image = AsyncMock(side_effect=[RuntimeError("GPU OOM"), True])

        # Fake services.image_service as a whole so its real torch import
        # doesn't run in the test env.
        fake_image_service = MagicMock()
        fake_image_service.get_image_service = MagicMock(return_value=svc)

        sc = _mock_sc()
        sc.get.side_effect = lambda k, d="": "postgres://cloud" if k == "database_url" else ""
        with patch.dict(
                "sys.modules",
                {"asyncpg": fake_asyncpg, "services.image_service": fake_image_service},
             ), \
             patch("services.jobs.regenerate_stock_images._build_sdxl_prompt",
                   new=AsyncMock(return_value="a prompt")):
            await job.run(pool, {}, site_config=sc)

        # Both posts attempted: first raised, second succeeded.
        assert svc.generate_image.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
class TestBuildSDXLPrompt:
    async def test_ollama_success_returns_generated(self):
        site_config = MagicMock()
        site_config.get = MagicMock(return_value="http://ollama")

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": '"a detailed photoreal landscape scene"'})

        client = MagicMock()
        client.post = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=ctx):
            result = await _build_sdxl_prompt("A post title", "llama3:latest", site_config)
        assert "photoreal" in result

    async def test_ollama_failure_returns_fallback(self):
        site_config = MagicMock()
        site_config.get = MagicMock(return_value="http://ollama")
        with patch("httpx.AsyncClient", side_effect=RuntimeError("boom")):
            result = await _build_sdxl_prompt("X", "llama3:latest", site_config)
        assert "photorealistic scene related to X" in result

    async def test_short_ollama_response_returns_fallback(self):
        site_config = MagicMock()
        site_config.get = MagicMock(return_value="http://ollama")

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": '"short"'})

        client = MagicMock()
        client.post = AsyncMock(return_value=resp)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=ctx):
            result = await _build_sdxl_prompt("My blog post", "llama3:latest", site_config)
        assert "photorealistic scene" in result  # fell back
