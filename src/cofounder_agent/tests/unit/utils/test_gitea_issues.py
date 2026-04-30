"""Unit tests for ``utils/gitea_issues.py``.

httpx is mocked; site_config is patched for different credential
scenarios. Focus: opt-out when no password, dedup-by-prefix, error
swallowing so a failed Gitea call doesn't break the caller.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.gitea_issues import _prefix, create_gitea_issue


class TestPrefix:
    def test_prefix_before_colon(self):
        assert _prefix("links: 3 broken URLs in posts") == "links"

    def test_prefix_no_colon(self):
        assert _prefix("just a title without colon") == "just a title without colon"[:30]

    def test_prefix_strips_whitespace(self):
        assert _prefix("links :  broken URLs") == "links"

    def test_prefix_empty_string(self):
        assert _prefix("") == ""


def _patched_site_config(
    password: str = "secret",
    user: str = "gladlabs",
    url: str = "http://gitea.example",
    repo: str = "gladlabs/codebase",
):
    """Context-manager friendly site_config.get patch."""
    mapping = {
        "gitea_password": password,
        "gitea_user": user,
        "gitea_url": url,
        "gitea_repo": repo,
    }
    return patch(
        "utils.gitea_issues.site_config.get",
        side_effect=lambda k, d=None: mapping.get(k, d),
    )


def _fake_response(status_code: int = 201, json_data: Any = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    resp.text = text
    return resp


def _fake_client(
    search_json: list[dict] | None = None,
    post_response: MagicMock | None = None,
    post_raises: BaseException | None = None,
):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=_fake_response(200, json_data=search_json or []))
    if post_raises:
        client.post = AsyncMock(side_effect=post_raises)
    else:
        client.post = AsyncMock(
            return_value=post_response
            or _fake_response(201, json_data={"number": 42}),
        )
    return client


class TestCreateGiteaIssue:
    @pytest.mark.asyncio
    async def test_skipped_when_no_password(self):
        with _patched_site_config(password=""):
            result = await create_gitea_issue("title", "body")
        assert result is False

    @pytest.mark.asyncio
    async def test_creates_new_issue_when_no_dup(self):
        client = _fake_client(search_json=[])
        with _patched_site_config(), patch(
            "utils.gitea_issues.httpx.AsyncClient", return_value=client,
        ):
            result = await create_gitea_issue(
                "links: 3 broken URLs", "## broken\n- http://x",
            )
        assert result is True
        client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dedups_by_title_prefix(self):
        """An open issue with the same prefix should block a new filing."""
        existing = [{"number": 7, "title": "links: 1 broken URL from last run"}]
        client = _fake_client(search_json=existing)
        with _patched_site_config(), patch(
            "utils.gitea_issues.httpx.AsyncClient", return_value=client,
        ):
            result = await create_gitea_issue("links: 99 broken URLs", "body")
        assert result is False
        client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_different_prefix_does_not_dedup(self):
        """Open issue under a different prefix must not block the new one."""
        existing = [{"number": 7, "title": "seo: missing meta"}]
        client = _fake_client(search_json=existing)
        with _patched_site_config(), patch(
            "utils.gitea_issues.httpx.AsyncClient", return_value=client,
        ):
            result = await create_gitea_issue("links: 3 broken", "body")
        assert result is True
        client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_failure_returns_false_no_raise(self):
        """An httpx error from post() must not propagate to the caller."""
        client = _fake_client(post_raises=RuntimeError("network down"))
        with _patched_site_config(), patch(
            "utils.gitea_issues.httpx.AsyncClient", return_value=client,
        ):
            result = await create_gitea_issue("links: x", "body")
        assert result is False

    @pytest.mark.asyncio
    async def test_non_2xx_response_returns_false(self):
        client = _fake_client(post_response=_fake_response(422, text="bad payload"))
        with _patched_site_config(), patch(
            "utils.gitea_issues.httpx.AsyncClient", return_value=client,
        ):
            result = await create_gitea_issue("links: x", "body")
        assert result is False
