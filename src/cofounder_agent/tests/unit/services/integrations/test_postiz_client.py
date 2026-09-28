"""Unit tests for PostizClient payload construction (offline, httpx mocked)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.integrations.postiz_client import PostizClient, _extract_post_id


def _mock_http(captured: dict):
    """Build a mock httpx.AsyncClient whose .post() captures the JSON body."""
    resp = MagicMock()
    resp.status_code = 200
    # Postiz returns a LIST of {postId, integration} from POST /public/v1/posts.
    resp.json.return_value = [{"postId": "pz-1", "integration": "uuid-x"}]
    resp.raise_for_status = MagicMock()

    async def _post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return resp

    http = AsyncMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    http.post = _post
    return http


@pytest.mark.asyncio
async def test_create_post_injects_required_x_settings():
    """X posts must carry who_can_reply_post — Postiz 400s without it."""
    captured: dict = {}
    client = PostizClient(base_url="http://postiz:3000", api_key="k")
    with patch("httpx.AsyncClient", return_value=_mock_http(captured)):
        result = await client.create_post(
            integration_id="uuid-x",
            content="hello",
            platform_type="x",
            platform_settings={},
            upload_ids=[],
        )

    assert result["success"] is True
    assert result["post_id"] == "pz-1"  # parsed from the list response
    settings = captured["json"]["posts"][0]["settings"]
    assert settings["__type"] == "x"
    assert settings["who_can_reply_post"] == "everyone"


def test_extract_post_id_handles_list_dict_and_empty():
    """Postiz returns a list of {postId,...}; tolerate dict + empty too."""
    assert _extract_post_id([{"postId": "p1", "integration": "i"}]) == "p1"
    assert _extract_post_id([{"id": "p2"}]) == "p2"  # field fallback
    assert _extract_post_id({"id": "p3"}) == "p3"    # dict shape
    assert _extract_post_id([]) is None
    assert _extract_post_id(None) is None


@pytest.mark.asyncio
async def test_caller_platform_settings_override_defaults():
    """Caller-supplied platform_settings win over the per-platform defaults."""
    captured: dict = {}
    client = PostizClient(base_url="http://postiz:3000", api_key="k")
    with patch("httpx.AsyncClient", return_value=_mock_http(captured)):
        await client.create_post(
            integration_id="uuid-x",
            content="hello",
            platform_type="x",
            platform_settings={"who_can_reply_post": "verified"},
            upload_ids=[],
        )

    settings = captured["json"]["posts"][0]["settings"]
    assert settings["who_can_reply_post"] == "verified"


@pytest.mark.asyncio
async def test_create_post_no_defaults_for_unknown_platform():
    """A platform with no required-setting defaults gets only __type + caller."""
    captured: dict = {}
    client = PostizClient(base_url="http://postiz:3000", api_key="k")
    with patch("httpx.AsyncClient", return_value=_mock_http(captured)):
        await client.create_post(
            integration_id="uuid-li",
            content="hello",
            platform_type="linkedin",
            platform_settings={},
            upload_ids=[],
        )

    settings = captured["json"]["posts"][0]["settings"]
    assert settings == {"__type": "linkedin"}


# --- media upload + attachment (poindexter#1094) ------------------------------
#
# The upload route was /public/v1/uploads/url, which Postiz never served (404 on
# the live v2.21.10 and v2.24.0). And an attachment is a MediaDto whose `path`
# is required alongside `id`, so even a working upload would have been posted as
# an id-only item and rejected with a 400. The body below is the one a live
# upload returned on 2026-09-28.

_UPLOAD_BODY = {
    "id": "4825113f-4101-4b4e-9b55-abaa00a7d6d9",
    "name": "bf5326f9b3a7c32bab25c50829f19192.webp",
    "originalName": None,
    "path": "http://localhost:5003/uploads/2026/09/28/bf5326f9b3a7c32bab25c50829f19192.webp",
    "thumbnail": None,
    "alt": None,
    "status": "READY",
}


def _mock_json_http(captured: dict, body):
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()

    async def _post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers)
        return resp

    http = AsyncMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    http.post = _post
    return http


@pytest.mark.asyncio
async def test_upload_goes_to_the_route_postiz_serves_and_keeps_the_path():
    captured: dict = {}
    client = PostizClient(base_url="http://postiz:3000/", api_key="k")
    with patch("httpx.AsyncClient", return_value=_mock_json_http(captured, _UPLOAD_BODY)):
        uploaded = await client.upload_media_from_url("https://cdn.example.com/v.mp4")

    assert captured["url"] == "http://postiz:3000/public/v1/upload-from-url"
    assert captured["json"] == {"url": "https://cdn.example.com/v.mp4"}
    assert uploaded == {"id": _UPLOAD_BODY["id"], "path": _UPLOAD_BODY["path"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["id", "path"])
async def test_upload_without_id_or_path_is_an_error(missing):
    body = {k: v for k, v in _UPLOAD_BODY.items() if k != missing}
    client = PostizClient(base_url="http://postiz:3000", api_key="k")
    with patch("httpx.AsyncClient", return_value=_mock_json_http({}, body)):
        with pytest.raises(ValueError, match="no id/path"):
            await client.upload_media_from_url("https://cdn.example.com/v.mp4")


@pytest.mark.asyncio
async def test_upload_from_url_still_returns_the_id_via_the_right_route():
    captured: dict = {}
    client = PostizClient(base_url="http://postiz:3000", api_key="k")
    with patch("httpx.AsyncClient", return_value=_mock_json_http(captured, _UPLOAD_BODY)):
        assert await client.upload_from_url("https://cdn.example.com/v.mp4") == _UPLOAD_BODY["id"]
    assert captured["url"].endswith("/public/v1/upload-from-url")


@pytest.mark.asyncio
async def test_media_is_attached_with_id_and_path():
    """Postiz's MediaDto requires `path` next to `id`; an id-only item 400s."""
    captured: dict = {}
    client = PostizClient(base_url="http://postiz:3000", api_key="k")
    with patch("httpx.AsyncClient", return_value=_mock_http(captured)):
        result = await client.create_post(
            integration_id="uuid-t",
            content="caption",
            platform_type="tiktok",
            platform_settings={},
            media=[{"id": "m-1", "path": "http://localhost:5003/uploads/a.mp4"}],
        )

    assert result["success"] is True
    assert captured["json"]["posts"][0]["value"][0]["image"] == [
        {"id": "m-1", "path": "http://localhost:5003/uploads/a.mp4"}
    ]


@pytest.mark.asyncio
async def test_text_posts_and_upload_ids_callers_are_unchanged():
    """social_drafts posts text with upload_ids=[]; that and a bare call both
    still send an empty image list."""
    for kwargs in ({"upload_ids": []}, {}):
        captured: dict = {}
        client = PostizClient(base_url="http://postiz:3000", api_key="k")
        with patch("httpx.AsyncClient", return_value=_mock_http(captured)):
            await client.create_post(
                integration_id="uuid-x", content="hi", platform_type="x",
                platform_settings={}, **kwargs,
            )
        assert captured["json"]["posts"][0]["value"][0]["image"] == []
