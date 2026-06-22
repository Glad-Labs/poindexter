"""Regression: CLI resume/regen must thread the FULL pipeline handles.

The ``poindexter pipeline resume`` / ``regen`` commands resume an
interrupt()-paused graph from its checkpoint. Both used to hand the runner a
``_PoolShim`` (a stand-in exposing only ``.pool``) and NO ``platform`` key.

That was silently fine for the original *terminal* gate (``draft_gate`` resume
just flips status), but ``preview_gate`` sits MID-GRAPH: resuming it re-runs the
image / QA / finalize atoms. Those call full ``DatabaseService`` delegate
methods (``update_task``, ``create_quality_evaluation`` …) and dispatch SDXL /
LLM prompts through ``platform.dispatch.complete``. With the thin shim a real
``regen --images`` halted at ``content.persist_task`` with
``'_PoolShim' object has no attribute 'update_task'`` and SDXL inline silently
fell back to Pexels (``platform`` was ``None``).

The fix: build the same handles the Prefect subprocess builds — a real
``DatabaseService`` (whose ``initialize()`` also installs the global
``AuditLogger`` that the platform builder needs) plus a capability-scoped
``platform`` via ``build_platform_for_subprocess`` — and thread BOTH into the
resume state. These tests assert the state dict handed to ``TemplateRunner.run``
carries a real (delegate-bearing) ``database_service`` and the built
``platform``, for both the regen leg and the approve leg.
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.pipeline import regen_command, resume_command

pytestmark = pytest.mark.unit

_TID = "abc12345-0000-0000-0000-000000000000"
_PLATFORM_SENTINEL = object()


def _preview_gate_row(**over):
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


class _FakeDatabaseService:
    """Real-shaped DatabaseService double: bears the delegate methods the
    mid-graph atoms call, so a test can prove the state did NOT get a thin shim.
    """

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self.pool = MagicMock(name="db_service_pool")
        self.initialized = False
        self.closed = False

    async def initialize(self):
        self.initialized = True

    async def close(self):
        self.closed = True

    # The methods whose absence on _PoolShim crashed the regen leg.
    async def update_task(self, *a, **k):  # pragma: no cover - presence is the point
        return None

    async def create_quality_evaluation(self, *a, **k):  # pragma: no cover
        return None


def _handle_patches(stack: ExitStack) -> MagicMock:
    """Patch the shared seams + the new handle builders. Returns the runner_cls
    mock so callers can inspect the state handed to ``.run``."""
    runner_cls = MagicMock()
    runner_cls.return_value.run = AsyncMock(
        return_value=SimpleNamespace(ok=True, halted_at=None)
    )
    bp_mock = MagicMock(return_value=_PLATFORM_SENTINEL)

    for p in [
        patch("poindexter.cli.pipeline._make_pool",
              new=AsyncMock(return_value=MagicMock(close=AsyncMock()))),
        patch("poindexter.cli.pipeline._make_site_config",
              new=AsyncMock(return_value=MagicMock(name="site_config"))),
        patch("poindexter.cli.pipeline._dsn",
              new=MagicMock(return_value="postgresql://test/dsn")),
        # The new full-handle builders (resolved via local import at call time,
        # so patch them on their source modules).
        patch("services.database_service.DatabaseService", new=_FakeDatabaseService),
        patch("services.di_wiring.build_platform_for_subprocess", new=bp_mock),
        patch("services.template_runner.TemplateRunner", new=runner_cls),
        patch("services.template_runner.has_resumable_checkpoint",
              new=AsyncMock(return_value=False)),
        patch("services.approval_service.regen_at_gate",
              new=AsyncMock(return_value={"attempts": 1, "max_attempts": 3})),
        patch("services.approval_service.approve",
              new=AsyncMock(return_value={
                  "ok": True, "task_id": _TID,
                  "gate_name": "preview_gate", "gate_history_id": 7,
              })),
        patch("services.approval_service.rollback_resume_approval",
              new=AsyncMock(return_value={"ok": True})),
    ]:
        stack.enter_context(p)
    runner_cls._bp_mock = bp_mock
    return runner_cls


def _state_handed_to_run(runner_cls: MagicMock) -> dict:
    runner_cls.return_value.run.assert_awaited_once()
    # run(template_slug, state_dict, thread_id=..., resume=True, ...)
    return runner_cls.return_value.run.call_args.args[1]


class TestRegenLegThreadsFullHandles:
    def test_state_carries_real_db_service_and_platform(self):
        with ExitStack() as stack:
            with patch("poindexter.cli.pipeline._fetch_paused_row",
                       new=AsyncMock(return_value=_preview_gate_row())):
                runner_cls = _handle_patches(stack)
                result = CliRunner().invoke(regen_command, [_TID, "--images"])

        assert result.exit_code == 0, result.output
        state = _state_handed_to_run(runner_cls)

        # platform must be the built handle, not absent/None.
        assert state.get("platform") is _PLATFORM_SENTINEL

        # database_service must be the real, initialized DatabaseService —
        # NOT a thin shim. Presence of update_task is the discriminator.
        db = state.get("database_service")
        assert isinstance(db, _FakeDatabaseService)
        assert db.initialized is True
        assert hasattr(db, "update_task")

        # platform was built from the db_service's pool + site_config.
        bp = runner_cls._bp_mock
        bp.assert_called_once()
        assert bp.call_args.args[0] is db.pool

    def test_db_service_is_closed_after_resume(self):
        captured = {}
        orig_init = _FakeDatabaseService.__init__

        def _capturing_init(self, *a, **k):
            orig_init(self, *a, **k)
            captured["db"] = self

        with ExitStack() as stack:
            with patch("poindexter.cli.pipeline._fetch_paused_row",
                       new=AsyncMock(return_value=_preview_gate_row())), \
                 patch.object(_FakeDatabaseService, "__init__", _capturing_init):
                runner_cls = _handle_patches(stack)
                result = CliRunner().invoke(regen_command, [_TID, "--images"])

        assert result.exit_code == 0, result.output
        assert captured["db"].closed is True


class TestApproveLegThreadsFullHandles:
    def test_resume_state_carries_real_db_service_and_platform(self):
        with ExitStack() as stack:
            with patch("poindexter.cli.pipeline._fetch_paused_row",
                       new=AsyncMock(return_value=_preview_gate_row())):
                runner_cls = _handle_patches(stack)
                result = CliRunner().invoke(resume_command, [_TID])

        assert result.exit_code == 0, result.output
        state = _state_handed_to_run(runner_cls)
        assert state.get("platform") is _PLATFORM_SENTINEL
        db = state.get("database_service")
        assert isinstance(db, _FakeDatabaseService)
        assert db.initialized is True
