"""Tests for the registry-driven publishing dispatch path (poindexter#112).

These complement TestDistributeToAdapters in ``test_social_poster.py``;
they're broken out into their own file because the dispatch path is the
new surface — the social_poster file's tests cover the broader social
posting flow + prompt building + notification.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

# Trigger handler registration (idempotent — registry refuses dupes).
from services.integrations.handlers import (  # noqa: F401
    publishing_bluesky,
    publishing_mastodon,
)
from services.publishing_adapters_db import PublishingAdapterRow
from services.social_poster import SocialPost


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
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.integrations.handlers.publishing_mastodon.post_to_mastodon",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_db_rows_drive_dispatch_not_legacy_set(
        self, mock_load, mock_masto, mock_bsky, posts,
    ):
        """If the DB has only bluesky enabled, mastodon never fires —
        even when the legacy ``enabled`` set still says
        ``{'bluesky', 'mastodon'}``."""
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky")]  # mastodon row absent
        mock_bsky.return_value = {"success": True, "post_id": "b", "error": None}

        result = await _distribute_to_adapters(posts, {"bluesky", "mastodon"})

        assert "bluesky" in result
        assert "mastodon" not in result
        mock_masto.assert_not_awaited()


class TestSiteConfigKwargRegression:
    """REGRESSION (poindexter#112): every dispatched call must include
    ``site_config=`` so the bluesky/mastodon adapters' DI gate doesn't
    short-circuit. Pin the fix that resolved the 2026-05-09 17:00 UTC
    distribution-dark bug."""

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_bluesky_handler_receives_site_config(
        self, mock_load, mock_bsky, posts,
    ):
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky")]
        mock_bsky.return_value = {"success": True, "post_id": "b", "error": None}

        await _distribute_to_adapters(posts, set())

        kwargs = mock_bsky.await_args.kwargs
        assert "site_config" in kwargs and kwargs["site_config"] is not None

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

        await _distribute_to_adapters(posts, set())

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
            result = await _distribute_to_adapters(posts, set())
        assert result == {}
        assert any(
            "no enabled adapters" in rec.getMessage()
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
            await _distribute_to_adapters(posts, {"bluesky"})
        assert any(
            "bluesky" in rec.getMessage() and "skipping" in rec.getMessage()
            for rec in caplog.records
        )

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_counter_update_fires_after_each_call(
        self, mock_load, mock_bsky, posts,
    ):
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky")]
        mock_bsky.return_value = {"success": True, "post_id": "b", "error": None}

        pool = _FakePool()
        await _distribute_to_adapters(posts, set(), pool=pool)

        update_qs = [q for q, _ in pool.executes if "UPDATE publishing_adapters" in q]
        assert len(update_qs) == 1, "expected exactly one counter update per row"
        # Confirm the args carry success status + null error.
        _, args = pool.executes[0]
        assert args[1] == "success"
        assert args[2] is None  # last_error
        assert args[3] is True  # success flag for CASE expression

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_mastodon.post_to_mastodon",
           new_callable=AsyncMock)
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_one_handler_raising_does_not_stop_loop(
        self, mock_load, mock_bsky, mock_masto, posts,
    ):
        """Graceful-degradation regression — preserved for the dispatch
        path. Bluesky raises, mastodon still gets called."""
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky"), _row("mastodon")]
        mock_bsky.side_effect = RuntimeError("boom")
        mock_masto.return_value = {"success": True, "post_id": "m", "error": None}

        result = await _distribute_to_adapters(posts, set())
        assert result["bluesky"]["success"] is False
        assert "boom" in result["bluesky"]["error"]
        assert result["mastodon"]["success"] is True
