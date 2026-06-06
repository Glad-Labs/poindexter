"""Unit tests for the takedown/orphan helpers in ``static_export_service``.

Covers the two things most likely to regress:

* ``_list_exported_post_slugs`` parses ``static/posts/<slug>.json`` keys into
  slugs and excludes ``index.json``.
* ``_retire_slug`` deletes the R2 JSON **before** revalidating — the ordering
  that makes the page actually 404 (revalidating first would re-cache the
  still-present 200). See #1146.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import static_export_service as ses
from services.site_config import SiteConfig


@pytest.mark.unit
class TestRetireHelpers:

    @pytest.mark.asyncio
    async def test_list_exported_post_slugs_parses_and_excludes_index(self):
        fake_r2 = MagicMock()
        fake_r2.list_keys = AsyncMock(
            return_value=[
                "static/posts/alpha.json",
                "static/posts/beta-123.json",
                "static/posts/index.json",  # must be excluded
            ]
        )
        with patch(
            "services.r2_upload_service.R2UploadService", return_value=fake_r2
        ):
            slugs = await ses._list_exported_post_slugs(site_config=SiteConfig())

        assert sorted(slugs) == ["alpha", "beta-123"]
        fake_r2.list_keys.assert_awaited_once_with("static/posts/")

    @pytest.mark.asyncio
    async def test_retire_slug_deletes_before_revalidating(self):
        order: list[str] = []

        async def _delete(key, *, site_config):  # noqa: ANN001, ARG001
            order.append(f"delete:{key}")
            return True

        async def _revalidate(slug, *, site_config):  # noqa: ANN001, ARG001
            order.append(f"revalidate:{slug}")
            return True

        with patch.object(ses, "_delete_json", _delete), patch(
            "services.revalidation_service.trigger_isr_revalidate", _revalidate
        ):
            await ses._retire_slug("ghost", site_config=SiteConfig())

        assert order == ["delete:posts/ghost.json", "revalidate:ghost"]

    @pytest.mark.asyncio
    async def test_retire_slug_swallows_revalidate_failure(self):
        """A cache-bust failure must not raise — the R2 delete already
        happened and the orphan sweep should keep going."""
        async def _boom(slug, *, site_config):  # noqa: ANN001, ARG001
            raise RuntimeError("edge challenged")

        with patch.object(ses, "_delete_json", AsyncMock(return_value=True)), patch(
            "services.revalidation_service.trigger_isr_revalidate", _boom
        ):
            # Should not raise.
            await ses._retire_slug("ghost", site_config=SiteConfig())

    @pytest.mark.asyncio
    async def test_sweep_retires_only_unpublished(self):
        with patch.object(
            ses,
            "_list_exported_post_slugs",
            AsyncMock(return_value=["live", "ghost-1", "ghost-2"]),
        ), patch.object(ses, "_retire_slug", AsyncMock()) as retire:
            retired = await ses._sweep_orphan_post_jsons(
                {"live"}, site_config=SiteConfig()
            )

        assert sorted(retired) == ["ghost-1", "ghost-2"]
        assert retire.await_count == 2
