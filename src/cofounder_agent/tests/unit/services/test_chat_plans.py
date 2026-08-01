"""Tests for services/chat_plans.py + the plan_pipeline tool (poindexter#950).

Pins the safety properties of the architect's chat entry point:

  - the slug NAMESPACE guard (an LLM naming its spec 'canonical blog'
    must never overwrite the production template — cache_template upserts
    by slug),
  - one-shot Run (draft→ran atomically; the created task carries the
    cached template_slug; conversation gets the link + system message),
  - compose failures surface as ChatToolError with the FIX errors,
  - the loop drains ctx.emitted_cards into parts + a ``card`` event.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import Any

import pytest

import services.chat_plans as chat_plans
import services.pipeline_architect as pipeline_architect
from services.chat_plans import namespace_spec
from services.chat_tools import ChatToolContext, ChatToolError, get_tool


@pytest.mark.unit
class TestNamespaceSpec:
    def test_prefixes_dangerous_names(self):
        spec = {"name": "canonical blog", "nodes": []}
        assert namespace_spec(spec)["name"] == "plan_canonical blog"

    def test_already_prefixed_untouched(self):
        assert namespace_spec({"name": "plan_x"})["name"] == "plan_x"

    def test_empty_name_gets_default(self):
        assert namespace_spec({})["name"] == "plan_architect plan"

    def test_original_spec_not_mutated(self):
        spec = {"name": "foo"}
        namespace_spec(spec)
        assert spec["name"] == "foo"


class FakePlanPool:
    def __init__(self):
        self.plans: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list] = {}
        self.executes: list[tuple] = []
        self._n = 0

    async def fetchrow(self, sql: str, *args: Any):
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO chat_plans"):
            self._n += 1
            pid = f"plan-{self._n}"
            self.plans[pid] = {
                "id": pid, "conversation_id": args[0], "message_id": args[1],
                "intent": args[2], "topic": args[3], "template_slug": args[4],
                "spec": args[5], "status": "draft", "task_id": None,
                "created_at": dt.datetime.now(dt.timezone.utc),
                "resolved_at": None,
            }
            return {"id": pid}
        if s.startswith("SELECT * FROM chat_plans"):
            return self.plans.get(args[0])
        if s.startswith("UPDATE chat_plans SET status"):
            row = self.plans.get(args[0])
            if row is None or row["status"] != "draft":
                return None
            row["status"] = "ran"
            row["resolved_at"] = dt.datetime.now(dt.timezone.utc)
            return {k: row[k] for k in
                    ("id", "conversation_id", "message_id", "intent",
                     "topic", "template_slug")}
        if s.startswith("SELECT parts FROM chat_messages"):
            parts = self.messages.get(args[0])
            return None if parts is None else {"parts": json.dumps(parts)}
        raise AssertionError(f"unexpected fetchrow: {s[:70]}")

    async def execute(self, sql: str, *args: Any):
        s = " ".join(sql.split())
        if s.startswith("UPDATE chat_plans SET task_id"):
            self.plans[args[0]]["task_id"] = args[1]
            return "UPDATE 1"
        if s.startswith("UPDATE chat_messages SET parts"):
            self.messages[args[0]] = json.loads(args[1])
            return "UPDATE 1"
        self.executes.append((s, args))
        return "UPDATE 1"


@pytest.fixture
def plan_env(monkeypatch):
    pool = FakePlanPool()
    cached = {}

    async def cache_template(p, spec):
        cached["spec"] = spec
        import re
        return re.sub(r"[^a-z0-9_]+", "_", spec["name"].lower()).strip("_")

    monkeypatch.setattr(pipeline_architect, "cache_template", cache_template)

    system_messages = []
    links = []

    async def add_message(p, conversation_id, *, role, parts=None, **kw):
        system_messages.append({"conversation_id": conversation_id,
                                "role": role, "parts": parts})
        return {"id": "sys"}

    async def add_task_link(p, conversation_id, task_id, **kw):
        links.append((conversation_id, task_id))

    monkeypatch.setattr(chat_plans.store, "add_message", add_message)
    monkeypatch.setattr(chat_plans.store, "add_task_link", add_task_link)

    async def audit(p, **kw):
        pass

    monkeypatch.setattr(chat_plans, "_audit", audit)
    return pool, cached, system_messages, links


def _spec(name="focused factcheck"):
    return {
        "name": name,
        "nodes": [
            {"id": "verify_task", "atom": "stage.verify_task"},
            {"id": "draft", "atom": "content.generate_draft"},
            {"id": "qa1", "atom": "qa.web_factcheck"},
        ],
    }


@pytest.mark.unit
class TestCreatePlan:
    def test_caches_namespaced_and_returns_card_fields(self, plan_env):
        pool, cached, _, _ = plan_env
        out = asyncio.run(chat_plans.create_plan(
            pool, conversation_id="c1", message_id="m1",
            intent="write about X, skip video", topic="X",
            spec=_spec("canonical blog"),
        ))
        assert cached["spec"]["name"] == "plan_canonical blog"
        assert out["slug"].startswith("plan_")
        assert out["node_count"] == 3
        assert out["nodes"][0] == "verify_task"

    def test_escaped_namespace_refused(self, plan_env, monkeypatch):
        async def bad_cache(p, spec):
            return "canonical_blog"  # guard breach simulation

        monkeypatch.setattr(pipeline_architect, "cache_template", bad_cache)
        with pytest.raises(RuntimeError, match="namespace"):
            asyncio.run(chat_plans.create_plan(
                FakePlanPool(), conversation_id="c", message_id="m",
                intent="i", topic="t", spec=_spec(),
            ))


class FakeDb:
    def __init__(self):
        self.added = None

    async def add_task(self, task_data):
        self.added = task_data
        return task_data["id"]


@pytest.mark.unit
class TestRunPlan:
    def _mk(self, plan_env):
        pool, _, msgs, links = plan_env
        plan = asyncio.run(chat_plans.create_plan(
            pool, conversation_id="c1", message_id="m1",
            intent="write about X, skip video", topic="X", spec=_spec(),
        ))
        pool.messages["m1"] = [{
            "type": "card",
            "card": {"kind": "plan", "plan_id": plan["plan_id"],
                     "state": "draft"},
        }]
        return pool, plan, msgs, links

    def test_run_creates_task_on_cached_slug(self, plan_env):
        pool, plan, msgs, links = self._mk(plan_env)
        db = FakeDb()
        out = asyncio.run(chat_plans.run_plan(
            pool=pool, db_service=db, plan_id=plan["plan_id"],
        ))
        assert out["status"] == "ran"
        assert db.added["template_slug"] == plan["slug"]
        assert db.added["topic"] == "X"
        assert db.added["metadata"]["created_via"] == "chat_plan"
        assert links and links[0][1] == db.added["id"]
        card = pool.messages["m1"][0]["card"]
        assert card["state"] == "ran" and card["task_id"] == db.added["id"]
        assert msgs and "Plan run started" in msgs[0]["parts"][0]["text"]

    def test_topic_override_wins(self, plan_env):
        pool, plan, _, _ = self._mk(plan_env)
        db = FakeDb()
        asyncio.run(chat_plans.run_plan(
            pool=pool, db_service=db, plan_id=plan["plan_id"],
            topic_override="Better topic",
        ))
        assert db.added["topic"] == "Better topic"

    def test_one_shot(self, plan_env):
        pool, plan, _, _ = self._mk(plan_env)
        db = FakeDb()
        asyncio.run(chat_plans.run_plan(
            pool=pool, db_service=db, plan_id=plan["plan_id"],
        ))
        first_task = db.added["id"]
        again = asyncio.run(chat_plans.run_plan(
            pool=pool, db_service=FakeDb(), plan_id=plan["plan_id"],
        ))
        assert again.get("already_resolved") is True
        assert again["task_id"] == first_task

    def test_unknown_plan_raises(self, plan_env):
        pool, *_ = plan_env
        with pytest.raises(KeyError):
            asyncio.run(chat_plans.run_plan(
                pool=pool, db_service=FakeDb(), plan_id="nope",
            ))


@pytest.mark.unit
class TestPlanPipelineTool:
    def _ctx(self, pool):
        return ChatToolContext(
            db_service=object(), site_config=object(), pool=pool,
            conversation_id="c1", message_id="m1",
        )

    def test_compose_ok_emits_card(self, plan_env, monkeypatch):
        pool, *_ = plan_env

        async def compose(intent, *, site_config, pool, max_attempts):
            assert max_attempts == 2
            from services.pipeline_architect import ArchitectResult
            return ArchitectResult(ok=True, spec=_spec())

        monkeypatch.setattr(pipeline_architect, "compose", compose)
        ctx = self._ctx(pool)
        out = asyncio.run(
            get_tool("plan_pipeline").handler(
                ctx, intent="write about X, skip video", topic="X",
            )
        )
        assert "Plan ready: 3 steps" in out
        assert len(ctx.emitted_cards) == 1
        card = ctx.emitted_cards[0]
        assert card["kind"] == "plan" and card["state"] == "draft"
        assert card["slug"].startswith("plan_")

    def test_compose_failure_surfaces_fix_errors(self, plan_env, monkeypatch):
        pool, *_ = plan_env

        async def compose(intent, *, site_config, pool, max_attempts):
            from services.pipeline_architect import ArchitectResult
            return ArchitectResult(
                ok=False, errors=["FIX: node 'qa.bogus' names an unknown atom"],
            )

        monkeypatch.setattr(pipeline_architect, "compose", compose)
        with pytest.raises(ChatToolError, match="qa.bogus"):
            asyncio.run(
                get_tool("plan_pipeline").handler(
                    self._ctx(pool), intent="impossible thing",
                )
            )


@pytest.mark.unit
class TestLoopCardDrain:
    def test_emitted_cards_become_parts_and_events(self, monkeypatch):
        from services.chat_tools import ChatToolSpec
        from tests.unit.services.test_chat_agent import (
            FakeStore,
            _completion,
            _run,
            _tool_call,
        )

        async def handler(ctx, **kwargs):
            ctx.emitted_cards.append({"kind": "plan", "plan_id": "p1",
                                      "state": "draft"})
            return "Plan ready."

        spec = ChatToolSpec(
            name="plan_pipeline", description="d",
            parameters={"type": "object", "properties": {}, "required": []},
            tier="read", handler=handler,
        )
        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, tool_specs={"plan_pipeline": spec},
            completions=[
                _completion(tool_calls=[_tool_call("plan_pipeline", "{}")]),
                _completion(text="Here is your plan."),
            ],
        )
        card_events = [e for e in events if e["event"] == "card"]
        assert card_events and card_events[0]["card"]["plan_id"] == "p1"
        card_parts = [
            p for p in fs.finalized["parts"]
            if p.get("type") == "card"
            and (p.get("card") or {}).get("kind") == "plan"
        ]
        assert card_parts and card_parts[0]["card"]["state"] == "draft"
