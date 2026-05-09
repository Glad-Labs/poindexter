"""Unit tests for ``services.social_adapters.mastodon`` (GH-36).

Covers the direct Mastodon/Fediverse adapter that replaced the dlvr.it
RSS bridge. Mocks ``Mastodon.py`` so tests run on any dev box.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.social_adapters import mastodon as mastodon_adapter


def _site_config_mock(instance_url: str = "", access_token: str = "") -> MagicMock:
    """Build a SiteConfig stand-in for the kwarg-DI surface.

    Mastodon's adapter uses a mixed accessor pattern: ``site_config.get``
    (sync) for the instance URL plus ``site_config.get_secret`` (async)
    for the access token. Mirrors the post-#330 singleton-deletion
    contract that ``social_poster._distribute_to_adapters`` honors.
    """
    sc = MagicMock()
    sc.get = MagicMock(return_value=instance_url)
    sc.get_secret = AsyncMock(return_value=access_token)
    return sc


def _install_fake_mastodon(client_instance: MagicMock | None = None) -> MagicMock:
    """Shim a minimal ``mastodon`` module into ``sys.modules``.

    Returns the fake ``Mastodon`` class MagicMock so tests can assert
    on ``.return_value`` etc.
    """
    fake_mod = types.ModuleType("mastodon")
    instance = client_instance or MagicMock()
    mastodon_cls = MagicMock(return_value=instance)
    fake_mod.Mastodon = mastodon_cls
    sys.modules["mastodon"] = fake_mod
    return mastodon_cls


def _uninstall_fake_mastodon() -> None:
    sys.modules.pop("mastodon", None)


@pytest.fixture(autouse=True)
def _cleanup_mastodon():
    _uninstall_fake_mastodon()
    yield
    _uninstall_fake_mastodon()


class TestTruncate:
    def test_under_limit_unchanged(self):
        assert mastodon_adapter._truncate("hello", 100) == "hello"

    def test_over_limit_truncated_with_ellipsis(self):
        s = "x" * 600
        result = mastodon_adapter._truncate(s, 500)
        assert len(result) == 500
        assert result.endswith("...")

    def test_default_limit_is_500(self):
        s = "x" * 700
        result = mastodon_adapter._truncate(s)
        assert len(result) == 500


class TestMissingCredentials:
    @pytest.mark.asyncio
    async def test_missing_site_config_short_circuits(self):
        result = await mastodon_adapter.post_to_mastodon("hi", "https://x.com/1")
        assert result["success"] is False
        assert "not provided" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_instance_url_short_circuits(self):
        sc = _site_config_mock(instance_url="", access_token="token")
        result = await mastodon_adapter.post_to_mastodon(
            "hi", "https://x.com/1", site_config=sc,
        )
        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_access_token_short_circuits(self):
        sc = _site_config_mock(instance_url="https://mastodon.social", access_token="")
        result = await mastodon_adapter.post_to_mastodon(
            "hi", "https://x.com/1", site_config=sc,
        )
        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_instance_url(self):
        sc = _site_config_mock(
            instance_url="https://mastodon.social/", access_token="token",
        )
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            return_value={"id": 1, "url": "u"}
        )

        await mastodon_adapter.post_to_mastodon(
            "hi", "https://x.com/1", site_config=sc,
        )

        kwargs = mastodon_cls.call_args.kwargs
        assert kwargs["api_base_url"] == "https://mastodon.social"


class TestMissingMastodonPackage:
    @pytest.mark.asyncio
    async def test_importerror_returns_clean_error(self):
        sc = _site_config_mock(
            instance_url="https://mastodon.social", access_token="token",
        )
        with patch.dict(sys.modules, {"mastodon": None}):
            result = await mastodon_adapter.post_to_mastodon(
                "hi", "https://x.com/1", site_config=sc,
            )
        assert result["success"] is False
        assert "not installed" in result["error"]


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_posts_successfully(self):
        sc = _site_config_mock(
            instance_url="https://mastodon.social", access_token="my-token",
        )
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            return_value={"id": 12345, "url": "https://mastodon.social/@x/12345"}
        )

        result = await mastodon_adapter.post_to_mastodon(
            "Check out this post!",
            "https://gladlabs.io/posts/my-post",
            site_config=sc,
        )

        assert result["success"] is True
        assert result["post_id"] == "12345"
        assert result["error"] is None
        mastodon_cls.assert_called_once()
        kwargs = mastodon_cls.call_args.kwargs
        assert kwargs["access_token"] == "my-token"
        assert kwargs["api_base_url"] == "https://mastodon.social"

    @pytest.mark.asyncio
    async def test_url_appended_if_not_in_text(self):
        sc = _site_config_mock(
            instance_url="https://mastodon.social", access_token="t",
        )
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            return_value={"id": 1, "url": "u"}
        )

        await mastodon_adapter.post_to_mastodon(
            "A cool post", "https://gladlabs.io/p/a", site_config=sc,
        )

        status_call = mastodon_cls.return_value.status_post.call_args
        posted_text = status_call.kwargs.get("status") or status_call.args[0]
        assert "https://gladlabs.io/p/a" in posted_text

    @pytest.mark.asyncio
    async def test_visibility_is_public(self):
        sc = _site_config_mock(
            instance_url="https://mastodon.social", access_token="t",
        )
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            return_value={"id": 1, "url": "u"}
        )

        await mastodon_adapter.post_to_mastodon(
            "hi", "https://x.com/1", site_config=sc,
        )

        status_call = mastodon_cls.return_value.status_post.call_args
        assert status_call.kwargs.get("visibility") == "public"

    @pytest.mark.asyncio
    async def test_long_text_truncated(self):
        sc = _site_config_mock(
            instance_url="https://mastodon.social", access_token="t",
        )
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            return_value={"id": 1, "url": "u"}
        )

        long_text = "word " * 200  # 1000 chars
        await mastodon_adapter.post_to_mastodon(
            long_text, "https://x.com/1", site_config=sc,
        )

        status_call = mastodon_cls.return_value.status_post.call_args
        posted_text = status_call.kwargs.get("status") or status_call.args[0]
        assert len(posted_text) <= 500
        assert posted_text.endswith("...")
