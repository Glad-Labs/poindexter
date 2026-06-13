"""Regression tests for ``poindexter pipeline status`` / ``resume``.

#1490 taught ``_fetch_paused_row`` to accept short id prefixes but shipped
with no tests. This module pins that behavior now that the prefix logic is
delegated to the shared :mod:`poindexter.cli._prefix` resolver — full UUID
shortcut, single-prefix expansion, zero → not-found, many → an ambiguous
``click.UsageError`` that lists the candidates.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from poindexter.cli.pipeline import (
    _fetch_paused_row,
    pipeline_group,
)

FULL = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"


def _paused_row(**overrides):
    row = {
        "task_id": FULL,
        "status": "awaiting_gate",
        "awaiting_gate": "draft_gate",
        "gate_artifact": None,
        "gate_paused_at": None,
        "topic": "DDR5 latency",
        "template_slug": "canonical_blog",
    }
    row.update(overrides)
    return row


def _pool(*, like_rows=None, exact_row=None):
    """asyncpg pool double exposing both ``conn.fetch`` (the LIKE prefix
    scan the resolver runs) and ``conn.fetchrow`` (the exact re-fetch)."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=list(like_rows or []))
    conn.fetchrow = AsyncMock(return_value=exact_row)

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    pool.close = AsyncMock()
    return pool, conn


# ---------------------------------------------------------------------------
# _fetch_paused_row — resolution + row fetch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchPausedRow:
    @pytest.mark.asyncio
    async def test_full_uuid_skips_like_scan(self):
        """A full UUID resolves with no DB round trip, then the row is fetched."""
        pool, conn = _pool(exact_row=_paused_row())
        out = await _fetch_paused_row(pool, FULL)
        assert out["task_id"] == FULL
        conn.fetch.assert_not_called()  # no LIKE scan for a full UUID
        conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_prefix_resolves_then_fetches_with_full_id(self):
        pool, conn = _pool(like_rows=[{"task_id": FULL}], exact_row=_paused_row())
        out = await _fetch_paused_row(pool, "6bf91cc3")
        assert out["task_id"] == FULL
        # The exact re-fetch used the EXPANDED id, not the prefix.
        assert conn.fetchrow.await_args.args[-1] == FULL

    @pytest.mark.asyncio
    async def test_zero_match_returns_none_without_fetchrow(self):
        pool, conn = _pool(like_rows=[])
        assert await _fetch_paused_row(pool, "deadbeef") is None
        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_ambiguous_prefix_raises_usage_error(self):
        pool, _ = _pool(like_rows=[
            {"task_id": "a1111111-1111-1111-1111-111111111111"},
            {"task_id": "a2222222-2222-2222-2222-222222222222"},
        ])
        with pytest.raises(click.UsageError) as exc:
            await _fetch_paused_row(pool, "a")
        assert "matches 2 tasks" in str(exc.value)


# ---------------------------------------------------------------------------
# status / resume command glue (exit codes preserved per #1490)
# ---------------------------------------------------------------------------


def _patched_pool():
    return patch(
        "poindexter.cli.pipeline._make_pool",
        new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
    )


@pytest.mark.unit
class TestStatusCommand:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_paused_renders_full_id_exit_0(self, runner):
        with _patched_pool(), patch(
            "poindexter.cli.pipeline._fetch_paused_row",
            new=AsyncMock(return_value=_paused_row()),
        ):
            result = runner.invoke(pipeline_group, ["status", "6bf91cc3"])
        assert result.exit_code == 0, result.output
        assert FULL in result.output
        assert "PAUSED" in result.output

    def test_not_found_exit_1(self, runner):
        with _patched_pool(), patch(
            "poindexter.cli.pipeline._fetch_paused_row",
            new=AsyncMock(return_value=None),
        ):
            result = runner.invoke(pipeline_group, ["status", "deadbeef"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_ambiguous_prefix_lists_candidates(self, runner):
        from poindexter.cli._prefix import AmbiguousPrefixError

        with _patched_pool(), patch(
            "poindexter.cli.pipeline._fetch_paused_row",
            new=AsyncMock(side_effect=AmbiguousPrefixError(
                "abc", ["a1111111", "a2222222"], noun="task",
            )),
        ):
            result = runner.invoke(pipeline_group, ["status", "abc"])
        assert result.exit_code != 0
        assert "matches 2 tasks" in result.output


@pytest.mark.unit
class TestResumeCommand:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_not_found_exit_1(self, runner):
        with _patched_pool(), patch(
            "poindexter.cli.pipeline._fetch_paused_row",
            new=AsyncMock(return_value=None),
        ):
            result = runner.invoke(pipeline_group, ["resume", "deadbeef"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_ambiguous_prefix_exits_nonzero(self, runner):
        from poindexter.cli._prefix import AmbiguousPrefixError

        with _patched_pool(), patch(
            "poindexter.cli.pipeline._fetch_paused_row",
            new=AsyncMock(side_effect=AmbiguousPrefixError(
                "abc", ["a1111111", "a2222222"], noun="task",
            )),
        ):
            result = runner.invoke(pipeline_group, ["resume", "abc"])
        assert result.exit_code != 0
        assert "matches 2 tasks" in result.output
