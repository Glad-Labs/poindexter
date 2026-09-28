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

from poindexter.brain import scheduled_workflow_watch as swf

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
    # failure_episode reads the episode row and gh_token.updated_at here.
    pool.fetchval = AsyncMock(return_value=None)
    # _open_episode_repos lists open failure episodes: none here.
    pool.fetch = AsyncMock(return_value=[])
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
    """The ``finding`` rows only; the watchdog's own probe.* audit rows are
    pinned in test_scheduled_workflow_watch_failure_episodes.py."""
    out = []
    for c in pool.execute.call_args_list:
        if c.args and "audit_log" in c.args[0] and "'finding'" in c.args[0]:
            out.append(json.loads(c.args[1]))
    return out


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(swf, "_shared_read_app_setting", AsyncMock(return_value="t0ken"))


@pytest.fixture(autouse=True)
def pages(monkeypatch):
    """Stand in for ``notify_operator``, which these tests reach by default.

    The real one writes to ``~/.poindexter/alerts.log`` and to any Discord
    webhook in the environment, so no test here may reach it.
    """
    sent: list[dict] = []

    def _record(**kwargs):
        sent.append(kwargs)
        return {"discord": "discord", "alerts_log": "alerts.log (test)"}

    monkeypatch.setattr(swf, "notify_operator", _record)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return sent


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
    async def test_zero_scheduled_runs_is_not_an_alert(self, monkeypatch, pages):
        """An operator who never enabled the cron gets no alarms."""
        mod, _ = _client({False: (0, None), True: (0, None)})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool()
        summary = await swf.run_scheduled_workflow_watch(pool)
        assert _findings(pool) == []
        assert pages == []
        assert summary["workflows"]["acme/widgets:benchmarks.yml"]["state"] == "not_assessed"
        assert "failures" not in summary or summary["failures"] == {}

    @pytest.mark.asyncio
    async def test_api_error_does_not_invent_a_verdict(self, monkeypatch, pages):
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
        # No verdict, and no claim of health either: a transient failure is
        # not paged on its own, but the pass is not ok.
        assert summary["ok"] is False
        assert summary["failures"]["acme/widgets"]["transient"] is True
        assert pages == []

    @pytest.mark.asyncio
    async def test_missing_token_pages_and_never_calls_github(self, monkeypatch, pages):
        """The operator configured watches, so no token means a blind
        watchdog. Until 2026-09-25 this reported ok with an INFO line."""
        monkeypatch.setattr(swf, "_shared_read_app_setting", AsyncMock(return_value=""))
        mod, calls = _client({False: (40, _iso(1)), True: (30, _iso(50))})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool()
        summary = await swf.run_scheduled_workflow_watch(pool)
        assert _findings(pool) == []
        assert calls == [], "must not call GitHub without a token"
        assert "token" in summary["detail"]
        assert summary["ok"] is False
        assert len(pages) == 1
        assert "gh_token is not set" in pages[0]["detail"]

    @pytest.mark.asyncio
    async def test_disabled_is_a_noop(self, monkeypatch):
        mod, calls = _client({False: (40, _iso(1)), True: (30, _iso(50))})
        monkeypatch.setattr(swf, "httpx", mod)
        pool = _pool(enabled="false")
        summary = await swf.run_scheduled_workflow_watch(pool)
        assert summary["detail"] == "disabled"
        assert calls == []


@pytest.mark.unit
class TestEveryPassIsAudible:
    """A probe that only speaks on failure looks identical to a dead probe.

    Found during this probe's own first-pass verification: the brain log said
    nothing at all, and whether it had run had to be dug out of
    brain_knowledge. Silence-on-success is the failure class this file exists
    to catch, so it must not be the file's own behaviour.
    """

    @pytest.mark.asyncio
    async def test_healthy_pass_still_logs(self, monkeypatch, caplog):
        mod, _ = _client({False: (40, _iso(1)), True: (40, _iso(2))})
        monkeypatch.setattr(swf, "httpx", mod)
        with caplog.at_level("INFO", logger=swf.logger.name):
            await swf.run_scheduled_workflow_watch(_pool())
        assert any("pass complete" in r.getMessage() for r in caplog.records), (
            "a healthy pass logged nothing — indistinguishable from not running"
        )

    @pytest.mark.asyncio
    async def test_not_assessed_never_reads_as_healthy(self, monkeypatch, caplog):
        """The headline must not CLAIM health for targets never checked.

        Asserting only the parenthetical counts is not enough — those stay
        correct even when the summary says "all 1 workflow(s) healthy", which
        is the misleading half. So this pins the claim itself, on both the
        returned `detail` (it propagates into the brain's cycle summary) and
        the log line.
        """
        mod, _ = _client({False: (0, None), True: (0, None)})
        monkeypatch.setattr(swf, "httpx", mod)
        with caplog.at_level("INFO", logger=swf.logger.name):
            summary = await swf.run_scheduled_workflow_watch(_pool())

        assert "healthy" not in summary["detail"], (
            f"claimed health with nothing assessed: {summary['detail']!r}"
        )
        assert "assessed" in summary["detail"], summary["detail"]
        # The flag the brain heartbeat reads must agree with the words: a pass
        # that assessed nothing is not ok.
        assert summary["ok"] is False

        line = next(r.getMessage() for r in caplog.records if "pass complete" in r.getMessage())
        assert "healthy" not in line, line
        assert "0 assessed" in line and "1 not assessed" in line, line

    @pytest.mark.asyncio
    async def test_mixed_pass_reports_assessed_count_not_configured(
        self, monkeypatch, caplog
    ):
        """With one healthy and one unreachable target, say 1 — not 2."""
        two = json.dumps(_WATCH + [
            {"repo": "acme/other", "workflow": "nightly.yml", "max_age_hours": 30},
        ])
        seen = {"n": 0}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, params=None):  # noqa: ANN001, ARG002
                # First target healthy; second has zero scheduled runs.
                if "nightly.yml" in url:
                    payload = {"total_count": 0, "workflow_runs": []}
                else:
                    payload = {"total_count": 5,
                               "workflow_runs": [{"created_at": _iso(2)}]}
                seen["n"] += 1

                class _R:
                    status_code = 200

                    def json(self_inner):
                        return payload

                return _R()

        mod = MagicMock()
        mod.AsyncClient = MagicMock(return_value=_Client())
        monkeypatch.setattr(swf, "httpx", mod)
        with caplog.at_level("INFO", logger=swf.logger.name):
            summary = await swf.run_scheduled_workflow_watch(_pool(watches=two))

        assert "all 1 assessed" in summary["detail"], summary["detail"]
        assert "1 not assessed" in summary["detail"], summary["detail"]
        # A cron that never fired is not a failure to read it: the pass is ok.
        assert summary["ok"] is True


@pytest.mark.unit
class TestConfigValidation:
    """Every entry is either watched or ignored with a reason, never dropped
    unreported. The pages built from these reasons are pinned in
    test_scheduled_workflow_watch_failure_episodes.py."""

    @pytest.mark.parametrize("entry, field, reason", [
        ({"repo": "no-slash", "workflow": "a.yml"}, "repo",
         'entry 1: repo must be <owner>/<name>, got "no-slash"'),
        ({"workflow": "a.yml"}, "repo", "entry 1 has no repo"),
        ({"repo": None, "workflow": "a.yml"}, "repo",
         "entry 1: repo must be <owner>/<name>, got null"),
        ({"repo": "a/b", "workflow": "../../etc/passwd"}, "workflow",
         'entry 1 (a/b): workflow must be a bare .yml or .yaml file name, '
         'got "../../etc/passwd"'),
        ({"repo": "a/b", "workflow": "benchmarks"}, "workflow",   # missing extension
         'entry 1 (a/b): workflow must be a bare .yml or .yaml file name, '
         'got "benchmarks"'),
        ({"repo": "a/b"}, "workflow", "entry 1 (a/b) has no workflow"),
        ({"repo": "a/b", "workflow": "a.yml", "max_age_hours": 0}, "max_age_hours",
         "entry 1 (a/b, a.yml): max_age_hours must be a number of hours above 0, got 0"),
        ({"repo": "a/b", "workflow": "a.yml", "max_age_hours": -5}, "max_age_hours",
         "entry 1 (a/b, a.yml): max_age_hours must be a number of hours above 0, got -5"),
        ({"repo": "a/b", "workflow": "a.yml", "max_age_hours": "soon"}, "max_age_hours",
         'entry 1 (a/b, a.yml): max_age_hours must be a number of hours above 0, got "soon"'),
        ({"repo": "a/b", "workflow": "a.yml", "max_age_hours": None}, "max_age_hours",
         "entry 1 (a/b, a.yml): max_age_hours must be a number of hours above 0, got null"),
        # float(True) is 1.0: a JSON true is not a one-hour window.
        ({"repo": "a/b", "workflow": "a.yml", "max_age_hours": True}, "max_age_hours",
         "entry 1 (a/b, a.yml): max_age_hours must be a number of hours above 0, got true"),
        ("not-an-object", "entry", 'entry 1 is not an object: "not-an-object"'),
        (["a/b", "a.yml"], "entry", 'entry 1 is not an object: ["a/b", "a.yml"]'),
    ])
    def test_a_malformed_entry_is_ignored_with_its_reason(self, entry, field, reason):
        watch_list = swf._parse_watch_list(json.dumps([entry]))

        assert watch_list.watches == []
        assert [(ig.index, ig.field, ig.reason) for ig in watch_list.ignored] == [
            (1, field, reason),
        ]
        assert watch_list.signature.startswith("config:all-entries-invalid:1:")
        assert not watch_list.empty

    @pytest.mark.parametrize("raw_age", ["NaN", "Infinity", "-Infinity", '"inf"', '"nan"'])
    def test_a_window_that_is_not_finite_is_ignored(self, raw_age):
        """An infinite or NaN window never compares below a workflow's age, so
        the workflow could never go stale. Both used to be accepted."""
        raw = f'[{{"repo": "a/b", "workflow": "a.yml", "max_age_hours": {raw_age}}}]'

        watch_list = swf._parse_watch_list(raw)

        assert watch_list.watches == []
        assert [ig.field for ig in watch_list.ignored] == ["max_age_hours"]

    def test_valid_entries_survive_in_order(self):
        watch_list = swf._parse_watch_list(json.dumps(_WATCH + [
            {"repo": "acme/other", "workflow": "nightly.yaml", "max_age_hours": "12"},
            {"repo": " acme/third ", "workflow": " weekly.yml "},
        ]))

        assert watch_list.watches == [
            {"repo": "acme/widgets", "workflow": "benchmarks.yml", "max_age_hours": 30.0},
            {"repo": "acme/other", "workflow": "nightly.yaml", "max_age_hours": 12.0},
            {"repo": "acme/third", "workflow": "weekly.yml", "max_age_hours": 30.0},
        ]
        assert watch_list.ignored == ()
        assert watch_list.signature is None
        assert not watch_list.empty

    def test_a_duplicate_with_another_window_is_ignored_and_says_which_applies(self):
        """Watched twice, it would be checked twice and counted twice. And the
        second window would be silently overruled, so it is reported."""
        watch_list = swf._parse_watch_list(json.dumps(_WATCH + [
            {"repo": "acme/widgets", "workflow": "benchmarks.yml", "max_age_hours": 12},
        ]))

        assert [(w["repo"], w["workflow"], w["max_age_hours"]) for w in watch_list.watches] == [
            ("acme/widgets", "benchmarks.yml", 30.0),
        ]
        assert [ig.reason for ig in watch_list.ignored] == [
            "entry 2 (acme/widgets, benchmarks.yml): repeats entry 1 with "
            "max_age_hours 12, not 30; only entry 1's window is used",
        ]
        assert watch_list.signature.startswith("config:invalid-entries:1:")

    def test_an_exact_duplicate_is_ignored_as_a_likely_copy_paste(self):
        watch_list = swf._parse_watch_list(json.dumps(_WATCH + _WATCH))

        assert len(watch_list.watches) == 1
        assert [ig.reason for ig in watch_list.ignored] == [
            "entry 2 (acme/widgets, benchmarks.yml): repeats entry 1 and adds "
            "nothing; if it was meant for another workflow, fix its name",
        ]

    @pytest.mark.parametrize("raw", ["", "   ", "\n", "[]", " [ ]\n"])
    def test_only_an_empty_value_means_not_configured(self, raw):
        watch_list = swf._parse_watch_list(raw)

        assert watch_list.empty
        assert watch_list.signature is None

    def test_invalid_json_is_reported_with_where_it_broke(self):
        raw = '[{"repo": "a/b", "workflow": "a.yml"} {"repo": "a/c"}]'

        watch_list = swf._parse_watch_list(raw)

        assert watch_list.watches == []
        assert watch_list.signature == "config:invalid-json:char-38"
        assert watch_list.summary == (
            "app_settings.scheduled_workflows is not valid JSON "
            "(Expecting ',' delimiter at line 1, column 39)"
        )
        assert '"a.yml"} {"repo": "a/c"}]' in watch_list.problem
        assert not watch_list.empty

    @pytest.mark.parametrize("raw, kind", [
        ('{"repo": "a/b", "workflow": "a.yml"}', "object"),
        ('"a/b:a.yml"', "string"),
        ("30", "number"),
        ("true", "boolean"),
        ("null", "null"),
    ])
    def test_json_that_is_not_a_list_is_reported_by_its_type(self, raw, kind):
        watch_list = swf._parse_watch_list(raw)

        assert watch_list.watches == []
        assert watch_list.signature == f"config:not-a-list:{kind}"
        assert watch_list.summary == (
            f"app_settings.scheduled_workflows holds a JSON {kind}, not a list"
        )

    @pytest.mark.parametrize("raw, signature", [
        # json.loads refuses an integer past Python's 4,300-digit limit with a
        # plain ValueError, not a JSONDecodeError.
        ("[" + "1" * 5000 + "]", "config:invalid-json:ValueError"),
        ("[" * 100_000 + "]" * 100_000, "config:invalid-json:RecursionError"),
    ], ids=["huge-integer", "deep-nesting"])
    def test_json_that_python_refuses_is_reported_not_raised(self, raw, signature):
        watch_list = swf._parse_watch_list(raw)

        assert watch_list.signature == signature
        assert watch_list.watches == []
        assert watch_list.summary.startswith(
            "app_settings.scheduled_workflows is not valid JSON ("
        )

    def test_a_window_too_large_for_a_float_is_ignored_not_raised(self):
        """float() of a 400-digit integer raises OverflowError."""
        raw = '[{"repo": "a/b", "workflow": "a.yml", "max_age_hours": 1' + "0" * 400 + "}]"

        watch_list = swf._parse_watch_list(raw)

        assert [ig.field for ig in watch_list.ignored] == ["max_age_hours"]

    def test_the_parse_is_silent_because_it_runs_every_cycle(self, caplog):
        """Logging here spoke on every 5-minute brain cycle. The pass logs
        once, after the throttle."""
        with caplog.at_level("DEBUG", logger=swf.logger.name):
            for raw in ("{not json", '{"a": 1}', json.dumps([{"repo": "x"}, "y"])):
                swf._parse_watch_list(raw)

        assert caplog.records == []
