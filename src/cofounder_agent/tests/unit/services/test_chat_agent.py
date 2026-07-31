"""Turn-loop tests for the Cofounder chat agent (services/chat_agent.py).

Fake LLM + fake store; pins the loop guards and the persisted lifecycle
(poindexter#947):

  - happy paths (text-only; tool call then answer),
  - repeat-call detection (2nd identical call → corrective, 3rd → abort),
  - unknown-tool + bad-JSON-args corrective replies,
  - max-tool-call + round caps fail loud,
  - provider-without-tools and exhausted daily budget fail loud,
  - deadline → ``interrupted``; crash → ``failed`` — both finalized,
  - the assistant row is ALWAYS finalized with the terminal status.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import services.chat_agent as chat_agent
from plugins.llm_provider import Completion
from services.chat_tools import ChatToolError, ChatToolSpec


class FakeStore:
    """In-memory stand-in for chat_conversation_store."""

    def __init__(self):
        self.messages: list[dict] = []
        self.finalized: dict | None = None
        self.task_links: list[str] = []
        self.titles: list[str] = []
        self.tokens_today = 0
        self._next_id = 0

    def install(self, monkeypatch):
        async def add_message(pool, conversation_id, *, role, parts=None,
                              turn_status="complete", model=""):
            self._next_id += 1
            row = {"id": f"m{self._next_id}", "role": role,
                   "parts": parts or [], "turn_status": turn_status}
            self.messages.append(row)
            return row

        async def finalize_message(pool, message_id, *, parts, turn_status,
                                   model="", prompt_tokens=0,
                                   completion_tokens=0, cost_usd=0.0):
            self.finalized = {
                "message_id": message_id, "parts": parts,
                "turn_status": turn_status, "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens, "cost_usd": cost_usd,
            }

        async def list_messages(pool, conversation_id, *, limit=200):
            return list(self.messages)

        async def set_title_if_empty(pool, conversation_id, title):
            self.titles.append(title)

        async def add_task_link(pool, conversation_id, task_id, *, purpose="created"):
            self.task_links.append(task_id)

        async def tokens_used_today(pool, *, user_id="operator"):
            return self.tokens_today

        for name, fn in [
            ("add_message", add_message), ("finalize_message", finalize_message),
            ("list_messages", list_messages),
            ("set_title_if_empty", set_title_if_empty),
            ("add_task_link", add_task_link),
            ("tokens_used_today", tokens_used_today),
        ]:
            monkeypatch.setattr(chat_agent.store, name, fn)


class FakeSiteConfig:
    def __init__(self, **overrides):
        self.values = {
            "console_chat_turn_timeout_s": "30",
            "console_chat_max_tool_calls": "8",
            "console_chat_tool_result_max_chars": "2000",
            "console_chat_context_recent_turns": "12",
            "console_chat_daily_token_budget": "200000",
            "console_chat_model": "fake-model",
            "agent_persona_name": "Testdexter",
        }
        self.values.update({k: str(v) for k, v in overrides.items()})

    def get(self, key, default=None):
        return self.values.get(key, default)


def _completion(text="", tool_calls=None, prompt_tokens=10, completion_tokens=5):
    return Completion(
        text=text, model="fake-model", prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, tool_calls=tool_calls,
    )


def _tool_call(name, arguments, call_id="c1"):
    return {"id": call_id, "name": name, "arguments": arguments}


def _run(monkeypatch, fake_store, *, completions=None, dispatch=None,
         supports_tools=True, site_config=None, tool_specs=None):
    """Drive run_turn with fakes; return the collected event list."""
    fake_store.install(monkeypatch)

    if dispatch is None:
        queue = list(completions or [])

        async def dispatch(pool, messages, model, tools):
            return queue.pop(0)

    monkeypatch.setattr(chat_agent, "_dispatch", dispatch)

    class Provider:
        name = "fake"

    Provider.supports_tools = supports_tools

    async def resolve(pool, tier):
        return Provider(), supports_tools

    monkeypatch.setattr(chat_agent, "_resolve_provider_supports_tools", resolve)

    audits = []

    async def audit(pool, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(chat_agent, "_audit_tool_call", audit)

    if tool_specs is not None:
        monkeypatch.setattr(
            chat_agent, "get_tool", lambda name: tool_specs.get(name),
        )

    async def collect():
        events = []
        async for event in chat_agent.run_turn(
            pool=object(), db_service=object(),
            site_config=site_config or FakeSiteConfig(),
            conversation={"id": "conv1"}, user_text="hello there",
        ):
            events.append(event)
        return events

    events = asyncio.run(collect())
    return events, audits


def _events_of(events, kind):
    return [e for e in events if e["event"] == kind]


def _spec(name, handler, tier="read"):
    return ChatToolSpec(
        name=name, description="d",
        parameters={"type": "object", "properties": {}, "required": []},
        tier=tier, handler=handler,
    )


@pytest.mark.unit
class TestHappyPaths:
    def test_text_only_turn(self, monkeypatch):
        fs = FakeStore()
        events, _ = _run(monkeypatch, fs, completions=[_completion(text="Hi Matt")])
        kinds = [e["event"] for e in events]
        assert kinds == ["turn_started", "text", "done"]
        assert events[1]["text"] == "Hi Matt"
        assert events[-1]["turn_status"] == "complete"
        assert fs.finalized["turn_status"] == "complete"
        assert fs.finalized["prompt_tokens"] == 10
        assert fs.titles == ["hello there"]

    def test_tool_then_answer(self, monkeypatch):
        calls = {}

        async def handler(ctx, **kwargs):
            calls["kwargs"] = kwargs
            ctx.linked_task_ids.append("t-9")
            return "tool says 41 tasks"

        specs = {"list_tasks": _spec("list_tasks", handler)}
        fs = FakeStore()
        events, audits = _run(
            monkeypatch, fs, tool_specs=specs,
            completions=[
                _completion(tool_calls=[_tool_call("list_tasks", '{"limit": 3}')]),
                _completion(text="There are 41 tasks."),
            ],
        )
        kinds = [e["event"] for e in events]
        assert kinds == [
            "turn_started", "tool_start", "tool_result", "task_linked",
            "text", "done",
        ]
        assert calls["kwargs"] == {"limit": 3}
        assert _events_of(events, "tool_result")[0]["ok"] is True
        assert fs.task_links == ["t-9"]
        assert audits and audits[0]["tool"] == "list_tasks" and audits[0]["ok"]
        parts = fs.finalized["parts"]
        assert [p["type"] for p in parts] == ["tool_call", "card", "markdown"]

    def test_empty_reply_is_explicit(self, monkeypatch):
        fs = FakeStore()
        events, _ = _run(monkeypatch, fs, completions=[_completion(text="  ")])
        assert "empty reply" in _events_of(events, "text")[0]["text"]


@pytest.mark.unit
class TestLoopGuards:
    def test_repeat_call_corrected_then_aborted(self, monkeypatch):
        async def handler(ctx, **kwargs):
            return "result"

        specs = {"list_tasks": _spec("list_tasks", handler)}
        same = [_tool_call("list_tasks", "{}")]
        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, tool_specs=specs,
            completions=[
                _completion(tool_calls=same),
                _completion(tool_calls=same),  # 2nd → corrective reply
                _completion(tool_calls=same),  # 3rd → abort
            ],
        )
        results = _events_of(events, "tool_result")
        assert results[0]["ok"] is True
        assert results[1]["ok"] is False
        assert "already called" in results[1]["digest"]
        errors = _events_of(events, "error")
        assert errors and errors[0]["reason"] == "repeat_tool_call"
        assert events[-1]["turn_status"] == "failed"
        assert fs.finalized["turn_status"] == "failed"

    def test_unknown_tool_gets_corrective_reply(self, monkeypatch):
        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, tool_specs={},
            completions=[
                _completion(tool_calls=[_tool_call("bogus", "{}")]),
                _completion(text="ok, no such tool"),
            ],
        )
        result = _events_of(events, "tool_result")[0]
        assert result["ok"] is False and "Unknown tool" in result["digest"]
        assert events[-1]["turn_status"] == "complete"

    def test_bad_json_args_corrected(self, monkeypatch):
        async def handler(ctx, **kwargs):  # pragma: no cover — must not run
            raise AssertionError("handler must not execute on bad args")

        specs = {"list_tasks": _spec("list_tasks", handler)}
        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, tool_specs=specs,
            completions=[
                _completion(tool_calls=[_tool_call("list_tasks", "{not json")]),
                _completion(text="done"),
            ],
        )
        result = _events_of(events, "tool_result")[0]
        assert result["ok"] is False and "Could not parse" in result["digest"]

    def test_tool_error_is_repair_signal_not_crash(self, monkeypatch):
        async def handler(ctx, **kwargs):
            raise ChatToolError("No task found for id 'x'.")

        specs = {"get_task": _spec("get_task", handler)}
        fs = FakeStore()
        events, audits = _run(
            monkeypatch, fs, tool_specs=specs,
            completions=[
                _completion(tool_calls=[_tool_call("get_task", "{}")]),
                _completion(text="that task does not exist"),
            ],
        )
        result = _events_of(events, "tool_result")[0]
        assert result["ok"] is False and "No task found" in result["digest"]
        assert audits[0]["ok"] is False and "No task found" in audits[0]["error"]
        assert events[-1]["turn_status"] == "complete"

    def test_max_tool_calls_cap(self, monkeypatch):
        async def handler(ctx, **kwargs):
            return "r"

        specs = {"a": _spec("a", handler), "b": _spec("b", handler)}
        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, tool_specs=specs,
            site_config=FakeSiteConfig(console_chat_max_tool_calls=1),
            completions=[
                _completion(tool_calls=[
                    _tool_call("a", "{}", "c1"), _tool_call("b", "{}", "c2"),
                ]),
                _completion(text="stopping"),
            ],
        )
        results = _events_of(events, "tool_result")
        assert results[0]["ok"] is True
        assert results[1]["ok"] is False and "limit" in results[1]["digest"]

    def test_round_cap_fails_loud(self, monkeypatch):
        async def handler(ctx, **kwargs):
            return "r"

        specs = {"a": _spec("a", handler)}
        n = [0]

        def make_call():
            n[0] += 1
            return _completion(tool_calls=[_tool_call("a", f'{{"i": {n[0]}}}')])

        async def dispatch(pool, messages, model, tools):
            return make_call()

        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, tool_specs=specs, dispatch=dispatch,
            site_config=FakeSiteConfig(console_chat_max_tool_calls=2),
        )
        errors = _events_of(events, "error")
        assert errors and errors[-1]["reason"] == "round_limit"
        assert events[-1]["turn_status"] == "failed"


@pytest.mark.unit
class TestPreflightGates:
    def test_provider_without_tools_fails_loud(self, monkeypatch):
        fs = FakeStore()
        events, _ = _run(monkeypatch, fs, completions=[], supports_tools=False)
        errors = _events_of(events, "error")
        assert errors[0]["reason"] == "provider_no_tools"
        assert "litellm" in errors[0]["detail"]
        assert events[-1]["turn_status"] == "failed"
        assert fs.finalized["turn_status"] == "failed"

    def test_daily_budget_exhausted(self, monkeypatch):
        fs = FakeStore()
        fs.tokens_today = 500
        events, _ = _run(
            monkeypatch, fs, completions=[],
            site_config=FakeSiteConfig(console_chat_daily_token_budget=100),
        )
        errors = _events_of(events, "error")
        assert errors[0]["reason"] == "daily_budget_exhausted"
        assert events[-1]["turn_status"] == "failed"


@pytest.mark.unit
class TestLifecycle:
    def test_deadline_interrupts(self, monkeypatch):
        async def slow_dispatch(pool, messages, model, tools):
            await asyncio.sleep(5)

        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, dispatch=slow_dispatch,
            site_config=FakeSiteConfig(console_chat_turn_timeout_s=0),
        )
        errors = _events_of(events, "error")
        assert errors[0]["reason"] == "turn_timeout"
        assert events[-1]["turn_status"] == "interrupted"
        assert fs.finalized["turn_status"] == "interrupted"

    def test_dispatch_crash_fails_turn(self, monkeypatch):
        async def broken(pool, messages, model, tools):
            raise RuntimeError("ollama exploded")

        fs = FakeStore()
        events, _ = _run(monkeypatch, fs, dispatch=broken)
        errors = _events_of(events, "error")
        assert errors[0]["reason"] == "turn_crashed"
        assert "ollama exploded" in errors[0]["detail"]
        assert fs.finalized["turn_status"] == "failed"

    def test_tool_result_digested(self, monkeypatch):
        async def handler(ctx, **kwargs):
            return "x" * 5000

        specs = {"a": _spec("a", handler)}
        fs = FakeStore()
        events, _ = _run(
            monkeypatch, fs, tool_specs=specs,
            site_config=FakeSiteConfig(console_chat_tool_result_max_chars=100),
            completions=[
                _completion(tool_calls=[_tool_call("a", "{}")]),
                _completion(text="done"),
            ],
        )
        digest = _events_of(events, "tool_result")[0]["digest"]
        assert len(digest) <= 130 and "truncated" in digest

    def test_events_are_json_serializable(self, monkeypatch):
        fs = FakeStore()
        events, _ = _run(monkeypatch, fs, completions=[_completion(text="ok")])
        for event in events:
            json.dumps(event)
