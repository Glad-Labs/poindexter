"""Unit tests for PostizClient payload construction (offline, httpx mocked)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.integrations.postiz_client import PostizClient, _extract_post_id


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
