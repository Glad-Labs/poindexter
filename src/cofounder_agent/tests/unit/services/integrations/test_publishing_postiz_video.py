"""publishing.postiz_video attaches the upload by id AND path (poindexter#1094).

Postiz rejects an id-only attachment: each `image` entry is a MediaDto with
`path` required. The handler used to post `upload_ids=[upload_id]`, so its
first real use would have failed at the upload (a route Postiz never served)
and, past that, at the post.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.services.integrations.handlers.publishing_postiz_video import postiz_video

pytestmark = pytest.mark.asyncio

_UPLOADED = {"id": "m-1", "path": "http://localhost:5003/uploads/2026/09/28/v.mp4"}


class _Cfg:
    def get(self, key: str, default: Any = None) -> Any:
        return {
            "postiz_api_url": "http://postiz:3000",
            "postiz_integration_id_tiktok": "uuid-tiktok",
        }.get(key, default)

    async def get_secret(self, key: str, default: str = "") -> str:
        return "k" if key == "postiz_api_key" else default


async def test_the_post_carries_the_uploaded_media_path():
    upload = AsyncMock(return_value=_UPLOADED)
    create = AsyncMock(return_value={"success": True, "post_id": "pz-1", "error": None})
    with patch("poindexter.services.integrations.postiz_client.PostizClient.upload_media_from_url", upload), \
         patch("poindexter.services.integrations.postiz_client.PostizClient.create_post", create):
        result = await postiz_video(
            {"media_url": "https://cdn.example.com/v.mp4", "title": "cap", "platform": "tiktok"},
            site_config=_Cfg(), row={},
        )

    assert result["success"] is True and result["post_id"] == "pz-1"
    upload.assert_awaited_once_with("https://cdn.example.com/v.mp4")
    assert create.await_args.kwargs["media"] == [_UPLOADED]
    assert "upload_ids" not in create.await_args.kwargs


async def test_an_upload_failure_is_a_failed_result_not_a_post():
    create = AsyncMock()
    with patch("poindexter.services.integrations.postiz_client.PostizClient.upload_media_from_url",
               AsyncMock(side_effect=ValueError("Postiz upload returned no id/path: {}"))), \
         patch("poindexter.services.integrations.postiz_client.PostizClient.create_post", create):
        result = await postiz_video(
            {"media_url": "https://cdn.example.com/v.mp4", "title": "cap", "platform": "tiktok"},
            site_config=_Cfg(), row={},
        )

    assert result["success"] is False and "no id/path" in result["error"]
    create.assert_not_awaited()
