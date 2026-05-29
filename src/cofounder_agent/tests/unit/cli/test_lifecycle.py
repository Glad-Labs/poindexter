"""Unit tests for ``poindexter.cli._lifecycle`` — the CLI's
``container_for_cli`` async context manager.

PR 2 of the SiteConfig constructor-DI migration (design doc:
``docs/architecture/2026-05-28-site-config-di-migration.md``). The
manager is the seam every CLI subcommand will eventually go through to
get an ``AppContainer``; today the body is a no-op around
``services.bootstrap.build_container``.

Covers two scenarios:

1. Happy path — manager yields an ``AppContainer`` and the caller can
   read its wiring fields.
2. Failure propagation — ``build_container`` raising propagates out of
   the ``async with`` (per ``feedback_no_silent_defaults``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from poindexter.cli._lifecycle import container_for_cli
from services.container import AppContainer


class TestContainerForCli:
    async def test_yields_app_container_with_loaded_site_config(self):
        """Happy path: pool.fetch returns rows; manager yields a real
        AppContainer carrying a loaded SiteConfig + the same pool."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                {"key": "site_name", "value": "From CLI Pool"},
                {"key": "preferred_ollama_model", "value": "qwen2.5:14b"},
            ]
        )

        async with container_for_cli(pool) as container:
            assert isinstance(container, AppContainer)
            assert container.pool is pool
            assert container.site_config.is_loaded is True
            assert container.site_config.get("site_name") == "From CLI Pool"
            assert (
                container.site_config.get("preferred_ollama_model")
                == "qwen2.5:14b"
            )

    async def test_build_container_failure_propagates(self):
        """Fail-loud per feedback_no_silent_defaults: a build_container
        crash inside the manager surfaces to the caller's ``async with``
        instead of yielding a degraded/empty container."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            side_effect=RuntimeError("simulated connection reset")
        )

        with pytest.raises(RuntimeError) as excinfo:
            async with container_for_cli(pool):
                pytest.fail("manager body must not execute on build failure")

        # build_container re-raises with the SQL echoed in the message
        # — confirm the helper preserves that context through the
        # context manager surface.
        msg = str(excinfo.value)
        assert "is_secret = false" in msg
        assert "simulated connection reset" in msg

    async def test_rejects_none_pool(self):
        """``build_container`` rejects ``pool=None`` loudly; the
        manager passes that error straight through."""
        with pytest.raises(RuntimeError, match="pool=None"):
            async with container_for_cli(None):
                pytest.fail("manager body must not execute on None pool")
