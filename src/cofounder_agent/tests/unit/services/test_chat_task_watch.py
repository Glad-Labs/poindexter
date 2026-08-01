"""Tests for the watch read (services/chat_watch.py) and the completion
watcher job (services/jobs/chat_task_watch.py) — poindexter#949."""

from __future__ import annotations

import asyncio

import pytest

import services.jobs.chat_task_watch as watch_job_module
from services.chat_watch import TERMINAL_STATUSES, watch_task
from services.jobs.chat_task_watch import ChatTaskWatchJob


class FakeWatchPool:
    def __init__(self, *, task=None, expected=31, nodes=None):
        self.task = task
        self.expected = expected
        self.nodes = nodes or []

    async def fetchrow(self, sql, *args):
        return self.task

    async def fetchval(self, sql, *args):
        return self.expected

    async def fetch(self, sql, *args):
        return self.nodes


@pytest.mark.unit
class TestWatchTask:
    def test_unknown_task_is_none(self):
        assert asyncio.run(watch_task(FakeWatchPool(task=None), "x")) is None

    def test_snapshot_shape(self):
        import datetime as dt

        task = {
            "task_id": "t-1", "status": "in_progress", "topic": "T",
            "template_slug": "canonical_blog", "quality_score": None,
            "updated_at": dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc),
        }
        nodes = [
            {"node_id": f"n{i}", "atom": f"a{i}", "status": "success",
             "duration_ms": 10, "started_at": None}
            for i in range(12)
        ]
        snap = asyncio.run(watch_task(FakeWatchPool(task=task, nodes=nodes), "t-1"))
        assert snap["terminal"] is False
        assert snap["expected_nodes"] == 31
        assert snap["nodes_done"] == 12
        assert len(snap["nodes"]) == 10, "spine is capped at the last 10"

    def test_terminal_statuses_flag(self):
        for status in ("awaiting_approval", "failed", "cancelled", "published"):
            assert status in TERMINAL_STATUSES
            task = {
                "task_id": "t", "status": status, "topic": "T",
                "template_slug": None, "quality_score": 87.0, "updated_at": None,
            }
            snap = asyncio.run(watch_task(FakeWatchPool(task=task), "t"))
            assert snap["terminal"] is True
            assert snap["quality_score"] == 87.0
        assert "in_progress" not in TERMINAL_STATUSES


class FakeJobPool:
    def __init__(self, rows):
        self.rows = rows
        self.stamps = []

    async def fetch(self, sql, *args):
        return self.rows

    async def execute(self, sql, *args):
        self.stamps.append(args)
        return "UPDATE 1"


class FakeSiteConfig:
    def __init__(self, **overrides):
        self.values = {
            "console_chat_enabled": "true",
            "console_chat_watch_notify": "discord",
            "console_public_url": "http://100.1.2.3:8002",
        }
        self.values.update({k: str(v) for k, v in overrides.items()})

    def get(self, key, default=None):
        return self.values.get(key, default)


def _row(**overrides):
    base = {
        "conversation_id": "conv-1", "pipeline_task_id": "t-1",
        "task_id": "t-1", "status": "awaiting_approval",
        "topic": "Own your numbers", "quality_score": 87.0,
    }
    base.update(overrides)
    return base


@pytest.fixture
def job_env(monkeypatch):
    messages = []
    pings = []

    async def add_message(pool, conversation_id, *, role, parts=None, **kw):
        messages.append({"conversation_id": conversation_id, "role": role,
                         "parts": parts})
        return {"id": "m"}

    import services.chat_conversation_store as store_module
    monkeypatch.setattr(store_module, "add_message", add_message)

    async def notify_operator(message, *, critical=False, site_config=None):
        pings.append({"message": message, "critical": critical})

    import services.integrations.operator_notify as notify_module
    monkeypatch.setattr(notify_module, "notify_operator", notify_operator)
    return messages, pings


@pytest.mark.unit
class TestChatTaskWatchJob:
    def _run(self, pool, site_config=None):
        return asyncio.run(ChatTaskWatchJob().run(
            pool, {"_site_config": site_config or FakeSiteConfig()},
        ))

    def test_disabled_is_noop(self):
        out = self._run(FakeJobPool([]), FakeSiteConfig(console_chat_enabled="false"))
        assert out.ok and "no-op" in out.detail

    def test_draft_ready_appends_card_and_pings_discord(self, job_env):
        messages, pings = job_env
        pool = FakeJobPool([_row()])
        out = self._run(pool)
        assert out.ok and out.metrics["notified"] == 1
        assert messages[0]["role"] == "system"
        card = messages[0]["parts"][0]["card"]
        assert card["kind"] == "task_result"
        assert card["status"] == "awaiting_approval"
        assert card["quality_score"] == 87.0
        assert "awaits your approval" in messages[0]["parts"][1]["text"]
        # Stamp before ping; discord = critical False; deeplink present.
        assert pool.stamps and pool.stamps[0] == ("conv-1", "t-1")
        assert pings[0]["critical"] is False
        assert "#trace/t-1" in pings[0]["message"]

    def test_telegram_channel_is_critical(self, job_env):
        _, pings = job_env
        self._run(
            FakeJobPool([_row()]),
            FakeSiteConfig(console_chat_watch_notify="telegram"),
        )
        assert pings[0]["critical"] is True

    def test_none_channel_skips_ping_but_still_appends(self, job_env):
        messages, pings = job_env
        self._run(
            FakeJobPool([_row()]),
            FakeSiteConfig(console_chat_watch_notify="none"),
        )
        assert messages and not pings

    def test_failed_run_reports_honestly(self, job_env):
        messages, _ = job_env
        self._run(FakeJobPool([_row(status="failed", quality_score=None)]))
        assert "ended failed" in messages[0]["parts"][1]["text"]

    def test_one_bad_row_does_not_stall_the_sweep(self, job_env, monkeypatch):
        messages, _ = job_env
        calls = {"n": 0}
        original = ChatTaskWatchJob._notify_one

        async def flaky(self, pool, site_config, row):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return await original(self, pool, site_config, row)

        monkeypatch.setattr(ChatTaskWatchJob, "_notify_one", flaky)
        out = self._run(FakeJobPool([_row(), _row(pipeline_task_id="t-2",
                                                  task_id="t-2")]))
        assert out.ok
        assert out.metrics == {"notified": 1, "swept": 2}
        assert len(messages) == 1


_ = watch_job_module
