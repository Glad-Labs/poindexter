"""The /api/export/rebuild route triggers ISR revalidation after a rebuild.

Gap found 2026-06-02: export_full_rebuild re-uploads JSON to R2 but the
route never busted the Next.js tag cache (no TTL), so an operator rebuild
silently left the live site stale. The publish path always did both; the
rebuild path did only the export half.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routes import cms_routes


@pytest.mark.asyncio
async def test_rebuild_triggers_revalidation_on_success():
    db = MagicMock()
    db.cloud_pool = None
    db.pool = object()
    sc = MagicMock()
    with patch(
        "services.static_export_service.export_full_rebuild",
        AsyncMock(return_value={"success": True, "posts": 5}),
    ), patch(
        "services.revalidation_service.trigger_nextjs_revalidation",
        AsyncMock(return_value=True),
    ) as reval:
        resp = await cms_routes.rebuild_static_export(db_service=db, site_config_dep=sc)

    reval.assert_awaited_once()
    # canonical post tags must be invalidated so per-slug pages (tagged
    # 'posts') and the index both refresh.
    tags = reval.call_args.kwargs.get("tags") or []
    assert "posts" in tags
    body = json.loads(bytes(resp.body))
    assert body["revalidation_success"] is True


@pytest.mark.asyncio
async def test_rebuild_does_not_revalidate_on_failure():
    db = MagicMock()
    db.cloud_pool = None
    db.pool = object()
    sc = MagicMock()
    with patch(
        "services.static_export_service.export_full_rebuild",
        AsyncMock(return_value={"success": False, "error": "r2 down"}),
    ), patch(
        "services.revalidation_service.trigger_nextjs_revalidation",
        AsyncMock(return_value=True),
    ) as reval:
        resp = await cms_routes.rebuild_static_export(db_service=db, site_config_dep=sc)

    reval.assert_not_awaited()
    assert resp.status_code == 207
