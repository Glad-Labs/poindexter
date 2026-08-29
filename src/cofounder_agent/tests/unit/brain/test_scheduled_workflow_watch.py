"""Unit tests for ``brain/scheduled_workflow_watch.py`` (2026-08-28).

Pins the dead-man's switch for SCHEDULED CI: a cron workflow that stops
firing, or that has never once passed, has no PR to block and turns nothing
red — so only a clock-driven watcher catches it.

The load-bearing test here is ``test_runs_query_filters_to_event_schedule``.
Several watched workflows (``security``, ``unit-tests``, ``release-please``,
``console-contract-drift``) also run on pushes and PRs. Ask GitHub for their
last successful run WITHOUT ``event=schedule`` and you get today's push, so a
cron dead for three weeks reports healthy — which would make this probe an
instance of the very "green while checking nothing" failure it exists to
catch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from brain import scheduled_workflow_watch as swf

_WATCH = [{"repo": "acme/widgets", "workflow": "benchmarks.yml", "max_age_hours": 30}]
_WATCH_JSON = json.dumps(_WATCH)


def _pool(*, prev_state=None, watches=_WATCH_JSON, enabled="true", last_checked=None):
    pool = MagicMock()

    async def _fetchrow(query, *args, **kwargs):  # noqa: ANN001, ARG001
        if "brain_knowledge" in query:
            entity = args[0] if args else ""
            if entity.endswith(":_last_checked"):
                return {"value": last_checked} if last_checked else None
            return {"value": prev_state} if prev_state is not None else None
        key = args[0] if args else ""
        return {
            swf.ENABLED_SETTING_KEY: {"value": enabled},
            swf.WATCHES_SETTING_KEY: {"value": watches},
            swf.INTERVAL_SETTING_KEY: {"value": "0"},  # no throttle in tests
        }.get(key)

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    return pool


def _client(pages):
    """Fake httpx client. ``pages`` maps only_success -> (total, created_at)."""
    calls = []

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):  # noqa: ANN001
            calls.append({"url": url, "params": params or {}})
            only_success = (params or {}).get("status") == "success"
            total, created = pages[only_success]
            runs = [{"created_at": created}] if created else []
            return _Resp({"total_count": total, "workflow_runs": runs})

    mod = MagicMock()
    mod.AsyncClient = MagicMock(return_value=_Client())
    return mod, calls


def _findings(pool):
    out = []
    for c in pool.execute.call_args_list:
        if c.args and "audit_log" in c.args[0]:
            out.append(json.loads(c.args[1]))
    return out


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(swf, "_shared_read_app_setting", AsyncMock(return_value="t0ken"))


def _iso(hours_ago):
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


@pytest.mark.unit
class TestScheduleFilter:
    @pytest.mark.asyncio
    async def test_runs_query_filters_to_event_schedule(self, monkeypatch):
        """Without this the probe reports push runs as scheduled health."""
        mod, calls = _client({False: (12, _iso(2)), True: (10, _iso(2))})
        monkeypatch.setattr(swf, "httpx", mod)
        await swf.run_scheduled_workflow_watch(_pool())

        assert calls, "probe made no GitHub calls"
        for c in calls:
            assert c["params"].get("event") == "schedule", (
                f"unfiltered runs query: {c['params']!r} — a workflow that also "
                "runs on PRs would report a push run as scheduled health."
            )

    @pytest.mark.asyncio
    async def test_success_query_asks_for_success_status(self, monkeypatch):
        mod, calls = _client({False: (12, _iso(2)), True: (10, _iso(2))})
        monkeypatch.setattr(swf, "httpx", mod)
        await swf.run_scheduled_workflow_watch(_pool())
        assert any(c["params"].get("status") == "success" for c in calls)


@pytest.mark.unit
class TestVerdicts:
    @pytest.mark.asyncio
    async def test_never_green_emits_its_own_mode(self, monkeypatch):
        """71 runs, 0 green — the benchmarks shape."""
        mod, _ = _client({False: (71, _iso(1)), True: (0, None)})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool()
        summary = await swf.run_scheduled_workflow_watch(pool)

        found = _findings(pool)
        assert len(found) == 1
        assert found[0]["kind"] == "scheduled_workflow_stale"
        assert found[0]["extra"]["mode"] == "never_green"
        assert "NEVER succeeded" in found[0]["title"]
        assert summary["ok"] is False

    @pytest.mark.asyncio
    async def test_stale_when_last_success_older_than_window(self, monkeypatch):
        mod, _ = _client({False: (40, _iso(1)), True: (30, _iso(50))})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool()
        summary = await swf.run_scheduled_workflow_watch(pool)

        found = _findings(pool)
        assert len(found) == 1
        assert found[0]["extra"]["mode"] == "stale"
        assert summary["ok"] is False

    @pytest.mark.asyncio
    async def test_recent_success_is_clean(self, monkeypatch):
        mod, _ = _client({False: (40, _iso(1)), True: (40, _iso(2))})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool()
        summary = await swf.run_scheduled_workflow_watch(pool)
        assert _findings(pool) == []
        assert summary["ok"] is True


@pytest.mark.unit
class TestEdgeTriggering:
    @pytest.mark.asyncio
    async def test_persistent_stall_does_not_refire(self, monkeypatch):
        mod, _ = _client({False: (40, _iso(1)), True: (30, _iso(50))})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool(prev_state="stale")
        await swf.run_scheduled_workflow_watch(pool)
        assert _findings(pool) == []

    @pytest.mark.asyncio
    async def test_already_dead_at_boot_still_emits_once(self, monkeypatch):
        """prev=None + bad must emit, or a boot-time outage stays invisible."""
        mod, _ = _client({False: (40, _iso(1)), True: (30, _iso(50))})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool(prev_state=None)
        assert len(_findings(pool)) == 0
        await swf.run_scheduled_workflow_watch(pool)
        assert len(_findings(pool)) == 1

    @pytest.mark.asyncio
    async def test_recovery_emits_nothing_and_clears_state(self, monkeypatch):
        mod, _ = _client({False: (40, _iso(1)), True: (40, _iso(2))})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool(prev_state="stale")
        await swf.run_scheduled_workflow_watch(pool)
        assert _findings(pool) == []
        wrote_ok = [
            c.args for c in pool.execute.call_args_list
            if c.args and "brain_knowledge" in c.args[0] and "ok" in c.args
        ]
        assert wrote_ok, "recovery must clear the edge state"


@pytest.mark.unit
class TestNotAssessed:
    @pytest.mark.asyncio
    async def test_zero_scheduled_runs_is_not_an_alert(self, monkeypatch):
        """An operator who never enabled the cron gets no alarms."""
        mod, _ = _client({False: (0, None), True: (0, None)})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool()
        summary = await swf.run_scheduled_workflow_watch(pool)
        assert _findings(pool) == []
        assert summary["workflows"]["acme/widgets:benchmarks.yml"]["state"] == "not_assessed"

    @pytest.mark.asyncio
    async def test_api_error_does_not_invent_a_verdict(self, monkeypatch):
        class _Boom:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, params=None):  # noqa: ANN001, ARG002
                raise RuntimeError("502 bad gateway")

        mod = MagicMock()
        mod.AsyncClient = MagicMock(return_value=_Boom())
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool()
        summary = await swf.run_scheduled_workflow_watch(pool)
        assert _findings(pool) == []
        assert summary["workflows"]["acme/widgets:benchmarks.yml"]["state"] == "not_assessed"

    @pytest.mark.asyncio
    async def test_missing_token_does_not_alarm(self, monkeypatch):
        monkeypatch.setattr(swf, "_shared_read_app_setting", AsyncMock(return_value=""))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        mod, calls = _client({False: (40, _iso(1)), True: (30, _iso(50))})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool()
        summary = await swf.run_scheduled_workflow_watch(pool)
        assert _findings(pool) == []
        assert calls == [], "must not call GitHub without a token"
        assert "token" in summary["detail"]

    @pytest.mark.asyncio
    async def test_disabled_is_a_noop(self, monkeypatch):
        mod, calls = _client({False: (40, _iso(1)), True: (30, _iso(50))})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool(enabled="false")
        summary = await swf.run_scheduled_workflow_watch(pool)
        assert summary["detail"] == "disabled"
        assert calls == []


@pytest.mark.unit
class TestConfigValidation:
    @pytest.mark.parametrize("entry", [
        {"repo": "no-slash", "workflow": "a.yml"},
        {"repo": "a/b", "workflow": "../../etc/passwd"},
        {"repo": "a/b", "workflow": "benchmarks"},          # missing extension
        {"repo": "a/b", "workflow": "a.yml", "max_age_hours": 0},
        {"repo": "a/b", "workflow": "a.yml", "max_age_hours": "soon"},
        "not-an-object",
    ])
    def test_malformed_entries_are_dropped(self, entry):
        assert swf._parse_watches(json.dumps([entry])) == []

    def test_valid_entry_survives(self):
        parsed = swf._parse_watches(_WATCH_JSON)
        assert len(parsed) == 1
        assert parsed[0]["repo"] == "acme/widgets"

    def test_non_json_is_dropped_not_raised(self):
        assert swf._parse_watches("{not json") == []
        assert swf._parse_watches('{"a": 1}') == []
        assert swf._parse_watches("") == []
