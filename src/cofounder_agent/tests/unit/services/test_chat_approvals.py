"""Tests for services/chat_approvals.py (poindexter#949).

The approval card's safety properties, pinned with a fake pool:

  - one-shot resolve (the atomic UPDATE … WHERE status='pending' race —
    a second click gets ``already_resolved`` and NOTHING executes twice),
  - deny never executes,
  - approve executes the registry handler and stamps row + card part +
    appends the system-message outcome,
  - execution failures surface on the card (executed_ok=False) instead of
    raising out of the resolution,
  - approval_policy: registry default / agent_permissions overrides /
    fail-closed-to-card on an indeterminate check.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import services.chat_agent as chat_agent
import services.chat_approvals as chat_approvals
from services.chat_tools import ChatToolError, ChatToolSpec


class FakePool:
    """Minimal asyncpg-pool stand-in driving chat_approvals' SQL surface."""

    def __init__(self):
        self.approvals: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.system_messages: list[dict[str, Any]] = []
        self.audits: list[tuple[str, dict]] = []
        self._id = 0

    # -- the SQL entry points chat_approvals uses ------------------
    async def fetchrow(self, sql: str, *args: Any):
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO chat_approvals"):
            self._id += 1
            aid = f"appr-{self._id}"
            import datetime as dt

            row = {
                "id": aid, "conversation_id": args[0], "message_id": args[1],
                "tool": args[2], "args": args[3], "summary": args[4],
                "status": "pending",
                "created_at": dt.datetime.now(dt.timezone.utc),
                "resolved_at": None, "executed_ok": None, "result_digest": "",
            }
            self.approvals[aid] = row
            return {"id": aid, "status": "pending", "created_at": row["created_at"]}
        if s.startswith("SELECT * FROM chat_approvals"):
            return self.approvals.get(args[0])
        if s.startswith("UPDATE chat_approvals SET status"):
            row = self.approvals.get(args[0])
            if row is None or row["status"] != "pending":
                return None
            row["status"] = args[1]
            import datetime as dt

            row["resolved_at"] = dt.datetime.now(dt.timezone.utc)
            return {k: row[k] for k in
                    ("id", "conversation_id", "message_id", "tool", "args", "summary")}
        if s.startswith("SELECT parts FROM chat_messages"):
            parts = self.messages.get(args[0])
            return None if parts is None else {"parts": json.dumps(parts)}
        if s.startswith("SELECT allowed, requires_approval FROM agent_permissions"):
            return getattr(self, "permission_row", None)
        raise AssertionError(f"unexpected fetchrow: {s[:80]}")

    async def execute(self, sql: str, *args: Any):
        s = " ".join(sql.split())
        if s.startswith("UPDATE chat_approvals SET executed_ok"):
            row = self.approvals[args[0]]
            row["executed_ok"] = args[1]
            row["result_digest"] = args[2]
            return "UPDATE 1"
        if s.startswith("UPDATE chat_messages SET parts"):
            self.messages[args[0]] = json.loads(args[1])
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {s[:80]}")


@pytest.fixture
def env(monkeypatch):
    pool = FakePool()

    async def add_message(p, conversation_id, *, role, parts=None, **kw):
        pool.system_messages.append(
            {"conversation_id": conversation_id, "role": role, "parts": parts},
        )
        return {"id": "sys-1"}

    async def add_task_link(p, conversation_id, task_id, **kw):
        pass

    monkeypatch.setattr(chat_approvals.store, "add_message", add_message)
    monkeypatch.setattr(chat_approvals.store, "add_task_link", add_task_link)

    async def audit(pool_, **kwargs):
        pool.audits.append(("chat_approval_resolved", kwargs))

    monkeypatch.setattr(chat_approvals, "_audit", audit)
    return pool


def _spec(handler):
    return ChatToolSpec(
        name="set_setting", description="d",
        parameters={"type": "object", "properties": {}, "required": []},
        tier="write", handler=handler, requires_approval=True,
    )


async def _make_pending(pool) -> str:
    approval = await chat_approvals.create_approval(
        pool, conversation_id="conv-1", message_id="msg-1",
        tool="set_setting", args={"key": "k", "value": "v"}, summary="set k=v",
    )
    # The message carries the pending card part (as the loop writes it).
    pool.messages["msg-1"] = [
        {"type": "card", "card": {
            "kind": "approval", "approval_id": approval["id"],
            "tool": "set_setting", "state": "pending",
        }},
    ]
    return approval["id"]


@pytest.mark.unit
class TestResolve:
    def test_approve_executes_and_stamps_everything(self, env, monkeypatch):
        calls = []

        async def handler(ctx, **kwargs):
            calls.append(kwargs)
            return "k set to 'v'."

        monkeypatch.setattr(chat_approvals, "get_tool", lambda n: _spec(handler))
        aid = asyncio.run(_make_pending(env))
        out = asyncio.run(chat_approvals.resolve_approval(
            pool=env, db_service=object(), site_config=object(),
            approval_id=aid, approve=True,
        ))
        assert calls == [{"key": "k", "value": "v"}]
        assert out["status"] == "approved"
        assert out["executed_ok"] is True
        assert "k set" in out["result_digest"]
        card = env.messages["msg-1"][0]["card"]
        assert card["state"] == "approved" and card["executed_ok"] is True
        assert env.system_messages[0]["role"] == "system"
        assert "Approved: set_setting — ok" in env.system_messages[0]["parts"][0]["text"]
        assert env.audits[0][1]["approved"] is True

    def test_deny_never_executes(self, env, monkeypatch):
        async def handler(ctx, **kwargs):  # pragma: no cover — must not run
            raise AssertionError("deny must not execute the tool")

        monkeypatch.setattr(chat_approvals, "get_tool", lambda n: _spec(handler))
        aid = asyncio.run(_make_pending(env))
        out = asyncio.run(chat_approvals.resolve_approval(
            pool=env, db_service=object(), site_config=object(),
            approval_id=aid, approve=False,
        ))
        assert out["status"] == "denied"
        assert out["executed_ok"] is None
        assert env.messages["msg-1"][0]["card"]["state"] == "denied"
        assert "Denied" in env.system_messages[0]["parts"][0]["text"]

    def test_one_shot_second_resolve_is_noop(self, env, monkeypatch):
        runs = []

        async def handler(ctx, **kwargs):
            runs.append(1)
            return "ok"

        monkeypatch.setattr(chat_approvals, "get_tool", lambda n: _spec(handler))
        aid = asyncio.run(_make_pending(env))
        asyncio.run(chat_approvals.resolve_approval(
            pool=env, db_service=object(), site_config=object(),
            approval_id=aid, approve=True,
        ))
        second = asyncio.run(chat_approvals.resolve_approval(
            pool=env, db_service=object(), site_config=object(),
            approval_id=aid, approve=True,
        ))
        assert len(runs) == 1, "second click must not re-execute"
        assert second.get("already_resolved") is True

    def test_unknown_approval_raises_keyerror(self, env):
        with pytest.raises(KeyError):
            asyncio.run(chat_approvals.resolve_approval(
                pool=env, db_service=object(), site_config=object(),
                approval_id="nope", approve=True,
            ))

    def test_execution_failure_lands_on_card_not_raise(self, env, monkeypatch):
        async def handler(ctx, **kwargs):
            raise ChatToolError("Setting 'k' is a secret")

        monkeypatch.setattr(chat_approvals, "get_tool", lambda n: _spec(handler))
        aid = asyncio.run(_make_pending(env))
        out = asyncio.run(chat_approvals.resolve_approval(
            pool=env, db_service=object(), site_config=object(),
            approval_id=aid, approve=True,
        ))
        assert out["status"] == "approved"
        assert out["executed_ok"] is False
        assert "secret" in out["result_digest"]
        assert "FAILED" in env.system_messages[0]["parts"][0]["text"]

    def test_vanished_tool_fails_gracefully(self, env, monkeypatch):
        monkeypatch.setattr(chat_approvals, "get_tool", lambda n: None)
        aid = asyncio.run(_make_pending(env))
        out = asyncio.run(chat_approvals.resolve_approval(
            pool=env, db_service=object(), site_config=object(),
            approval_id=aid, approve=True,
        ))
        assert out["executed_ok"] is False
        assert "no longer exists" in out["result_digest"]


@pytest.mark.unit
class TestApprovalPolicy:
    def _policy(self, pool, default_card=True):
        return asyncio.run(
            chat_approvals.approval_policy(pool, "set_setting", default_card=default_card)
        )

    def test_no_row_uses_registry_default(self, env):
        env.permission_row = None
        assert self._policy(env) == "card"
        assert self._policy(env, default_card=False) == "inline"

    def test_row_forbids(self, env):
        env.permission_row = {"allowed": False, "requires_approval": False}
        assert self._policy(env) == "forbid"

    def test_row_relaxes_to_inline(self, env):
        env.permission_row = {"allowed": True, "requires_approval": False}
        assert self._policy(env) == "inline"

    def test_row_keeps_card(self, env):
        env.permission_row = {"allowed": True, "requires_approval": True}
        assert self._policy(env) == "card"

    def test_indeterminate_check_fails_closed_to_card(self, monkeypatch):
        class BrokenPool:
            async def fetchrow(self, *a):
                raise RuntimeError("db down")

        assert asyncio.run(
            chat_approvals.approval_policy(
                BrokenPool(), "set_setting", default_card=False,
            )
        ) == "card"


@pytest.mark.unit
class TestAgentGating:
    """The loop queues gated write tools instead of executing them."""

    def test_gated_tool_becomes_card_and_turn_completes(self, monkeypatch):
        from tests.unit.services.test_chat_agent import (
            FakeStore,
            _completion,
            _run,
            _tool_call,
        )

        executed = []

        async def handler(ctx, **kwargs):  # pragma: no cover — must not run
            executed.append(kwargs)
            return "ran"

        spec = ChatToolSpec(
            name="set_setting", description="d",
            parameters={"type": "object", "properties": {}, "required": []},
            tier="write", handler=handler, requires_approval=True,
        )

        async def policy(pool, name, *, default_card):
            return "card" if default_card else "inline"

        created = []

        async def create_approval(pool, **kwargs):
            created.append(kwargs)
            return {"id": "appr-9", "status": "pending", "created_at": "now"}

        monkeypatch.setattr(chat_approvals, "approval_policy", policy)
        monkeypatch.setattr(chat_approvals, "create_approval", create_approval)

        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, tool_specs={"set_setting": spec},
            completions=[
                _completion(tool_calls=[
                    _tool_call("set_setting", '{"key": "k", "value": "v"}'),
                ]),
                _completion(text="Queued for your approval."),
            ],
        )
        assert executed == [], "gated tool must not run inline"
        assert created and created[0]["tool"] == "set_setting"
        kinds = [e["event"] for e in events]
        assert "approval_required" in kinds
        assert events[-1]["turn_status"] == "complete"
        card_parts = [
            p for p in fs.finalized["parts"]
            if p.get("type") == "card"
            and (p.get("card") or {}).get("kind") == "approval"
        ]
        assert card_parts and card_parts[0]["card"]["state"] == "pending"

    def test_forbidden_tool_refused_loud(self, monkeypatch):
        from tests.unit.services.test_chat_agent import (
            FakeStore,
            _completion,
            _run,
            _tool_call,
        )

        spec = ChatToolSpec(
            name="set_setting", description="d",
            parameters={"type": "object", "properties": {}, "required": []},
            tier="write",
            handler=None,  # type: ignore[arg-type] — must never be called
            requires_approval=True,
        )

        async def policy(pool, name, *, default_card):
            return "forbid"

        monkeypatch.setattr(chat_approvals, "approval_policy", policy)
        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, tool_specs={"set_setting": spec},
            completions=[
                _completion(tool_calls=[_tool_call("set_setting", "{}")]),
                _completion(text="I can't do that."),
            ],
        )
        results = [e for e in events if e["event"] == "tool_result"]
        assert results[0]["ok"] is False
        assert "forbidden" in results[0]["digest"]
        assert events[-1]["turn_status"] == "complete"


# The agent-side import of chat_agent keeps this file honest about the
# integration seam it exercises.
_ = chat_agent
