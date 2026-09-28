"""Unit tests for ``poindexter.cli._lifecycle`` — the CLI's
``container_for_cli`` async context manager.

PR 2 of the SiteConfig constructor-DI migration (design doc:
``docs/architecture/2026-05-28-site-config-di-migration.md``). The
manager is the seam every CLI subcommand will eventually go through to
get an ``AppContainer``, built by ``services.bootstrap.build_container``.

Covers three scenarios:

1. Happy path — manager yields an ``AppContainer`` and the caller can
   read its wiring fields.
2. Failure propagation — ``build_container`` raising propagates out of
   the ``async with`` (per ``feedback_no_silent_defaults``).
3. Teardown — the settings the command read through the container's
   ``SiteConfig`` are stamped into ``app_settings.last_read_at`` while the
   caller's pool is still open (read telemetry, poindexter#756), and a
   failed stamp never masks the body's own exception.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from poindexter.cli._lifecycle import container_for_cli
from poindexter.services.container import AppContainer


class _StampRecordingPool:
    """A pool whose ``fetch`` serves build_container's settings probe and
    whose connections record the read-telemetry UPDATE."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.fetch = AsyncMock(return_value=rows)
        self.stamped: list[list[str]] = []

    def acquire(self) -> Any:
        pool = self

        class _Conn:
            async def fetch(self, *_args: Any) -> list:
                return []

            async def execute(self, _sql: str, keys: list[str], _restamp: int) -> str:
                pool.stamped.append(list(keys))
                return f"UPDATE {len(keys)}"

        class _Acquire:
            async def __aenter__(self) -> _Conn:
                return _Conn()

            async def __aexit__(self, *_exc: object) -> bool:
                return False

        return _Acquire()


class TestContainerForCli:
    async def test_yields_app_container_with_loaded_site_config(self):
        """Happy path: pool.fetch returns rows; manager yields a real
        AppContainer carrying a loaded SiteConfig + the same pool."""
        pool = _StampRecordingPool(
            [
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


class TestContainerForCliStampsReads:
    """The container's SiteConfig keeps its reads on the instance, and a CLI
    process exits with the command, so nothing else would ever flush them."""

    async def test_reads_are_stamped_when_the_block_exits(self):
        pool = _StampRecordingPool([{"key": "topic_batch_size", "value": "5"}])

        async with container_for_cli(pool) as container:
            container.site_config.get_int("topic_batch_size", 3)
            container.site_config.get("topic_ranking_model", "")
            assert pool.stamped == []  # not before the command is done

        assert len(pool.stamped) == 1
        assert {"topic_batch_size", "topic_ranking_model"} <= set(pool.stamped[0])

    async def test_reads_are_stamped_when_the_body_raises_and_the_error_propagates(self):
        pool = _StampRecordingPool([])

        with pytest.raises(ValueError, match="command failed"):
            async with container_for_cli(pool) as container:
                container.site_config.get("topic_batch_size")
                raise ValueError("command failed")

        assert pool.stamped and "topic_batch_size" in pool.stamped[0]

    async def test_a_failed_stamp_never_masks_the_bodys_exception(self, caplog):
        pool = _StampRecordingPool([])

        def _no_connection() -> Any:
            raise ConnectionResetError("connection reset by peer")

        pool.acquire = _no_connection  # type: ignore[method-assign]

        with caplog.at_level("WARNING"), pytest.raises(ValueError, match="command failed"):
            async with container_for_cli(pool) as container:
                container.site_config.get("topic_batch_size")
                raise ValueError("command failed")

        assert any("connection reset by peer" in r.getMessage() for r in caplog.records)
