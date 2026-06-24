"""Tests for the registry-driven publishing dispatch path (poindexter#112).

These complement TestDistributeToAdapters in ``test_social_poster.py``;
they're broken out into their own file because the dispatch path is the
new surface — the social_poster file's tests cover the broader social
posting flow + prompt building + notification.

Mastodon is the canonical example adapter here. (Bluesky/atproto was
retired 2026-06-17 to unblock the cryptography>=48.0.1 security bump for
GHSA-537c-gmf6-5ccf.) A second, un-rowed platform name ("reddit") stands
in where a test needs a "listed but not wired" platform; the two-handler
graceful-degradation case patches ``registry.dispatch`` directly so it
exercises the loop without needing a second real adapter.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

# Trigger handler registration (idempotent — registry refuses dupes).
from services.integrations.handlers import publishing_mastodon  # noqa: F401
from services.publishing_adapters_db import PublishingAdapterRow
from services.site_config import SiteConfig
from services.social_poster import SocialPost

# SiteConfig DI (#272 Phase-2e): ``_distribute_to_adapters`` takes a required
# ``site_config=`` kwarg. Tests thread this shared env-backed instance.
_TEST_SC = SiteConfig()


def _row(platform: str, *, handler_name: str | None = None) -> PublishingAdapterRow:
    return PublishingAdapterRow(
        id=uuid4(),
        name=f"{platform}_main",
        platform=platform,
        handler_name=handler_name or platform,
        credentials_ref=f"{platform}_",
        enabled=True,
        config={},
        metadata={},
    )


class _FakeConn:
    def __init__(self, parent): self.parent = parent
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def execute(self, q, *args):
        self.parent.executes.append((q, args))
        return "UPDATE 1"


class _FakePool:
    def __init__(self): self.executes: list[tuple[str, tuple]] = []
    def acquire(self): return _FakeConn(self)


@pytest.fixture
def posts() -> list[SocialPost]:
    return [SocialPost(platform="twitter", text="hello world",
                       post_url="https://gladlabs.io/posts/x")]


class TestRegistryDrivenDispatch:
    """The dispatcher walks DB rows, not the legacy ``enabled`` set."""

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_mastodon.post_to_mastodon",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_db_rows_drive_dispatch_not_legacy_set(
        self, mock_load, mock_masto, posts,
    ):
        """If the DB has only mastodon enabled, a legacy-only platform
        never fires — even when the legacy ``enabled`` set still lists it."""
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("mastodon")]  # 'reddit' row absent
        mock_masto.return_value = {"success": True, "post_id": "m", "error": None}

        result = await _distribute_to_adapters(posts, {"mastodon", "reddit"}, site_config=_TEST_SC)

        assert "mastodon" in result
        assert "reddit" not in result
        mock_masto.assert_awaited_once()


class TestSiteConfigKwargRegression:
    """REGRESSION (poindexter#112): every dispatched call must include
    ``site_config=`` so the mastodon adapter's DI gate doesn't
    short-circuit. Pin the fix that resolved the 2026-05-09 17:00 UTC
    distribution-dark bug."""

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_mastodon.post_to_mastodon",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_mastodon_handler_receives_site_config(
        self, mock_load, mock_masto, posts,
    ):
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("mastodon")]
        mock_masto.return_value = {"success": True, "post_id": "m", "error": None}

        await _distribute_to_adapters(posts, set(), site_config=_TEST_SC)

        kwargs = mock_masto.await_args.kwargs
        assert "site_config" in kwargs and kwargs["site_config"] is not None


class TestDispatchEdgeCases:
    @pytest.mark.asyncio
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_zero_enabled_rows_logs_info_and_returns_empty(
        self, mock_load, posts, caplog,
    ):
        import logging

        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = []
        with caplog.at_level(logging.INFO, logger="services.social_poster"):
            result = await _distribute_to_adapters(posts, set(), site_config=_TEST_SC)
        assert result == {}
        assert any(
            "no enabled social adapters" in rec.getMessage()
            for rec in caplog.records
        )

    @pytest.mark.asyncio
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_legacy_platform_without_db_row_logs_warning(
        self, mock_load, posts, caplog,
    ):
        """Legacy ``social_distribution_platforms`` lists an unknown
        platform = WARN + skip. No silent no-op (matches Matt's "no
        silent defaults" feedback)."""
        import logging

        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = []  # nothing wired
        with caplog.at_level(logging.WARNING, logger="services.social_poster"):
            await _distribute_to_adapters(posts, {"reddit"}, site_config=_TEST_SC)
        assert any(
            "reddit" in rec.getMessage() and "skipping" in rec.getMessage()
            for rec in caplog.records
        )

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_mastodon.post_to_mastodon",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_counter_update_fires_after_each_call(
        self, mock_load, mock_masto, posts,
    ):
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("mastodon")]
        mock_masto.return_value = {"success": True, "post_id": "m", "error": None}

        pool = _FakePool()
        await _distribute_to_adapters(posts, set(), pool=pool, site_config=_TEST_SC)

        update_qs = [q for q, _ in pool.executes if "UPDATE publishing_adapters" in q]
        assert len(update_qs) == 1, "expected exactly one counter update per row"
        # Confirm the args carry success status + null error.
        _, args = pool.executes[0]
        assert args[1] == "success"
        assert args[2] is None  # last_error
        assert args[3] is True  # success flag for CASE expression

    @pytest.mark.asyncio
    @patch("services.social_poster.registry.dispatch", new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_one_handler_raising_does_not_stop_loop(
        self, mock_load, mock_dispatch, posts,
    ):
        """Graceful-degradation regression — preserved for the dispatch
        path. One platform's handler raises; the other still gets called
        and the loop returns a well-formed result for both. Patching
        ``registry.dispatch`` exercises the loop without a second real
        adapter."""
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("mastodon"), _row("reddit")]

        async def _dispatch(surface, handler_name, payload, **kwargs):
            if handler_name == "mastodon":
                raise RuntimeError("boom")
            return {"success": True, "post_id": "r", "error": None}

        mock_dispatch.side_effect = _dispatch

        result = await _distribute_to_adapters(posts, set(), site_config=_TEST_SC)
        assert result["mastodon"]["success"] is False
        assert "boom" in result["mastodon"]["error"]
        assert result["reddit"]["success"] is True
