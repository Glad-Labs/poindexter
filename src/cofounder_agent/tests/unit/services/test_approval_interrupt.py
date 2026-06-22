"""Unit tests for interrupt()-based approval gates (Glad-Labs/poindexter#363).

Covers the four moving parts of the conversion from status-polling re-run to
true LangGraph ``interrupt()``:

1. The ``atoms.approval_gate`` atom: disabled → pass-through; rejected-history
   → halt; approved-history → pass-through; pending → calls ``interrupt()``;
   ``interrupt()`` returning (Command(resume=...) re-entry) → pass-through.
2. GraphInterrupt propagation through ``_wrap_atom`` and ``make_stage_node``
   (must NOT be swallowed into an error/halt record).
3. ``_wrap_atom`` merges the spec node's static config into the atom input.
4. ``TemplateRunner.run(resume=True)`` invokes ``ainvoke`` with a
   ``Command(resume=...)`` rather than the full data_state.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.errors import GraphInterrupt

from tests.unit.services._gate_fakes import FakeConn, FakePool, executed_sql

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**extra):
    base = {
        "task_id": "task-123",
        "gate_name": "draft_gate",
        "database_service": SimpleNamespace(pool=None),
        "site_config": object(),
    }
    base.update(extra)
    return base


def _state_with_pool(conn: FakeConn, **extra):
    pool = FakePool(conn)
    return _state(database_service=SimpleNamespace(pool=pool), **extra)


# ---------------------------------------------------------------------------
# 1. The approval_gate atom
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalGateAtom:
    async def test_disabled_gate_passes_through(self):
        from modules.content.atoms import approval_gate

        with patch(
            "services.approval_service.is_gate_enabled", return_value=False,
        ):
            out = await approval_gate.run(_state())
        assert out == {}

    async def test_missing_gate_name_passes_through(self):
        from modules.content.atoms import approval_gate

        out = await approval_gate.run(_state(gate_name=""))
        assert out == {}

    async def test_missing_task_id_passes_through(self):
        from modules.content.atoms import approval_gate

        out = await approval_gate.run(_state(task_id=""))
        assert out == {}

    async def test_approved_history_passes_through(self):
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result={"event_kind": "approved"})
        state = _state_with_pool(conn)
        with patch(
            "services.approval_service.is_gate_enabled", return_value=True,
        ):
            out = await approval_gate.run(state)
        # Approved on a prior pass → resume case → pass-through, no interrupt.
        assert out == {}

    async def test_rejected_history_halts(self):
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result={"event_kind": "rejected"})
        state = _state_with_pool(conn)
        with patch(
            "services.approval_service.is_gate_enabled", return_value=True,
        ):
            out = await approval_gate.run(state)
        assert out.get("_halt") is True
        assert "rejected" in out.get("_halt_reason", "")

    async def test_pending_calls_interrupt(self):
        """No prior decision → pause_at_gate + notify + interrupt()."""
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result=None)  # no gate_history row
        state = _state_with_pool(conn, title="Hello", topic="t")

        pause_mock = AsyncMock(return_value={"ok": True})
        notify_mock = AsyncMock()

        # interrupt() raises GraphInterrupt in the pending case (LangGraph
        # would catch it, checkpoint, and pause). Simulate that here.
        def _raise_interrupt(payload):
            raise GraphInterrupt(payload)

        with (
            patch("services.approval_service.is_gate_enabled", return_value=True),
            patch("services.approval_service.pause_at_gate", pause_mock),
            patch(
                "services.integrations.operator_notify.notify_operator",
                notify_mock,
            ),
            patch.object(approval_gate, "interrupt", _raise_interrupt),
        ):
            with pytest.raises(GraphInterrupt):
                await approval_gate.run(state)

        # The gate persisted state + paged the operator before interrupting.
        pause_mock.assert_awaited_once()
        assert pause_mock.await_args.kwargs["gate_name"] == "draft_gate"
        notify_mock.assert_awaited_once()
        # Critical (Telegram) page.
        assert notify_mock.await_args.kwargs.get("critical") is True

    async def test_interrupt_returning_value_passes_through(self):
        """Command(resume=...) re-entry: interrupt() RETURNS instead of raising.

        On resume LangGraph re-executes the atom from the top; interrupt()
        returns the resume value. The atom must treat that as approval and
        pass through (return {}), not re-pause.
        """
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result=None)
        state = _state_with_pool(conn, title="Hello")

        with (
            patch("services.approval_service.is_gate_enabled", return_value=True),
            patch(
                "services.approval_service.pause_at_gate",
                AsyncMock(return_value={"ok": True}),
            ),
            patch(
                "services.integrations.operator_notify.notify_operator",
                AsyncMock(),
            ),
            patch.object(
                approval_gate, "interrupt",
                lambda payload: {"approved": True},
            ),
        ):
            out = await approval_gate.run(state)
        assert out == {}

    async def test_no_pool_halts_loud(self):
        from modules.content.atoms import approval_gate

        # database_service present but pool is None.
        state = _state(database_service=SimpleNamespace(pool=None))
        with patch(
            "services.approval_service.is_gate_enabled", return_value=True,
        ):
            out = await approval_gate.run(state)
        assert out.get("_halt") is True
        assert "no DB pool" in out.get("_halt_reason", "")


# ---------------------------------------------------------------------------
# 1b. The approval_gate atom — stale-approval freshness check (c2)
# ---------------------------------------------------------------------------
#
# The gate-history 'approved' short-circuit exists so a resume doesn't re-pause.
# But after a crashed resume, the stale-inprogress sweep resets the task to
# 'pending' (bumping retry_count) and clears the LangGraph checkpoint — WITHOUT
# touching pipeline_gate_history. On the fresh re-run the gate must NOT honor
# the now-stale 'approved' row (that would republish regenerated content with
# no operator review). The approval is tagged with the retry_count it was
# granted at; the gate only honors it when that attempt matches the task's
# current retry_count.


@pytest.mark.unit
class TestApprovalGateRetryCountFreshness:
    async def test_stale_approval_re_pauses(self):
        """approved_at_retry_count != current retry_count → re-pause, NOT pass.

        Models the post-sweep fresh run: an 'approved' row from attempt 0
        survives, but the sweep bumped retry_count to 1. The gate must treat
        the stale approval as no-decision and pause again for a fresh review.
        """
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result={
            "event_kind": "approved",
            "approved_at_retry_count": "0",
            "current_retry_count": 1,
        })
        state = _state_with_pool(conn, title="Hello", topic="t")

        pause_mock = AsyncMock(return_value={"ok": True})

        def _raise_interrupt(payload):
            raise GraphInterrupt(payload)

        with (
            patch("services.approval_service.is_gate_enabled", return_value=True),
            patch("services.approval_service.pause_at_gate", pause_mock),
            patch(
                "services.integrations.operator_notify.notify_operator",
                AsyncMock(),
            ),
            patch.object(approval_gate, "interrupt", _raise_interrupt),
        ):
            with pytest.raises(GraphInterrupt):
                await approval_gate.run(state)

        # It re-paused (did not silently pass the stale approval through).
        pause_mock.assert_awaited_once()

    async def test_matching_approval_passes_through(self):
        """approved_at_retry_count == current retry_count → resume case → pass."""
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result={
            "event_kind": "approved",
            "approved_at_retry_count": "2",
            "current_retry_count": 2,
        })
        state = _state_with_pool(conn)
        with patch(
            "services.approval_service.is_gate_enabled", return_value=True,
        ):
            out = await approval_gate.run(state)
        assert out == {}

    async def test_legacy_untagged_approval_passes_through(self):
        """Backcompat: an 'approved' row written before the retry_count tag
        (no approved_at_retry_count) is still honored — we don't strand
        in-flight approvals that predate this change."""
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result={
            "event_kind": "approved",
            "approved_at_retry_count": None,
            "current_retry_count": 4,
        })
        state = _state_with_pool(conn)
        with patch(
            "services.approval_service.is_gate_enabled", return_value=True,
        ):
            out = await approval_gate.run(state)
        assert out == {}


# ---------------------------------------------------------------------------
# 1c. The approval_gate atom — pending-regen short-circuit (preview_gate)
# ---------------------------------------------------------------------------
#
# preview_gate adds a third resume outcome beyond approve/reject: a per-component
# regen. The operator surface sets pipeline_tasks.regen_<c>_pending=true and
# resumes the graph. The atom must read that flag BEFORE pausing, clear it
# (one-shot consume), and route _goto to the configured image/writer block — so
# it does NOT re-page, and the loop-back (pending now false) falls through to a
# single fresh review page. See docs/architecture/2026-06-21-component-scoped-
# regen-gate.md.


def _regen_fetchrow(*, images=False, text=False):
    """A FakeConn.fetchrow callable: no approved/rejected gate-history row, but
    pipeline_tasks shows the given pending flags."""

    def _fn(sql, args):
        if "pipeline_gate_history" in sql:
            return None  # _gate_decision: no approved/rejected decision
        if "regen_images_pending" in sql:  # _pending_regen
            return {"regen_images_pending": images, "regen_text_pending": text}
        return None

    return _fn


@pytest.mark.unit
class TestApprovalGatePendingRegen:
    _TARGETS = {"images": "plan_image_markers", "text": "generate_draft"}

    async def test_pending_image_regen_routes_goto_and_consumes(self):
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result=_regen_fetchrow(images=True))
        state = _state_with_pool(
            conn, gate_name="preview_gate",
            regen_targets=self._TARGETS, title="x", topic="t",
        )
        pause_mock = AsyncMock()
        notify_mock = AsyncMock()
        with (
            patch("services.approval_service.is_gate_enabled", return_value=True),
            patch("services.approval_service.pause_at_gate", pause_mock),
            patch(
                "services.integrations.operator_notify.notify_operator", notify_mock,
            ),
            # If the impl is missing, the atom falls through to interrupt();
            # return a sentinel so RED fails cleanly on the _goto assertion
            # rather than raising GraphInterrupt. In GREEN this is never called.
            patch.object(approval_gate, "interrupt", lambda payload: {"approved": True}),
        ):
            out = await approval_gate.run(state)

        assert out.get("_goto") == "plan_image_markers"
        assert "_halt" not in out
        # Consumed at the short-circuit → no pause, no page.
        pause_mock.assert_not_awaited()
        notify_mock.assert_not_awaited()
        # One-shot: the atom cleared the flag so the loop-back re-pauses.
        assert "regen_images_pending = false" in executed_sql(conn).lower()

    async def test_pending_text_regen_routes_goto_and_consumes(self):
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result=_regen_fetchrow(text=True))
        state = _state_with_pool(
            conn, gate_name="preview_gate",
            regen_targets=self._TARGETS, title="x", topic="t",
        )
        with (
            patch("services.approval_service.is_gate_enabled", return_value=True),
            patch("services.approval_service.pause_at_gate", AsyncMock()),
            patch(
                "services.integrations.operator_notify.notify_operator", AsyncMock(),
            ),
        ):
            out = await approval_gate.run(state)

        assert out.get("_goto") == "generate_draft"
        assert "_halt" not in out
        assert "regen_text_pending = false" in executed_sql(conn).lower()

    async def test_image_regen_outranks_stale_approval(self):
        """A pending regen takes priority over an 'approved' row (regen is the
        newer intent; the approval predates the operator asking for a redo)."""
        from modules.content.atoms import approval_gate

        def _fetchrow(sql, args):
            if "pipeline_gate_history" in sql:
                return {"event_kind": "approved"}  # stale approval present
            if "regen_images_pending" in sql:
                return {"regen_images_pending": True, "regen_text_pending": False}
            return None

        conn = FakeConn(fetchrow_result=_fetchrow)
        state = _state_with_pool(
            conn, gate_name="preview_gate", regen_targets=self._TARGETS,
        )
        with patch("services.approval_service.is_gate_enabled", return_value=True):
            out = await approval_gate.run(state)
        assert out.get("_goto") == "plan_image_markers"

    async def test_pending_regen_without_target_halts_loud(self):
        """Misconfig: pending regen but no regen_targets mapping → fail loud
        (no silent passthrough that would publish unreviewed content)."""
        from modules.content.atoms import approval_gate

        conn = FakeConn(fetchrow_result=_regen_fetchrow(images=True))
        state = _state_with_pool(conn, gate_name="preview_gate")  # no regen_targets
        with (
            patch("services.approval_service.is_gate_enabled", return_value=True),
            patch("services.approval_service.pause_at_gate", AsyncMock()),
            patch(
                "services.integrations.operator_notify.notify_operator", AsyncMock(),
            ),
            patch.object(approval_gate, "interrupt", lambda payload: {"approved": True}),
        ):
            out = await approval_gate.run(state)
        assert out.get("_halt") is True
        assert "regen target" in out.get("_halt_reason", "").lower()


# ---------------------------------------------------------------------------
# 2. GraphInterrupt propagation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGraphInterruptPropagation:
    async def test_wrap_atom_does_not_swallow_graph_interrupt(self):
        from services.pipeline_architect import _wrap_atom

        async def run_fn(state):
            raise GraphInterrupt({"paused": True})

        sink: list = []
        node = _wrap_atom(run_fn, "atoms.gate", "n1", sink)

        with pytest.raises(GraphInterrupt):
            await node({"task_id": "t"}, None)
        # No record written — the node suspended, it didn't fail.
        assert sink == []

    async def test_wrap_atom_still_catches_other_exceptions(self):
        """Regression guard: the GraphInterrupt re-raise must not break the
        normal error path (other exceptions still halt + record)."""
        from services.pipeline_architect import _wrap_atom

        async def boom(state):
            raise ValueError("nope")

        sink: list = []
        node = _wrap_atom(boom, "atoms.boom", "n2", sink)
        out = await node({"task_id": "t"}, None)
        assert out.get("_halt") is True
        assert len(sink) == 1
        assert sink[0].ok is False

    async def test_make_stage_node_does_not_swallow_graph_interrupt(self):
        from services.template_runner import make_stage_node

        # A fake stage whose execute() raises GraphInterrupt (mirrors a
        # stage.* virtual atom calling interrupt()).
        class _Stage:
            name = "interrupting_stage"
            timeout_seconds = 5
            halts_on_failure = True

            async def execute(self, context, config):
                raise GraphInterrupt({"paused": True})

        node = make_stage_node(_Stage(), pool=None, record_sink=[])

        # PluginConfig.load is hit before execute() — stub it to an enabled
        # config so we reach the stage.execute call.
        enabled_cfg = SimpleNamespace(
            enabled=True, config={}, get=lambda k, d=None: d,
        )
        with (
            patch(
                "plugins.config.PluginConfig.load",
                AsyncMock(return_value=enabled_cfg),
            ),
            patch(
                "services.template_runner._mark_stage_column", AsyncMock(),
            ),
            patch("services.template_runner._emit_progress", AsyncMock()),
        ):
            with pytest.raises(GraphInterrupt):
                await node({"task_id": "t"}, None)


# ---------------------------------------------------------------------------
# 3. _wrap_atom merges node config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWrapAtomConfigMerge:
    async def test_node_config_seeds_atom_input(self):
        from services.pipeline_architect import _wrap_atom

        seen: dict = {}

        async def run_fn(state):
            seen.update(state)
            return {}

        node = _wrap_atom(
            run_fn, "atoms.gate", "n1", None,
            node_config={"gate_name": "draft_gate", "extra": 1},
        )
        await node({"task_id": "t"}, None)
        assert seen["gate_name"] == "draft_gate"
        assert seen["extra"] == 1
        assert seen["task_id"] == "t"

    async def test_state_takes_precedence_over_config(self):
        from services.pipeline_architect import _wrap_atom

        seen: dict = {}

        async def run_fn(state):
            seen.update(state)
            return {}

        node = _wrap_atom(
            run_fn, "atoms.gate", "n1", None,
            node_config={"gate_name": "config_value"},
        )
        # State already has gate_name → wins over config fallback.
        await node({"task_id": "t", "gate_name": "state_value"}, None)
        assert seen["gate_name"] == "state_value"


# ---------------------------------------------------------------------------
# 4. TemplateRunner resume path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateRunnerResume:
    async def test_resume_invokes_with_command(self):
        """run(resume=True) calls ainvoke(Command(resume=...), config)."""
        from langgraph.types import Command

        from services.site_config import SiteConfig
        from services.template_runner import TemplateRunner

        sc = SiteConfig(initial_config={
            "pipeline_use_graph_def": "false",
            "template_runner_use_postgres_checkpointer": "false",
        })
        runner = TemplateRunner(pool=None, site_config=sc)

        captured = {}

        class _Compiled:
            async def ainvoke(self, value, config):
                captured["value"] = value
                captured["config"] = config
                return {"task_id": "task-123", "ok": True}

        class _Graph:
            def compile(self, checkpointer=None):
                return _Compiled()

        # Patch the template factory lookup so run() uses our fake graph.
        fake_templates = {"canonical_blog": lambda **kw: _Graph()}

        with (
            patch.dict(
                "services.pipeline_templates.TEMPLATES",
                fake_templates, clear=False,
            ),
            patch("services.template_runner._emit_progress", AsyncMock()),
        ):
            summary = await runner.run(
                "canonical_blog",
                {"task_id": "task-123", "topic": "t"},
                thread_id="task-123",
                resume=True,
                resume_value={"approved": True},
            )

        assert isinstance(captured["value"], Command)
        assert captured["value"].resume == {"approved": True}
        assert captured["config"]["configurable"]["thread_id"] == "task-123"
        assert summary.ok is True

    async def test_normal_run_invokes_with_data_state(self):
        """Backwards compat: resume=False (default) passes the data_state."""
        from services.site_config import SiteConfig
        from services.template_runner import TemplateRunner

        sc = SiteConfig(initial_config={
            "pipeline_use_graph_def": "false",
            "template_runner_use_postgres_checkpointer": "false",
        })
        runner = TemplateRunner(pool=None, site_config=sc)

        captured = {}

        class _Compiled:
            async def ainvoke(self, value, config):
                captured["value"] = value
                return {"task_id": "task-123"}

        class _Graph:
            def compile(self, checkpointer=None):
                return _Compiled()

        with (
            patch.dict(
                "services.pipeline_templates.TEMPLATES",
                {"canonical_blog": lambda **kw: _Graph()}, clear=False,
            ),
            patch("services.template_runner._emit_progress", AsyncMock()),
        ):
            await runner.run(
                "canonical_blog",
                {"task_id": "task-123", "topic": "t"},
                thread_id="task-123",
            )

        # Normal run passes the partitioned data_state dict, not a Command.
        assert isinstance(captured["value"], dict)
        assert captured["value"].get("task_id") == "task-123"
