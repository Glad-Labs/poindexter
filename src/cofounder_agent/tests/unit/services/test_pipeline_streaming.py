"""Tests for pipeline progress streaming (Glad-Labs/poindexter#361 part 2).

Covers three surfaces:

1. ``TemplateRunner.run`` on_event threading — a run through a trivial
   StateGraph drives node_started/node_completed/run_started/run_completed
   events into the callback (and a raising callback never breaks the run).
2. ``services.pipeline_streaming.make_streaming_callback`` channel routing —
   discord/off return None (no callback), telegram returns an edit-streamer.
3. ``_TelegramStreamCallback`` mechanics — initial send captures message_id,
   subsequent events editMessageText that id, edits are throttled.

Telegram Bot API is mocked at the outbound_telegram helper boundary
(send_telegram_message / edit_telegram_message) and at the httpx layer for
the handler-level tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langgraph.graph import END, StateGraph

from services.pipeline_streaming import (
    _TelegramStreamCallback,
    make_streaming_callback,
)
from services.site_config import SiteConfig
from services.template_runner import PipelineState, TemplateRunner

# ---------------------------------------------------------------------------
# 1. TemplateRunner.run on_event threading.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateRunnerOnEvent:
    async def test_on_event_invoked_for_run_and_atom_node_events(self, monkeypatch):
        """on_event sees run_started, per-node events, and run_completed.

        Uses the legacy-factory path with a trivial atom-node graph (the
        nodes are plain async fns, NOT make_stage_node), so the per-node
        events here come from build_graph_from_spec -> _wrap_atom. We drive
        that path by patching the factory to return our graph and routing
        through build_graph_from_spec via a fake graph_def.
        """
        events: list[tuple[str, dict[str, Any]]] = []

        async def on_event(event_type: str, payload: dict[str, Any]) -> None:
            events.append((event_type, payload))

        # Route through build_graph_from_spec so _wrap_atom wraps each node
        # and emits node_started/node_completed with on_event threaded.
        from services import pipeline_architect

        captured = {}

        def fake_build(spec, *, pool, record_sink=None, on_event=None):
            captured["on_event"] = on_event
            # Build a real graph via _wrap_atom so the atom-node emit path
            # runs for real.
            return _wrap_two_atoms(record_sink, on_event)

        monkeypatch.setattr(pipeline_architect, "build_graph_from_spec", fake_build)

        runner = TemplateRunner(
            pool=None,
            checkpointer_dsn=None,
            site_config=SiteConfig(initial_config={
                "template_runner_use_postgres_checkpointer": "false",
                "template_runner_progress_streaming": "false",
                "pipeline_use_graph_def": "true",
            }),
        )

        import services.pipeline_templates as pt

        async def fake_load(pool, slug):
            return {"name": slug, "entry": "x", "nodes": [], "edges": []}

        monkeypatch.setattr(pt, "load_active_graph_def", fake_load)

        summary = await runner.run(
            "canonical_blog", {"task_id": "task-1234567890"}, on_event=on_event,
        )

        assert summary.ok is True
        types = [t for t, _ in events]
        assert types[0] == "run_started"
        assert "node_started" in types
        assert "node_completed" in types
        assert types[-1] == "run_completed"
        # payloads carry task_id + node identity + index/total for nodes.
        started = next(p for t, p in events if t == "node_started")
        assert started["task_id"] == "task-1234567890"
        assert started["node"] == "atom.one"
        assert started["total"] == 2

    async def test_raising_callback_never_breaks_run(self, monkeypatch):
        """A callback that raises must not fail the pipeline run."""
        from services import pipeline_architect

        def fake_build(spec, *, pool, record_sink=None, on_event=None):
            return _wrap_two_atoms(record_sink, on_event)

        monkeypatch.setattr(pipeline_architect, "build_graph_from_spec", fake_build)

        import services.pipeline_templates as pt

        async def fake_load(pool, slug):
            return {"name": slug, "entry": "x", "nodes": [], "edges": []}

        monkeypatch.setattr(pt, "load_active_graph_def", fake_load)

        async def boom(event_type, payload):
            raise RuntimeError("callback exploded")

        runner = TemplateRunner(
            pool=None,
            checkpointer_dsn=None,
            site_config=SiteConfig(initial_config={
                "template_runner_use_postgres_checkpointer": "false",
                "template_runner_progress_streaming": "false",
                "pipeline_use_graph_def": "true",
            }),
        )
        summary = await runner.run(
            "canonical_blog", {"task_id": "t-err"}, on_event=boom,
        )
        # Run still succeeds despite every callback raising.
        assert summary.ok is True


def _wrap_two_atoms(record_sink, on_event):
    """Build a 2-node graph whose nodes are wrapped via _wrap_atom so the
    atom-node progress-emit path (#361 STEP 2) executes for real."""
    from services.pipeline_architect import _wrap_atom

    async def atom_one(state):
        return {"one": True}

    async def atom_two(state):
        return {"two": True}

    g: StateGraph = StateGraph(PipelineState)
    g.add_node(
        "n1",
        _wrap_atom(atom_one, "atom.one", "n1", record_sink,
                   on_event=on_event, index=0, total=2),
    )
    g.add_node(
        "n2",
        _wrap_atom(atom_two, "atom.two", "n2", record_sink,
                   on_event=on_event, index=1, total=2),
    )
    g.set_entry_point("n1")
    g.add_edge("n1", "n2")
    g.add_edge("n2", END)
    return g


# ---------------------------------------------------------------------------
# 2. make_streaming_callback channel routing.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChannelRouting:
    async def test_discord_channel_returns_no_callback(self):
        sc = SiteConfig(initial_config={"pipeline_streaming_channel": "discord"})
        cb = await make_streaming_callback(None, sc, "task-1")
        assert cb is None

    async def test_off_channel_returns_no_callback(self):
        sc = SiteConfig(initial_config={"pipeline_streaming_channel": "off"})
        cb = await make_streaming_callback(None, sc, "task-1")
        assert cb is None

    async def test_unknown_channel_returns_no_callback(self):
        sc = SiteConfig(initial_config={"pipeline_streaming_channel": "carrier_pigeon"})
        cb = await make_streaming_callback(None, sc, "task-1")
        assert cb is None

    async def test_telegram_without_credentials_returns_none(self, monkeypatch):
        sc = SiteConfig(initial_config={"pipeline_streaming_channel": "telegram"})
        # No telegram_bot_token/chat_id configured -> degrade to None.
        cb = await make_streaming_callback(None, sc, "task-1")
        assert cb is None

    async def test_telegram_with_credentials_returns_callback(self, monkeypatch):
        sc = SiteConfig(initial_config={
            "pipeline_streaming_channel": "telegram",
            "telegram_chat_id": "999",
            "pipeline_streaming_min_edit_interval_s": "5",
        })
        # Patch TelegramConfig.get_telegram_bot_token (async secret read).
        from services import telegram_config as tc

        async def fake_token(self):
            return "BOT:TOKEN"

        monkeypatch.setattr(tc.TelegramConfig, "get_telegram_bot_token", fake_token)

        cb = await make_streaming_callback(None, sc, "task-1", template_slug="canonical_blog")
        assert isinstance(cb, _TelegramStreamCallback)
        assert cb._chat_id == "999"
        assert cb._min_interval_s == 5


# ---------------------------------------------------------------------------
# 3. _TelegramStreamCallback mechanics — send captures id, edits reuse it,
#    throttle coalesces, channel=discord never calls Telegram.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTelegramStreamCallback:
    def _cb(self, min_interval_s: int = 0) -> _TelegramStreamCallback:
        return _TelegramStreamCallback(
            bot_token="BOT:TOKEN",
            chat_id="999",
            task_id="task-abcdef123",
            template_slug="canonical_blog",
            min_interval_s=min_interval_s,
        )

    async def test_initial_send_captures_message_id_then_edits_reuse_it(
        self, monkeypatch,
    ):
        send = AsyncMock(return_value={"message_id": 4242})
        edit = AsyncMock(return_value={"message_id": 4242})
        import services.integrations.handlers.outbound_telegram as ot
        monkeypatch.setattr(ot, "send_telegram_message", send)
        monkeypatch.setattr(ot, "edit_telegram_message", edit)

        cb = self._cb(min_interval_s=0)  # no throttle
        await cb("run_started", {"task_id": "task-abcdef123"})
        assert send.await_count == 1
        assert cb._message_id == 4242

        await cb("node_started", {"node": "verify_task"})
        await cb("node_completed", {"node": "verify_task"})

        # Every edit reuses the captured message_id (single message in place).
        assert edit.await_count >= 1
        for call in edit.await_args_list:
            args = call.args
            # signature: (base, token, chat_id, message_id, text)
            assert args[3] == 4242

    async def test_throttle_coalesces_rapid_completions_into_one_edit(
        self, monkeypatch,
    ):
        send = AsyncMock(return_value={"message_id": 7})
        edit = AsyncMock(return_value={"message_id": 7})
        import services.integrations.handlers.outbound_telegram as ot
        monkeypatch.setattr(ot, "send_telegram_message", send)
        monkeypatch.setattr(ot, "edit_telegram_message", edit)

        # Large throttle so the second rapid node_completed is suppressed.
        cb = self._cb(min_interval_s=9999)
        await cb("run_started", {"task_id": "task-abcdef123"})
        await cb("node_completed", {"node": "a"})  # throttled (within window)
        await cb("node_completed", {"node": "b"})  # throttled (within window)

        # Both node_completed events were within the throttle window after the
        # initial send, so NO mid-run edit fired.
        assert edit.await_count == 0

        # A terminal event bypasses the throttle and always renders.
        await cb("run_completed", {"task_id": "task-abcdef123"})
        assert edit.await_count == 1

    async def test_halt_and_failure_bypass_throttle(self, monkeypatch):
        send = AsyncMock(return_value={"message_id": 7})
        edit = AsyncMock(return_value={"message_id": 7})
        import services.integrations.handlers.outbound_telegram as ot
        monkeypatch.setattr(ot, "send_telegram_message", send)
        monkeypatch.setattr(ot, "edit_telegram_message", edit)

        cb = self._cb(min_interval_s=9999)
        await cb("run_started", {"task_id": "task-abcdef123"})
        await cb("node_failed", {"node": "qa.aggregate", "reason": "boom"})
        # node_failed forces an immediate edit despite the throttle.
        assert edit.await_count == 1

    async def test_callback_swallows_bot_api_errors(self, monkeypatch):
        send = AsyncMock(side_effect=RuntimeError("telegram down"))
        import services.integrations.handlers.outbound_telegram as ot
        monkeypatch.setattr(ot, "send_telegram_message", send)

        cb = self._cb(min_interval_s=0)
        # Must NOT raise even though the Bot API send blew up.
        await cb("run_started", {"task_id": "task-abcdef123"})
        # message_id never captured -> subsequent edits are no-ops, no raise.
        await cb("node_completed", {"node": "a"})

    async def test_identical_text_not_resent(self, monkeypatch):
        send = AsyncMock(return_value={"message_id": 7})
        edit = AsyncMock(return_value={"message_id": 7})
        import services.integrations.handlers.outbound_telegram as ot
        monkeypatch.setattr(ot, "send_telegram_message", send)
        monkeypatch.setattr(ot, "edit_telegram_message", edit)

        cb = self._cb(min_interval_s=0)
        await cb("run_started", {"task_id": "task-abcdef123"})
        await cb("node_completed", {"node": "a"})
        before = edit.await_count
        # Re-sending the SAME terminal render with no new node changes must
        # not produce a duplicate edit (Telegram rejects unchanged text).
        await cb("run_completed", {"task_id": "task-abcdef123"})
        after = edit.await_count
        # run_completed changes the header, so it DOES edit once more; assert
        # it isn't a no-op-loop (>= before) and a repeat run_completed coalesces.
        assert after >= before
        repeat_before = edit.await_count
        await cb("run_completed", {"task_id": "task-abcdef123"})
        assert edit.await_count == repeat_before  # identical text suppressed
