"""Regression tests for ``poindexter approve-publish`` / ``reject-publish``
/ ``show-pending-publish`` prefix resolution.

These commands open a pool and hand ``post_id`` straight to
``services.posts_approval_service``, which exact-matches ``posts.id::text``.
An operator pasting the 8-char prefix the dashboards show got a silent
"not found". They now resolve the prefix through the shared
:mod:`poindexter.cli._prefix` resolver first.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli._prefix import AmbiguousPrefixError
from poindexter.cli.publish_approval import (
    approve_publish_command,
    reject_publish_command,
    show_pending_publish_command,
)

FULL = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"


@pytest.fixture
def runner():
    return CliRunner()


def _patched_env():
    """Patch the pool factory + site_config bootstrap for one command run."""
    return (
        patch(
            "poindexter.cli.publish_approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ),
        patch(
            "poindexter.cli.publish_approval._make_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ),
    )


@pytest.mark.unit
class TestApprovePublishPrefix:
    def test_prefix_resolves_then_approves(self, runner):
        fake_svc = AsyncMock(return_value={"gate_name": "final_publish_approval"})
        p_pool, p_cfg = _patched_env()
        with p_pool, p_cfg, patch(
            "poindexter.cli.publish_approval.resolve_uuid_prefix",
            new=AsyncMock(return_value=FULL),
        ), patch(
            "services.posts_approval_service.approve_publish",
            new=fake_svc,
        ):
            result = runner.invoke(approve_publish_command, ["6bf91cc3"])

        assert result.exit_code == 0, result.output
        assert fake_svc.await_args.kwargs["post_id"] == FULL

    def test_ambiguous_prefix_exits_2(self, runner):
        p_pool, p_cfg = _patched_env()
        with p_pool, p_cfg, patch(
            "poindexter.cli.publish_approval.resolve_uuid_prefix",
            new=AsyncMock(side_effect=AmbiguousPrefixError(
                "abc", ["a1111111", "a2222222"], noun="post",
            )),
        ):
            result = runner.invoke(approve_publish_command, ["abc"])

        assert result.exit_code == 2
        assert "matches 2 posts" in result.output

    def test_not_found_exits_nonzero(self, runner):
        p_pool, p_cfg = _patched_env()
        with p_pool, p_cfg, patch(
            "poindexter.cli.publish_approval.resolve_uuid_prefix",
            new=AsyncMock(return_value=None),
        ):
            result = runner.invoke(approve_publish_command, ["deadbeef"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()


@pytest.mark.unit
class TestRejectPublishPrefix:
    def test_prefix_resolves_then_rejects(self, runner):
        fake_svc = AsyncMock(return_value={
            "gate_name": "final_publish_approval", "new_status": "rejected",
        })
        p_pool, p_cfg = _patched_env()
        with p_pool, p_cfg, patch(
            "poindexter.cli.publish_approval.resolve_uuid_prefix",
            new=AsyncMock(return_value=FULL),
        ), patch(
            "services.posts_approval_service.reject_publish",
            new=fake_svc,
        ):
            result = runner.invoke(reject_publish_command, ["6bf91cc3", "--reason", "x"])

        assert result.exit_code == 0, result.output
        assert fake_svc.await_args.kwargs["post_id"] == FULL


@pytest.mark.unit
class TestShowPendingPublishPrefix:
    def test_prefix_resolves_then_shows(self, runner):
        fake_svc = AsyncMock(return_value={
            "post_id": FULL,
            "gate_name": "final_publish_approval",
            "gate_paused_at": None,
            "status": "scheduled",
            "published_at": None,
            "slug": "s",
            "title": "t",
            "artifact": {},
        })
        p_pool, p_cfg = _patched_env()
        with p_pool, p_cfg, patch(
            "poindexter.cli.publish_approval.resolve_uuid_prefix",
            new=AsyncMock(return_value=FULL),
        ), patch(
            "services.posts_approval_service.show_pending_publish",
            new=fake_svc,
        ):
            result = runner.invoke(show_pending_publish_command, ["6bf91cc3"])

        assert result.exit_code == 0, result.output
        assert fake_svc.await_args.kwargs["post_id"] == FULL
        assert FULL in result.output
