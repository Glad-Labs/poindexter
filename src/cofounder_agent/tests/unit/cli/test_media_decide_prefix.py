"""Regression tests for ``poindexter media approve`` / ``reject`` prefix
resolution.

``media_approval_service.decide`` casts ``post_id = $1::uuid``, so pasting
the 8-char prefix ``media pending`` renders tripped asyncpg's client-side
UUID validation. The CLI now resolves the prefix against ``posts.id`` first
through the shared :mod:`poindexter.cli._prefix` resolver.

The ``media`` fixture re-fetches the module fresh each test: the sibling
``test_media_cli.py`` swaps ``poindexter.cli.media`` out of ``sys.modules``
(its ``_import_media_module`` helper) to stub the podcast/video producing
services, so a module-level import here would patch one object and invoke
another. Re-fetching keeps patch target and invocation target identical.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli._prefix import AmbiguousPrefixError

FULL = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def media():
    sys.modules.pop("poindexter.cli.media", None)
    from poindexter.cli import media as media_mod
    return media_mod


def _patched_pool(media_mod):
    return patch.object(
        media_mod, "_make_pool",
        new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
    )


@pytest.mark.unit
class TestMediaApprovePrefix:
    def test_prefix_resolves_then_decides(self, runner, media):
        fake_decide = AsyncMock()
        fake_resolve = AsyncMock(return_value=FULL)
        with _patched_pool(media), patch.object(
            media, "resolve_uuid_prefix", new=fake_resolve,
        ), patch(
            "services.media_approval_service.decide", new=fake_decide,
        ):
            result = runner.invoke(media.media_group, ["approve", "6bf91cc3", "podcast"])

        assert result.exit_code == 0, result.output
        # decide() got the EXPANDED id as its post_id positional (2nd arg).
        assert fake_decide.await_args.args[1] == FULL
        # CLI wires a loaded site_config into decide() so an approve rebuilds
        # the matching R2 feed immediately (self-healing propagation).
        assert fake_decide.await_args.kwargs.get("site_config") is not None
        assert "6bf91cc3" in result.output
        # Resolution is medium-scoped against media_approvals (the surface
        # `media pending` renders), not a global posts.id lookup (#1511).
        rkw = fake_resolve.await_args.kwargs
        assert rkw["table"] == "media_approvals"
        assert rkw["params"] == ("podcast",)

    def test_ambiguous_prefix_exits_2(self, runner, media):
        with _patched_pool(media), patch.object(
            media, "resolve_uuid_prefix",
            new=AsyncMock(side_effect=AmbiguousPrefixError(
                "abc", ["a1111111", "a2222222"], noun="post",
            )),
        ):
            result = runner.invoke(media.media_group, ["approve", "abc", "podcast"])

        assert result.exit_code == 2
        assert "matches 2 posts" in result.output

    def test_not_found_exits_2(self, runner, media):
        with _patched_pool(media), patch.object(
            media, "resolve_uuid_prefix", new=AsyncMock(return_value=None),
        ):
            result = runner.invoke(media.media_group, ["approve", "deadbeef", "podcast"])

        assert result.exit_code == 2
        # zero-match → the medium-specific hint, not a bare "not found".
        assert "no podcast media" in result.output.lower()


@pytest.mark.unit
class TestMediaRejectPrefix:
    def test_prefix_resolves_then_decides(self, runner, media):
        fake_decide = AsyncMock()
        with _patched_pool(media), patch.object(
            media, "resolve_uuid_prefix", new=AsyncMock(return_value=FULL),
        ), patch(
            "services.media_approval_service.decide", new=fake_decide,
        ):
            result = runner.invoke(
                media.media_group, ["reject", "6bf91cc3", "video", "--note", "tts glitch"],
            )

        assert result.exit_code == 0, result.output
        assert fake_decide.await_args.args[1] == FULL
        assert fake_decide.await_args.kwargs["approved"] is False
