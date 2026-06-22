"""Tests: ``poindexter pipeline regen`` (preview_gate component regen).

The regen command is a thin adapter: it resolves the paused task, delegates to
``approval_service.regen_at_gate`` (which sets the one-shot pending flag + bumps
the attempt counter), then resumes the graph from its checkpoint — mirroring the
approve-and-resume path of ``pipeline resume``. These tests pin the adapter
behaviour: right component routed, mutual-exclusion enforced, cap surfaced
without resuming.
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.pipeline import regen_command

pytestmark = pytest.mark.unit

_TID = "abc12345-0000-0000-0000-000000000000"


def _paused_row(**over):
    row = {
        "task_id": _TID,
        "status": "awaiting_gate",
        "awaiting_gate": "preview_gate",
        "gate_artifact": '{"title": "X"}',
        "gate_paused_at": None,
        "topic": "Topic",
        "template_slug": "canonical_blog",
    }
    row.update(over)
    return row


def _patches(*, row, regen=None, run_result=None, run_side_effect=None):
    runner_cls = MagicMock()
    runner_cls.return_value.run = AsyncMock(
        return_value=run_result, side_effect=run_side_effect,
    )
    regen_mock = regen or AsyncMock(return_value={
        "ok": True, "task_id": _TID, "gate_name": "preview_gate",
        "component": "images", "attempts": 1, "max_attempts": 3,
    })
    patches = [
        patch("poindexter.cli.pipeline._make_pool",
              new=AsyncMock(return_value=MagicMock(close=AsyncMock()))),
        patch("poindexter.cli.pipeline._make_site_config",
              new=AsyncMock(return_value=MagicMock())),
        patch("poindexter.cli.pipeline._fetch_paused_row",
              new=AsyncMock(return_value=row)),
        patch("services.approval_service.regen_at_gate", new=regen_mock),
        patch("services.template_runner.TemplateRunner", new=runner_cls),
        patch("poindexter.cli.pipeline._dsn",
              new=MagicMock(return_value="postgresql://test/dsn")),
    ]
    return patches, runner_cls, regen_mock


def _invoke(bundle, args):
    patches, _, _ = bundle
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return CliRunner().invoke(regen_command, args)


class TestRegenCommand:
    def test_images_routes_component_then_resumes(self):
        bundle = _patches(
            row=_paused_row(),
            run_result=SimpleNamespace(ok=False, halted_at="preview_gate"),
        )
        result = _invoke(bundle, [_TID, "--images"])
        assert result.exit_code == 0, result.output
        _, runner_cls, regen_mock = bundle
        regen_mock.assert_awaited_once()
        assert regen_mock.await_args.kwargs["component"] == "images"
        runner_cls.return_value.run.assert_awaited_once()

    def test_text_routes_component(self):
        bundle = _patches(
            row=_paused_row(),
            run_result=SimpleNamespace(ok=False, halted_at="preview_gate"),
        )
        result = _invoke(bundle, [_TID, "--text"])
        assert result.exit_code == 0, result.output
        _, _, regen_mock = bundle
        assert regen_mock.await_args.kwargs["component"] == "text"

    def test_steering_threads_through(self):
        bundle = _patches(
            row=_paused_row(),
            run_result=SimpleNamespace(ok=False, halted_at="preview_gate"),
        )
        result = _invoke(bundle, [_TID, "--images", "--steering", "less busy"])
        assert result.exit_code == 0, result.output
        _, _, regen_mock = bundle
        assert regen_mock.await_args.kwargs["steering"] == "less busy"

    def test_neither_flag_is_loud_error(self):
        bundle = _patches(row=_paused_row())
        result = _invoke(bundle, [_TID])
        assert result.exit_code != 0
        assert "exactly one" in result.output.lower()
        _, _, regen_mock = bundle
        regen_mock.assert_not_awaited()

    def test_both_flags_is_loud_error(self):
        bundle = _patches(row=_paused_row())
        result = _invoke(bundle, [_TID, "--images", "--text"])
        assert result.exit_code != 0
        _, _, regen_mock = bundle
        regen_mock.assert_not_awaited()

    def test_cap_reached_errors_without_resuming(self):
        from services.approval_service import RegenCapReachedError

        regen = AsyncMock(
            side_effect=RegenCapReachedError(
                "regen cap reached for images (3/3) on task — approve or reject"
            )
        )
        bundle = _patches(
            row=_paused_row(), regen=regen,
            run_result=SimpleNamespace(ok=True, halted_at=None),
        )
        result = _invoke(bundle, [_TID, "--images"])
        assert result.exit_code != 0
        assert "cap" in result.output.lower()
        _, runner_cls, _ = bundle
        runner_cls.return_value.run.assert_not_awaited()

    def test_passes_explicit_checkpointer_dsn(self):
        bundle = _patches(
            row=_paused_row(),
            run_result=SimpleNamespace(ok=False, halted_at="preview_gate"),
        )
        result = _invoke(bundle, [_TID, "--images"])
        assert result.exit_code == 0, result.output
        _, runner_cls, _ = bundle
        assert runner_cls.call_args.kwargs.get("checkpointer_dsn") == "postgresql://test/dsn"
