"""Unit tests for ``services/approval_service.py`` (#145).

Mocks the asyncpg pool with a tiny in-memory store so the test suite
runs without a live Postgres. Every external surface (audit_log,
notify path) is patched so we don't depend on Telegram / Discord
config either.

Coverage:

- ``is_gate_enabled`` reads the right setting key with default-off.
- ``pause_at_gate`` writes the gate columns and audit row.
- ``approve`` clears the gate + inserts a pipeline_gate_history row.
- ``approve`` raises TaskNotFoundError when the task is missing.
- ``approve`` raises GateMismatchError on wrong --gate.
- ``approve`` raises TaskNotPausedError when no gate is active.
- ``reject`` flips status to ``rejected`` (default) or a custom status.
- ``list_pending`` returns parsed artifacts ordered oldest-first.
- ``show_pending`` returns the single-row detail.
- ``set_gate_enabled`` / ``list_gates`` round-trip through the fake DB.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from services.approval_service import (
    GateMismatchError,
    TaskNotFoundError,
    TaskNotPausedError,
    approve,
    is_gate_enabled,
    list_gates,
    list_pending,
    pause_at_gate,
    reject,
    set_gate_enabled,
    show_pending,
)


# ---------------------------------------------------------------------------
# Fake asyncpg pool — a tiny in-memory store keyed on the SQL fragment we
# expect approval_service to issue. Keeping it dumb on purpose: the goal is
# to test approval_service's logic, not a real DB driver.
# ---------------------------------------------------------------------------


class FakeConnection:
    def __init__(self, store: "FakeStore") -> None:
        self._store = store

    async def __aenter__(self):  # used for `async with pool.acquire() as conn`
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("UPDATE pipeline_tasks SET awaiting_gate = $1"):
            gate, artifact_json, paused_at, task_id = args
            row = self._store.tasks.get(task_id)
            if row is not None:
                row["awaiting_gate"] = gate
                row["gate_artifact"] = artifact_json
                row["gate_paused_at"] = paused_at
            return "UPDATE 1"
        if sql_norm.startswith("UPDATE pipeline_tasks SET awaiting_gate = NULL"):
            (task_id,) = args
            row = self._store.tasks.get(task_id)
            if row is not None:
                row["awaiting_gate"] = None
                row["gate_artifact"] = "{}"
                row["gate_paused_at"] = None
            return "UPDATE 1"
        if sql_norm.startswith("UPDATE pipeline_tasks SET status = $2"):
            task_id, new_status, error_message = args
            row = self._store.tasks.get(task_id)
            if row is not None:
                row["status"] = new_status
                row["awaiting_gate"] = None
                row["gate_artifact"] = "{}"
                row["gate_paused_at"] = None
                if error_message is not None:
                    row["error_message"] = error_message
            return "UPDATE 1"
        if sql_norm.startswith("INSERT INTO pipeline_gate_history"):
            task_id, gate_name, feedback, metadata = args
            self._store.events.append({
                "task_id": task_id, "gate_name": gate_name,
                "event_kind": "approved", "feedback": feedback,
                "metadata": metadata,
            })
            return "INSERT 0 1"
        if sql_norm.startswith("INSERT INTO app_settings"):
            key, value, description = args
            self._store.app_settings[key] = {
                "value": value,
                "description": description,
                "is_active": True,
            }
            return "INSERT 0 1"
        raise AssertionError(f"FakeConnection.execute saw unexpected SQL: {sql_norm[:80]}")

    async def fetchrow(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT pt.task_id AS id"):
            (task_id,) = args
            row = self._store.tasks.get(task_id)
            if row is None:
                return None
            return {
                "id": task_id,
                "status": row.get("status"),
                "awaiting_gate": row.get("awaiting_gate"),
                "gate_artifact": row.get("gate_artifact"),
                "gate_paused_at": row.get("gate_paused_at"),
                "topic": row.get("topic"),
                "title": row.get("title"),
            }
        raise AssertionError(f"FakeConnection.fetchrow saw unexpected SQL: {sql_norm[:80]}")

    async def fetch(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT task_id::text AS task_id, awaiting_gate AS gate_name"):
            # list_pending — optional gate filter.
            gate_filter = args[0] if len(args) >= 2 else None
            limit = args[-1]
            rows = []
            for tid, t in self._store.tasks.items():
                if not t.get("awaiting_gate"):
                    continue
                if gate_filter and t["awaiting_gate"] != gate_filter:
                    continue
                rows.append({
                    "task_id": tid,
                    "gate_name": t["awaiting_gate"],
                    "gate_artifact": t.get("gate_artifact"),
                    "gate_paused_at": t.get("gate_paused_at"),
                    "status": t.get("status"),
                    "topic": t.get("topic"),
                    "title": t.get("title"),
                })
            rows.sort(
                key=lambda r: r["gate_paused_at"] or datetime.max.replace(tzinfo=timezone.utc),
            )
            return rows[:limit]
        if sql_norm.startswith("SELECT key, value, is_active"):
            (prefix_pat,) = args
            prefix = prefix_pat.rstrip("%")
            return [
                {"key": k, "value": v["value"], "is_active": v["is_active"]}
                for k, v in self._store.app_settings.items()
                if k.startswith(prefix)
            ]
        if sql_norm.startswith("SELECT awaiting_gate AS gate_name, COUNT(*) AS pending_count FROM pipeline_tasks"):
            counts: dict[str, int] = {}
            for t in self._store.tasks.values():
                g = t.get("awaiting_gate")
                if g:
                    counts[g] = counts.get(g, 0) + 1
            return [{"gate_name": k, "pending_count": v} for k, v in counts.items()]
        raise AssertionError(f"FakeConnection.fetch saw unexpected SQL: {sql_norm[:80]}")


class FakeStore:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.app_settings: dict[str, dict[str, Any]] = {}


class FakePool:
    def __init__(self) -> None:
        self.store = FakeStore()

    def acquire(self):  # not async — returns an async-context-manager object
        return FakeConnection(self.store)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_site_config(values: dict[str, str] | None = None):
    cache = dict(values or {})
    return SimpleNamespace(
        get=lambda k, d=None: cache.get(k, d),
        _config=cache,
    )


@pytest.fixture
def fake_pool():
    return FakePool()


@pytest.fixture
def patched_audit():
    """Stop audit_log_bg from trying to schedule a real coroutine."""
    with patch("services.approval_service.audit_log_bg") as m:
        yield m


@pytest.fixture
def patched_notify():
    """Don't depend on the live Telegram / Discord plumbing."""
    with patch(
        "services.approval_service._notify_gate_tripped",
        side_effect=lambda **kw: __import__("asyncio").sleep(0)
        or {"sent": True, "reason": "ok"},
    ) as m:
        # The lambda above returns {"sent": True} but the call signature
        # in approval_service awaits the return — wrap properly.
        async def _async_ok(**kwargs):
            return {"sent": True, "reason": "ok"}
        m.side_effect = _async_ok
        yield m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIsGateEnabled:
    def test_default_is_off(self):
        cfg = _make_site_config({})
        assert is_gate_enabled("topic_decision", cfg) is False

    def test_on_string_truthy(self):
        cfg = _make_site_config({"pipeline_gate_topic_decision": "on"})
        assert is_gate_enabled("topic_decision", cfg) is True

    def test_off_string_falsy(self):
        cfg = _make_site_config({"pipeline_gate_topic_decision": "off"})
        assert is_gate_enabled("topic_decision", cfg) is False

    def test_none_site_config_is_off(self):
        assert is_gate_enabled("topic_decision", None) is False

    def test_alternate_truthy_values(self):
        for v in ("true", "1", "yes", "TRUE", "On"):
            cfg = _make_site_config({"pipeline_gate_g": v})
            assert is_gate_enabled("g", cfg) is True


@pytest.mark.asyncio
class TestPauseAtGate:
    async def test_writes_gate_columns_and_audit(
        self, fake_pool, patched_audit, patched_notify,
    ):
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "topic": "Sample",
            "title": "Sample title",
        }
        result = await pause_at_gate(
            task_id="t-1",
            gate_name="topic_decision",
            artifact={"topic": "Sample topic"},
            site_config=_make_site_config(),
            pool=fake_pool,
        )
        row = fake_pool.store.tasks["t-1"]
        assert row["awaiting_gate"] == "topic_decision"
        assert json.loads(row["gate_artifact"]) == {"topic": "Sample topic"}
        assert row["gate_paused_at"] is not None
        assert result["ok"] is True
        assert result["gate_name"] == "topic_decision"
        # audit_log_bg called exactly once with "approval_gate_paused".
        assert patched_audit.called
        kwargs = patched_audit.call_args.kwargs
        assert kwargs["event_type"] == "approval_gate_paused"
        assert kwargs["task_id"] == "t-1"
        assert kwargs["details"]["gate_name"] == "topic_decision"
        # Notify path attempted.
        assert patched_notify.await_count == 1

    async def test_notify_off_skips_notification(
        self, fake_pool, patched_audit, patched_notify,
    ):
        fake_pool.store.tasks["t-1"] = {"status": "in_progress"}
        result = await pause_at_gate(
            task_id="t-1",
            gate_name="topic_decision",
            artifact={"x": 1},
            site_config=_make_site_config(),
            pool=fake_pool,
            notify=False,
        )
        assert patched_notify.await_count == 0
        assert result["notify"]["sent"] is False
        assert result["notify"]["reason"] == "skipped"


@pytest.mark.asyncio
class TestApprove:
    async def test_clears_gate_and_inserts_event(self, fake_pool, patched_audit):
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "awaiting_gate": "topic_decision",
            "gate_artifact": json.dumps({"topic": "x"}),
            "gate_paused_at": datetime.now(timezone.utc),
        }
        result = await approve(
            task_id="t-1",
            site_config=_make_site_config(),
            pool=fake_pool,
        )
        assert result["ok"] is True
        assert result["gate_name"] == "topic_decision"
        # Gate cleared.
        assert fake_pool.store.tasks["t-1"]["awaiting_gate"] is None
        # pipeline_gate_history row emitted (Phase 1 of poindexter#366).
        assert any(
            e.get("event_kind") == "approved"
            and e.get("task_id") == "t-1"
            and e.get("gate_name") == "topic_decision"
            for e in fake_pool.store.events
        )

    async def test_raises_when_task_missing(self, fake_pool):
        with pytest.raises(TaskNotFoundError):
            await approve(
                task_id="nope",
                site_config=_make_site_config(),
                pool=fake_pool,
            )

    async def test_raises_when_no_gate_active(self, fake_pool):
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "awaiting_gate": None,
        }
        with pytest.raises(TaskNotPausedError):
            await approve(
                task_id="t-1",
                site_config=_make_site_config(),
                pool=fake_pool,
            )

    async def test_raises_on_gate_mismatch(self, fake_pool, patched_audit):
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "awaiting_gate": "topic_decision",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        with pytest.raises(GateMismatchError):
            await approve(
                task_id="t-1",
                gate_name="preview_approval",
                site_config=_make_site_config(),
                pool=fake_pool,
            )

    async def test_records_feedback_in_audit(self, fake_pool, patched_audit):
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "awaiting_gate": "topic_decision",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        await approve(
            task_id="t-1",
            feedback="LGTM",
            site_config=_make_site_config(),
            pool=fake_pool,
        )
        assert patched_audit.called
        kwargs = patched_audit.call_args.kwargs
        assert kwargs["details"]["feedback"] == "LGTM"


@pytest.mark.asyncio
class TestReject:
    async def test_default_status_is_rejected(self, fake_pool, patched_audit):
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "awaiting_gate": "topic_decision",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        result = await reject(
            task_id="t-1",
            reason="off-brand",
            site_config=_make_site_config(),
            pool=fake_pool,
        )
        assert result["new_status"] == "rejected"
        assert fake_pool.store.tasks["t-1"]["status"] == "rejected"
        assert fake_pool.store.tasks["t-1"]["awaiting_gate"] is None

    async def test_custom_reject_status_via_settings(
        self, fake_pool, patched_audit,
    ):
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "awaiting_gate": "topic_decision",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        cfg = _make_site_config({
            "approval_gate_topic_decision_reject_status": "dismissed",
        })
        result = await reject(
            task_id="t-1",
            site_config=cfg,
            pool=fake_pool,
        )
        assert result["new_status"] == "dismissed"
        assert fake_pool.store.tasks["t-1"]["status"] == "dismissed"

    async def test_raises_on_unknown_task(self, fake_pool):
        with pytest.raises(TaskNotFoundError):
            await reject(
                task_id="nope", site_config=_make_site_config(), pool=fake_pool,
            )

    async def test_raises_on_gate_mismatch(self, fake_pool, patched_audit):
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "awaiting_gate": "topic_decision",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        with pytest.raises(GateMismatchError):
            await reject(
                task_id="t-1",
                gate_name="preview_approval",
                site_config=_make_site_config(),
                pool=fake_pool,
            )


@pytest.mark.asyncio
class TestListPending:
    async def test_returns_only_paused_rows_oldest_first(self, fake_pool):
        now = datetime.now(timezone.utc)
        fake_pool.store.tasks["t-old"] = {
            "status": "in_progress",
            "awaiting_gate": "topic_decision",
            "gate_artifact": json.dumps({"topic": "old"}),
            "gate_paused_at": now - timedelta(hours=2),
            "topic": "old",
            "title": "Old",
        }
        fake_pool.store.tasks["t-new"] = {
            "status": "in_progress",
            "awaiting_gate": "topic_decision",
            "gate_artifact": json.dumps({"topic": "new"}),
            "gate_paused_at": now,
            "topic": "new",
            "title": "New",
        }
        fake_pool.store.tasks["t-not-paused"] = {
            "status": "in_progress",
            "awaiting_gate": None,
        }
        rows = await list_pending(pool=fake_pool)
        assert [r["task_id"] for r in rows] == ["t-old", "t-new"]
        assert rows[0]["artifact"] == {"topic": "old"}
        # Timestamps stringified.
        assert isinstance(rows[0]["gate_paused_at"], str)

    async def test_filter_by_gate(self, fake_pool):
        now = datetime.now(timezone.utc)
        fake_pool.store.tasks["t-1"] = {
            "awaiting_gate": "topic_decision",
            "gate_artifact": "{}",
            "gate_paused_at": now,
        }
        fake_pool.store.tasks["t-2"] = {
            "awaiting_gate": "preview_approval",
            "gate_artifact": "{}",
            "gate_paused_at": now,
        }
        rows = await list_pending(pool=fake_pool, gate_name="preview_approval")
        assert [r["task_id"] for r in rows] == ["t-2"]


@pytest.mark.asyncio
class TestShowPending:
    async def test_returns_single_row(self, fake_pool):
        now = datetime.now(timezone.utc)
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "awaiting_gate": "topic_decision",
            "gate_artifact": json.dumps({"topic": "T"}),
            "gate_paused_at": now,
            "topic": "T",
            "title": "Ti",
        }
        row = await show_pending(pool=fake_pool, task_id="t-1")
        assert row["task_id"] == "t-1"
        assert row["gate_name"] == "topic_decision"
        assert row["artifact"] == {"topic": "T"}

    async def test_raises_on_missing_task(self, fake_pool):
        with pytest.raises(TaskNotFoundError):
            await show_pending(pool=fake_pool, task_id="nope")

    async def test_raises_when_not_paused(self, fake_pool):
        fake_pool.store.tasks["t-1"] = {
            "status": "in_progress",
            "awaiting_gate": None,
        }
        with pytest.raises(TaskNotPausedError):
            await show_pending(pool=fake_pool, task_id="t-1")


@pytest.mark.asyncio
class TestGateSettings:
    async def test_set_gate_enabled_writes_app_settings(
        self, fake_pool, patched_audit,
    ):
        cfg = _make_site_config({})
        result = await set_gate_enabled(
            gate_name="topic_decision",
            enabled=True,
            pool=fake_pool,
            site_config=cfg,
        )
        assert result["ok"] is True
        assert result["enabled"] is True
        assert "pipeline_gate_topic_decision" in fake_pool.store.app_settings
        assert (
            fake_pool.store.app_settings["pipeline_gate_topic_decision"]["value"]
            == "on"
        )
        # In-memory cache patched too.
        assert cfg._config["pipeline_gate_topic_decision"] == "on"

    async def test_set_gate_disabled_writes_off(self, fake_pool, patched_audit):
        await set_gate_enabled(
            gate_name="topic_decision",
            enabled=False,
            pool=fake_pool,
            site_config=_make_site_config(),
        )
        assert (
            fake_pool.store.app_settings["pipeline_gate_topic_decision"]["value"]
            == "off"
        )

    async def test_list_gates_merges_settings_and_live_rows(self, fake_pool):
        # One known gate via settings, one live (paused) gate not yet in settings.
        fake_pool.store.app_settings["pipeline_gate_topic_decision"] = {
            "value": "on", "is_active": True, "description": "",
        }
        now = datetime.now(timezone.utc)
        fake_pool.store.tasks["t-1"] = {
            "awaiting_gate": "preview_approval",
            "gate_artifact": "{}",
            "gate_paused_at": now,
        }
        rows = await list_gates(pool=fake_pool)
        names = {r["gate_name"] for r in rows}
        assert names == {"topic_decision", "preview_approval"}
        topic = next(r for r in rows if r["gate_name"] == "topic_decision")
        preview = next(r for r in rows if r["gate_name"] == "preview_approval")
        assert topic["enabled"] is True
        assert topic["pending_count"] == 0
        assert preview["enabled"] is False  # never set
        assert preview["pending_count"] == 1
