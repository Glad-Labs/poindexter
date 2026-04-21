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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helper: _truncate
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Credential short-circuit
# ---------------------------------------------------------------------------


class TestMissingCredentials:
    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_missing_instance_url_short_circuits(self, mock_get, mock_get_secret):
        mock_get.return_value = ""  # instance URL not set
        mock_get_secret.return_value = "token"
        result = await mastodon_adapter.post_to_mastodon("hi", "https://x.com/1")
        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_missing_access_token_short_circuits(self, mock_get, mock_get_secret):
        mock_get.return_value = "https://mastodon.social"
        mock_get_secret.return_value = ""
        result = await mastodon_adapter.post_to_mastodon("hi", "https://x.com/1")
        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_strips_trailing_slash_from_instance_url(self, mock_get, mock_get_secret):
        mock_get.return_value = "https://mastodon.social/"
        mock_get_secret.return_value = "token"
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(return_value={"id": 1, "url": "u"})

        await mastodon_adapter.post_to_mastodon("hi", "https://x.com/1")

        kwargs = mastodon_cls.call_args.kwargs
        assert kwargs["api_base_url"] == "https://mastodon.social"


# ---------------------------------------------------------------------------
# Missing Mastodon.py package
# ---------------------------------------------------------------------------


class TestMissingMastodonPackage:
    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_importerror_returns_clean_error(self, mock_get, mock_get_secret):
        mock_get.return_value = "https://mastodon.social"
        mock_get_secret.return_value = "token"
        # Simulate package unavailable.
        with patch.dict(sys.modules, {"mastodon": None}):
            result = await mastodon_adapter.post_to_mastodon("hi", "https://x.com/1")
        assert result["success"] is False
        assert "not installed" in result["error"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_posts_successfully(self, mock_get, mock_get_secret):
        mock_get.return_value = "https://mastodon.social"
        mock_get_secret.return_value = "my-token"

        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            return_value={"id": 12345, "url": "https://mastodon.social/@x/12345"}
        )

        result = await mastodon_adapter.post_to_mastodon(
            "Check out this post!", "https://gladlabs.io/posts/my-post"
        )

        assert result["success"] is True
        assert result["post_id"] == "12345"
        assert result["error"] is None
        mastodon_cls.assert_called_once()
        kwargs = mastodon_cls.call_args.kwargs
        assert kwargs["access_token"] == "my-token"
        assert kwargs["api_base_url"] == "https://mastodon.social"

    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_url_appended_if_not_in_text(self, mock_get, mock_get_secret):
        mock_get.return_value = "https://mastodon.social"
        mock_get_secret.return_value = "t"
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            return_value={"id": 1, "url": "u"}
        )

        await mastodon_adapter.post_to_mastodon("A cool post", "https://gladlabs.io/p/a")

        status_call = mastodon_cls.return_value.status_post.call_args
        posted_text = status_call.kwargs.get("status") or status_call.args[0]
        assert "https://gladlabs.io/p/a" in posted_text

    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_visibility_is_public(self, mock_get, mock_get_secret):
        mock_get.return_value = "https://mastodon.social"
        mock_get_secret.return_value = "t"
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            return_value={"id": 1, "url": "u"}
        )

        await mastodon_adapter.post_to_mastodon("hi", "https://x.com/1")

        status_call = mastodon_cls.return_value.status_post.call_args
        assert status_call.kwargs.get("visibility") == "public"

    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_long_text_truncated(self, mock_get, mock_get_secret):
        mock_get.return_value = "https://mastodon.social"
        mock_get_secret.return_value = "t"
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            return_value={"id": 1, "url": "u"}
        )

        long_text = "word " * 200  # 1000 chars
        await mastodon_adapter.post_to_mastodon(long_text, "https://x.com/1")

        status_call = mastodon_cls.return_value.status_post.call_args
        posted_text = status_call.kwargs.get("status") or status_call.args[0]
        assert len(posted_text) <= 500
        assert posted_text.endswith("...")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_post_failure_returns_error_dict(self, mock_get, mock_get_secret):
        mock_get.return_value = "https://mastodon.social"
        mock_get_secret.return_value = "t"
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            side_effect=Exception("429 rate limited")
        )

        result = await mastodon_adapter.post_to_mastodon("hi", "https://x.com/1")
        assert result["success"] is False
        assert result["post_id"] is None
        assert "rate limited" in result["error"]

    @pytest.mark.asyncio
    @patch.object(mastodon_adapter.site_config, "get_secret", new_callable=AsyncMock)
    @patch.object(mastodon_adapter.site_config, "get")
    async def test_error_does_not_raise(self, mock_get, mock_get_secret):
        mock_get.return_value = "https://mastodon.social"
        mock_get_secret.return_value = "t"
        mastodon_cls = _install_fake_mastodon()
        mastodon_cls.return_value.status_post = MagicMock(
            side_effect=RuntimeError("network gone")
        )

        # Must not raise.
        result = await mastodon_adapter.post_to_mastodon("hi", "https://x.com/1")
        assert result["success"] is False
