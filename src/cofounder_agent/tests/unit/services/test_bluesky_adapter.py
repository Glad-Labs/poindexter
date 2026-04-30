"""Unit tests for ``services.social_adapters.bluesky`` (GH-36).

Covers the direct AT Protocol adapter that replaced the dlvr.it RSS
bridge. Mocks the ``atproto`` SDK so the tests run on any dev box
(the package may not be installed in every environment).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.social_adapters import bluesky

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_fake_atproto(client_instance: MagicMock | None = None) -> None:
    """Shim a minimal ``atproto`` module into ``sys.modules``.

    Lets ``from atproto import Client`` succeed inside the adapter
    without the real package being installed. The returned Client
    class resolves to ``client_instance`` (or a fresh MagicMock).
    """
    fake_mod = types.ModuleType("atproto")
    fake_mod.Client = MagicMock(return_value=client_instance or MagicMock())
    sys.modules["atproto"] = fake_mod


def _uninstall_fake_atproto() -> None:
    sys.modules.pop("atproto", None)


@pytest.fixture(autouse=True)
def _cleanup_atproto():
    """Ensure each test starts + ends with no stale ``atproto`` shim."""
    _uninstall_fake_atproto()
    yield
    _uninstall_fake_atproto()


# ---------------------------------------------------------------------------
# Helper: _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_under_limit_unchanged(self):
        assert bluesky._truncate("hello", 100) == "hello"

    def test_at_limit_unchanged(self):
        s = "x" * 300
        assert bluesky._truncate(s, 300) == s

    def test_over_limit_truncated_with_ellipsis(self):
        s = "x" * 310
        result = bluesky._truncate(s, 300)
        assert len(result) == 300
        assert result.endswith("...")

    def test_default_limit_is_300(self):
        s = "x" * 500
        result = bluesky._truncate(s)
        assert len(result) == 300


# ---------------------------------------------------------------------------
# Credential short-circuit
# ---------------------------------------------------------------------------


class TestMissingCredentials:
    """When identifier or app password is empty, adapter skips cleanly."""

    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_missing_identifier_short_circuits(self, mock_get_secret):
        # get_secret returns "" for identifier, "something" for password
        mock_get_secret.side_effect = ["", "password123"]
        result = await bluesky.post_to_bluesky("hello", "https://example.com/p/1")
        assert result["success"] is False
        assert result["post_id"] is None
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_missing_password_short_circuits(self, mock_get_secret):
        mock_get_secret.side_effect = ["gladlabs.bsky.social", ""]
        result = await bluesky.post_to_bluesky("hello", "https://example.com/p/1")
        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_no_atproto_import_on_missing_creds(self, mock_get_secret):
        """Short-circuit must happen BEFORE we try to import atproto."""
        mock_get_secret.side_effect = ["", ""]
        # Don't install the shim — if we reach the import, this would
        # raise ImportError and surface as error="atproto not installed".
        result = await bluesky.post_to_bluesky("hello", "https://x.com")
        assert "not configured" in result["error"]


# ---------------------------------------------------------------------------
# Missing atproto package
# ---------------------------------------------------------------------------


class TestMissingAtprotoPackage:
    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_importerror_returns_clean_error(self, mock_get_secret):
        mock_get_secret.side_effect = ["handle", "pw"]
        # Force ImportError: with `sys.modules["atproto"] = None`, the
        # import machinery raises ImportError, and the adapter wraps that
        # into a "not installed" result.
        with patch.dict(sys.modules, {"atproto": None}):
            result = await bluesky.post_to_bluesky("hi", "https://x.com")
        assert result["success"] is False
        assert "not installed" in result["error"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_posts_successfully(self, mock_get_secret):
        mock_get_secret.side_effect = ["gladlabs.bsky.social", "app-pw"]

        fake_client = MagicMock()
        fake_client.login = MagicMock()
        fake_response = MagicMock()
        fake_response.uri = "at://did:plc:foo/app.bsky.feed.post/abc123"
        fake_client.send_post = MagicMock(return_value=fake_response)
        _install_fake_atproto(fake_client)

        result = await bluesky.post_to_bluesky(
            "Check out this post!", "https://gladlabs.io/posts/my-post"
        )

        assert result["success"] is True
        assert result["post_id"] == "at://did:plc:foo/app.bsky.feed.post/abc123"
        assert result["error"] is None
        fake_client.login.assert_called_once_with("gladlabs.bsky.social", "app-pw")
        fake_client.send_post.assert_called_once()
        # The post text should include the URL
        sent_text = fake_client.send_post.call_args.kwargs.get("text") or fake_client.send_post.call_args.args[0]
        assert "https://gladlabs.io/posts/my-post" in sent_text

    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_url_already_in_text_not_duplicated(self, mock_get_secret):
        mock_get_secret.side_effect = ["h", "p"]
        fake_client = MagicMock()
        resp = MagicMock()
        resp.uri = "at://x"
        fake_client.send_post = MagicMock(return_value=resp)
        _install_fake_atproto(fake_client)

        url = "https://gladlabs.io/posts/x"
        text = f"Already has the url: {url}"
        await bluesky.post_to_bluesky(text, url)

        sent_text = fake_client.send_post.call_args.kwargs.get("text") or fake_client.send_post.call_args.args[0]
        # URL appears exactly once
        assert sent_text.count(url) == 1

    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_long_text_truncated_to_300(self, mock_get_secret):
        mock_get_secret.side_effect = ["h", "p"]
        fake_client = MagicMock()
        resp = MagicMock()
        resp.uri = "at://x"
        fake_client.send_post = MagicMock(return_value=resp)
        _install_fake_atproto(fake_client)

        long_text = "word " * 100  # 500 chars
        await bluesky.post_to_bluesky(long_text, "https://x.com/1")

        sent_text = fake_client.send_post.call_args.kwargs.get("text") or fake_client.send_post.call_args.args[0]
        assert len(sent_text) <= 300
        assert sent_text.endswith("...")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_login_failure_returns_error_dict(self, mock_get_secret):
        mock_get_secret.side_effect = ["h", "p"]
        fake_client = MagicMock()
        fake_client.login.side_effect = Exception("invalid credentials")
        _install_fake_atproto(fake_client)

        result = await bluesky.post_to_bluesky("hi", "https://x.com/1")
        assert result["success"] is False
        assert result["post_id"] is None
        assert "invalid credentials" in result["error"]

    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_send_post_failure_returns_error_dict(self, mock_get_secret):
        mock_get_secret.side_effect = ["h", "p"]
        fake_client = MagicMock()
        fake_client.send_post.side_effect = Exception("rate limited")
        _install_fake_atproto(fake_client)

        result = await bluesky.post_to_bluesky("hi", "https://x.com/1")
        assert result["success"] is False
        assert "rate limited" in result["error"]

    @pytest.mark.asyncio
    @patch.object(bluesky.site_config, "get_secret", new_callable=AsyncMock)
    async def test_error_does_not_raise(self, mock_get_secret):
        """An adapter error MUST NOT propagate up to the caller."""
        mock_get_secret.side_effect = ["h", "p"]
        fake_client = MagicMock()
        fake_client.send_post.side_effect = RuntimeError("network gone")
        _install_fake_atproto(fake_client)

        # If this raised, pytest would fail here.
        result = await bluesky.post_to_bluesky("hi", "https://x.com/1")
        assert result["success"] is False
