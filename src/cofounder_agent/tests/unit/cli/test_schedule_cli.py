"""Regression tests for ``poindexter schedule show`` / ``shift`` / ``clear``
and ``poindexter publish-at`` prefix resolution.

These commands talk straight to ``services.scheduling_service``, which
exact-matches ``posts.id::text``. Operators paste the 8-char prefix the
``schedule list`` / dashboards render; it now resolves through the shared
:mod:`poindexter.cli._prefix` resolver first.

A no-match falls back to the original token so the service's own
not-found result (and its exit code) is preserved unchanged; an ambiguous
prefix exits 2.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli._prefix import AmbiguousPrefixError
from poindexter.cli.schedule import (
    publish_at_command,
    schedule_group,
)

FULL = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"


@pytest.fixture
def runner():
    return CliRunner()


async def _fake_with_pool(fn):
    """Stand-in for ``_with_pool`` — hand the inner fn a dummy pool."""
    return await fn(MagicMock())


def _result(*, ok=True, detail="ok"):
    return SimpleNamespace(ok=ok, detail=detail, count=1 if ok else 0, rows=[])


def _patch_with_pool():
    return patch("poindexter.cli.schedule._with_pool", new=_fake_with_pool)


def _patch_site_config():
    return patch(
        "poindexter.cli.schedule._load_site_config",
        new=AsyncMock(return_value=MagicMock()),
    )


@pytest.mark.unit
class TestScheduleShow:
    def test_prefix_resolves(self, runner):
        fake_show = AsyncMock(return_value=_result())
        with _patch_with_pool(), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix",
            new=AsyncMock(return_value=FULL),
        ), patch("services.scheduling_service.show_scheduled", new=fake_show):
            result = runner.invoke(schedule_group, ["show", "6bf91cc3"])

        assert result.exit_code == 0, result.output
        assert fake_show.await_args.args[0] == FULL

    def test_no_match_falls_back_to_original_preserving_not_found(self, runner):
        fake_show = AsyncMock(return_value=_result(ok=False, detail="not scheduled"))
        with _patch_with_pool(), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix",
            new=AsyncMock(return_value=None),
        ), patch("services.scheduling_service.show_scheduled", new=fake_show):
            result = runner.invoke(schedule_group, ["show", "deadbeef"])

        # service still saw the original token, and its not-ok → exit 1.
        assert fake_show.await_args.args[0] == "deadbeef"
        assert result.exit_code == 1

    def test_ambiguous_exits_2(self, runner):
        with _patch_with_pool(), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix",
            new=AsyncMock(side_effect=AmbiguousPrefixError(
                "abc", ["a1111111", "a2222222"], noun="post",
            )),
        ):
            result = runner.invoke(schedule_group, ["show", "abc"])

        assert result.exit_code == 2
        assert "matches 2 posts" in result.output


@pytest.mark.unit
class TestScheduleShift:
    def test_single_prefix_resolves(self, runner):
        fake_shift = AsyncMock(return_value=_result())
        with _patch_with_pool(), _patch_site_config(), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix",
            new=AsyncMock(return_value=FULL),
        ), patch("services.scheduling_service.shift", new=fake_shift):
            result = runner.invoke(schedule_group, ["shift", "6bf91cc3", "--by", "1h"])

        assert result.exit_code == 0, result.output
        assert fake_shift.await_args.kwargs["post_ids"] == [FULL]

    def test_all_skips_resolution(self, runner):
        fake_shift = AsyncMock(return_value=_result())
        spy_resolve = AsyncMock(return_value=FULL)
        with _patch_with_pool(), _patch_site_config(), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix", new=spy_resolve,
        ), patch("services.scheduling_service.shift", new=fake_shift):
            result = runner.invoke(schedule_group, ["shift", "--all", "--by", "1h"])

        assert result.exit_code == 0, result.output
        spy_resolve.assert_not_awaited()
        assert fake_shift.await_args.kwargs["post_ids"] is None


@pytest.mark.unit
class TestScheduleClear:
    def test_single_prefix_resolves(self, runner):
        fake_clear = AsyncMock(return_value=_result())
        with _patch_with_pool(), _patch_site_config(), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix",
            new=AsyncMock(return_value=FULL),
        ), patch("services.scheduling_service.clear", new=fake_clear):
            result = runner.invoke(schedule_group, ["clear", "6bf91cc3"])

        assert result.exit_code == 0, result.output
        assert fake_clear.await_args.kwargs["post_ids"] == [FULL]


@pytest.mark.unit
class TestPublishAt:
    def test_prefix_resolves(self, runner):
        fake_assign = AsyncMock(return_value=_result())
        with _patch_with_pool(), _patch_site_config(), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix",
            new=AsyncMock(return_value=FULL),
        ), patch("services.scheduling_service.assign_slot", new=fake_assign):
            result = runner.invoke(publish_at_command, ["6bf91cc3", "now"])

        assert result.exit_code == 0, result.output
        assert fake_assign.await_args.args[0] == FULL

    def test_ambiguous_exits_2(self, runner):
        with _patch_with_pool(), _patch_site_config(), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix",
            new=AsyncMock(side_effect=AmbiguousPrefixError(
                "abc", ["a1111111", "a2222222"], noun="post",
            )),
        ):
            result = runner.invoke(publish_at_command, ["abc", "now"])

        assert result.exit_code == 2
        assert "matches 2 posts" in result.output
