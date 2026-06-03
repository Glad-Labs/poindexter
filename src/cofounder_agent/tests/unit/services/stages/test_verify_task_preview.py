"""verify_task mints preview_token/preview_url early (Glad-Labs/poindexter#563).

The qa.vision rendered-preview rail runs BEFORE finalize_task, so the preview
token must be minted at the top of the pipeline and surfaced in
StageResult.context_updates (the only thing make_stage_node merges back into
the graph_def state). Without this echo the rail would have no URL to
screenshot — the exact cold state #563 flagged. finalize_task then reuses the
token rather than minting a second one.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.stages.verify_task import VerifyTaskStage


class _Cfg:
    def get(self, key, default=None):
        if key == "preview_base_url":
            return "http://localhost:8002"
        return default


class _Db:
    def __init__(self, found=True):
        self._found = found

    async def get_task(self, _task_id: str) -> dict[str, Any] | None:
        return {"task_id": _task_id} if self._found else None


@pytest.mark.unit
class TestVerifyTaskPreviewMint:
    async def test_surfaces_preview_token_and_url_in_updates(self):
        ctx = {"task_id": "t1", "database_service": _Db(), "site_config": _Cfg()}
        result = await VerifyTaskStage().execute(ctx, {})

        assert result.ok is True
        # The two channels the qa.vision rail reads must ride context_updates
        # (a bare context mutation would be dropped by make_stage_node).
        token = result.context_updates.get("preview_token")
        url = result.context_updates.get("preview_url")
        assert token and len(token) >= 16
        assert url == f"http://localhost:8002/preview/{token}"

    async def test_reuses_existing_token(self):
        """A pre-seeded preview_token (retry/replay) is kept stable."""
        ctx = {
            "task_id": "t1", "database_service": _Db(),
            "site_config": _Cfg(), "preview_token": "fixedtoken123456",
        }
        result = await VerifyTaskStage().execute(ctx, {})
        assert result.context_updates["preview_token"] == "fixedtoken123456"
        assert result.context_updates["preview_url"].endswith("/preview/fixedtoken123456")

    async def test_uses_configured_base_url(self):
        class _RemoteCfg:
            def get(self, key, default=None):
                if key == "preview_base_url":
                    return "https://preview.example.com/"
                return default

        ctx = {"task_id": "t1", "database_service": _Db(), "site_config": _RemoteCfg()}
        result = await VerifyTaskStage().execute(ctx, {})
        url = result.context_updates["preview_url"]
        # Trailing slash on the base is normalized (no double slash).
        assert url.startswith("https://preview.example.com/preview/")
        assert "//preview" not in url.replace("https://", "")

    async def test_no_token_when_task_missing(self):
        """The mint only happens on the verified-success path; a missing task
        returns the legacy not-found result with no preview channels."""
        ctx = {"task_id": "t1", "database_service": _Db(found=False), "site_config": _Cfg()}
        result = await VerifyTaskStage().execute(ctx, {})
        assert result.ok is False
        assert "preview_token" not in (result.context_updates or {})
