"""Unit tests — the data-freshness probe pages a feed list it cannot use.

``app_settings.data_freshness_feeds`` is the list of data feeds whose
staleness this probe watches. Until 2026-09-28 a list the probe could not use
never reached the operator:

* a bad entry was dropped with a WARNING on every 5-minute cycle (a
  non-object entry with no log at all);
* a list with no valid entry watched nothing and reported "0 feed(s)
  checked; all fresh";
* a list that did not parse was swapped for the built-in feeds, silently;
* two entries with one name flapped a ``data_feed_stale`` finding every cycle;
* an ``Infinity`` threshold crashed the probe every cycle;
* a ``filter_column`` with no value watched the whole table;
* a misspelled table stayed "not assessed" forever.

These tests pin the replacement, built on ``brain/failure_episode.py`` like
the scheduled-CI watchdog's config episode (#4151). The probe runs on every
brain cycle, so the pool is a small STATEFUL fake: the episode, the per-feed
state and the audit rows only mean anything across cycles.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from poindexter.brain import data_freshness_probe as dfp
from poindexter.brain import failure_episode as fe
from poindexter.brain import operator_notifier as on

_T0 = datetime(2026, 9, 28, 16, 0, 0, tzinfo=UTC)
_REF = f"app_settings.{dfp.FEEDS_SETTING_KEY}"
_BUILTIN_NAMES = "cost_logs, gpu_metrics, atom_runs, page_views"
_BUILTIN_TABLES = {f["table"] for f in dfp.DEFAULT_FEEDS}
# The operator's list as prod holds it (the seed, which equals the built-ins).
_FEEDS = [dict(f) for f in dfp.DEFAULT_FEEDS]
# The likeliest typo: the comma between the first two entries is gone.
_BROKEN_JSON = json.dumps(_FEEDS).replace("}, {", "} {", 1)
_BROKEN_AT = "config:invalid-json:char-95"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, start: datetime = _T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class _PgError(Exception):
    """What asyncpg raises: an exception carrying a SQLSTATE."""

    def __init__(self, sqlstate: str, message: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


_NO_TABLE = _PgError("42P01", 'relation "cost_log" does not exist')
_TIMEOUT = _PgError("57014", "canceling statement due to statement timeout")


class _FakeDB:
    """asyncpg-pool stand-in holding the rows this probe reads and writes.

    ``ages`` maps a table (``table:filter_value`` for a filtered feed, or
    ``table.column`` to single out one column) to the minutes since its newest
    row, None for no rows, or an exception to raise. Any table not listed is
    ten minutes old.
    """

    def __init__(self, *, feeds: Any = None, settings: dict[str, str] | None = None) -> None:
        raw = json.dumps(_FEEDS) if feeds is None else (
            feeds if isinstance(feeds, str) else json.dumps(feeds)
        )
        self.settings: dict[str, str] = {
            dfp.ENABLED_SETTING_KEY: "true",
            dfp.FEEDS_SETTING_KEY: raw,
            dfp.CONFIG_RECHECK_MINUTES_KEY: "60",
            dfp.CONFIG_REPAGE_HOURS_KEY: "24",
            **(settings or {}),
        }
        self.ages: dict[str, Any] = {}
        self.knowledge: dict[tuple[str, str], str] = {}
        self.knowledge_deletes = 0
        self.audit: list[tuple[str, dict[str, Any]]] = []
        self.queries: list[str] = []  # the table (or table:filter) of each feed query
        self.fail_setting_reads: set[str] = set()

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "FROM app_settings" in query:
            if args[0] in self.fail_setting_reads:
                raise RuntimeError("canceling statement due to statement timeout")
            return self.settings.get(args[0])
        if "FROM brain_knowledge" in query:  # failure_episode.read_episode
            return self.knowledge.get((args[0], args[1]))
        raise AssertionError(f"unexpected fetchval: {query}")

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if "FROM brain_knowledge" in query:  # a feed's last_state
            value = self.knowledge.get((args[0], "last_state"))
            return {"value": value} if value is not None else None
        if "max(" in query:
            table = re.search(r"FROM (\w+)", query).group(1)
            column = re.search(r"max\((\w+)\)", query).group(1)
            key = f"{table}:{args[0]}" if args else table
            self.queries.append(key)
            answer = self.ages.get(f"{table}.{column}", self.ages.get(key, 10.0))
            if isinstance(answer, BaseException):
                raise answer
            return {"age_min": answer}
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO brain_knowledge" in query:
            if "'last_state'" in query:
                self.knowledge[(args[0], "last_state")] = args[1]
            else:
                self.knowledge[(args[0], args[1])] = args[2]
        elif "DELETE FROM brain_knowledge" in query:
            self.knowledge_deletes += 1
            self.knowledge.pop((args[0], args[1]), None)
        elif "INSERT INTO audit_log" in query:
            if "'finding'" in query:
                self.audit.append(("finding", json.loads(args[0])))
            else:
                self.audit.append((args[0], json.loads(args[2])))
        else:
            raise AssertionError(f"unexpected write: {query}")
        return "OK"

    def audit_of(self, event: str) -> list[dict[str, Any]]:
        return [details for name, details in self.audit if name == event]

    def findings(self) -> list[dict[str, Any]]:
        return self.audit_of("finding")

    def episode(self) -> dict[str, Any] | None:
        raw = self.knowledge.get((dfp.FAILURE_STATE_ENTITY, "failure_episode:_config"))
        return json.loads(raw) if raw else None


class _Notifier:
    """Records pages; answers the way ``notify_operator`` does."""

    def __init__(self, result: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result or {
            "telegram": "skipped (severity below error)",
            "discord": "discord",
            "alerts_log": "alerts.log (test)",
        }

    def __call__(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return dict(self.result)

    def titles(self) -> list[str]:
        return [c["title"] for c in self.calls]


async def _run(db: _FakeDB, clock: _Clock, notify: Any) -> dict[str, Any]:
    return await dfp.run_data_freshness_probe(db, now_fn=clock, notify_fn=notify)


async def _cycles(
    n: int, db: _FakeDB, clock: _Clock, notify: Any, *, every: timedelta = timedelta(minutes=5),
) -> list[dict[str, Any]]:
    """``n`` brain cycles ``every`` apart; the clock ends one step past the last."""
    out = []
    for _ in range(n):
        out.append(await _run(db, clock, notify))
        clock.now += every
    return out


def _setup(**db_kwargs: Any) -> tuple[_FakeDB, _Clock, _Notifier]:
    return _FakeDB(**db_kwargs), _Clock(), _Notifier()


def _config_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.levelname == "WARNING" and "cannot use the feed list" in r.getMessage()
    ]


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No test here may reach the real notifier by default: it writes to
    ``~/.poindexter/alerts.log`` and to any Discord webhook in the env."""

    def _unexpected(**kwargs: Any) -> None:
        pytest.fail(f"the default notify_operator was reached: {kwargs.get('title')!r}")

    monkeypatch.setattr(dfp, "notify_operator", _unexpected)


def _real_notifier(monkeypatch, clock: _Clock) -> list[str]:
    """Route ``notify_operator`` to a Discord stub with the prod 30-min
    cooldown, its clock tied to the test clock. Returns the sent messages."""
    sent: list[str] = []

    def _discord(text: str) -> tuple[bool, str]:
        sent.append(text)
        return True, "discord"

    monkeypatch.setattr(on, "_try_discord", _discord)
    monkeypatch.setattr(on, "_try_telegram", lambda text: (False, "not configured"))
    monkeypatch.setattr(on, "_append_alerts_log", lambda text: (True, "alerts.log (test)"))
    monkeypatch.setattr(on, "_NOTIFY_AUDIT_SINK", None)
    monkeypatch.setattr(on, "_LAST_PAGED_AT", {})
    monkeypatch.setattr(on, "_PAGE_COOLDOWN_SECONDS", 30 * 60)
    monkeypatch.setattr(on, "time", SimpleNamespace(monotonic=lambda: clock.now.timestamp()))
    return sent


def _feed(name: str, **overrides: Any) -> dict[str, Any]:
    return {"name": name, "table": name, "column": "created_at", "threshold_minutes": 60, **overrides}


# ---------------------------------------------------------------------------
# A value that is not a usable JSON list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnusableValue:
    @pytest.mark.asyncio
    async def test_invalid_json_pages_once_a_day_and_the_builtin_feeds_are_checked(self, caplog):
        """Until 2026-09-28: the built-in feeds, a WARNING every cycle, no page."""
        db, clock, notify = _setup(feeds=_BROKEN_JSON)

        with caplog.at_level("WARNING", logger=dfp.logger.name):
            results = await _cycles(288, db, clock, notify)

        assert len(notify.calls) == 1, notify.titles()
        page = notify.calls[0]
        assert page["title"] == f"Data-freshness probe cannot use {_REF}"
        assert page["severity"] == "warning"
        assert page["source"] == "brain.data_freshness_probe"
        assert page["dedup_key"] == f"data_freshness_config:{_BROKEN_AT}:0"
        assert page["detail"].startswith(
            f"{_REF} is not valid JSON: Expecting ',' delimiter at line 1, column 96, "
            f'near `…hreshold_minutes": 180}} {{"name": "gpu_metrics",…`. '
            f"So the probe ignores the whole list and watches its built-in feeds "
            f"instead: {_BUILTIN_NAMES}.\n"
        )
        assert (
            f"Fix it with `poindexter settings set {dfp.FEEDS_SETTING_KEY} '<json>'`: "
            f"a JSON list of" in page["detail"]
        )
        assert (
            "Until then, any feed your list adds, and any threshold it changes, is "
            "not in effect." in page["detail"]
        )
        # The built-in feeds were still checked, on every cycle.
        assert len(db.queries) == 288 * 4
        assert set(db.queries) == _BUILTIN_TABLES
        assert [r["ok"] for r in results] == [False] * 288
        assert results[0]["detail"] == (
            f"all 4 assessed feed(s) fresh; {_REF} is not valid JSON (Expecting ',' "
            f"delimiter at line 1, column 96); watching the built-in feeds"
        )
        # Recorded once an hour, not once a cycle.
        failed = db.audit_of("probe.data_freshness_config_failed")
        assert len(failed) == 24
        assert [f["page_reason"] for f in failed] == [fe.PAGE_NEW] + [None] * 23
        assert len(_config_warnings(caplog)) == 24
        assert (db.episode()["attempts"], db.episode()["pages"]) == (24, 1)
        assert db.findings() == []

    @pytest.mark.asyncio
    async def test_the_real_notifier_sends_one_discord_message(self, monkeypatch):
        db, clock, _ = _setup(feeds=_BROKEN_JSON)
        sent = _real_notifier(monkeypatch, clock)

        await _cycles(288, db, clock, on.notify_operator)

        assert len(sent) == 1
        assert f"{_REF} is not valid JSON" in sent[0]
        assert "***" not in sent[0]  # survived the notifier's credential redaction
        assert len(sent[0]) <= 1900  # and Discord did not have to cut it

    @pytest.mark.parametrize("raw, kind, hint", [
        (json.dumps(_FEEDS[0]), "object",
         "It must be a list even for one feed: wrap the object in [ ]."),
        (json.dumps(json.dumps(_FEEDS)), "string",
         "The string itself holds a JSON list, so the value was encoded twice: "
         "store the list, not a string that contains it."),
        ("30", "number", None),
    ], ids=["object", "encoded-twice", "number"])
    @pytest.mark.asyncio
    async def test_json_that_is_not_a_list_pages(self, raw, kind, hint):
        db, clock, notify = _setup(feeds=raw)

        results = await _cycles(24, db, clock, notify)

        assert len(notify.calls) == 1
        detail = notify.calls[0]["detail"]
        assert detail.startswith(
            f"{_REF} holds a JSON {kind}, not a list. So the probe ignores the whole "
            f"list and watches its built-in feeds instead: {_BUILTIN_NAMES}."
        )
        if hint:
            assert f"\n{hint}\n" in detail
        assert results[0]["config"]["signature"] == f"config:not-a-list:{kind}"
        assert set(db.queries) == _BUILTIN_TABLES
        assert all(r["ok"] is False for r in results)

    @pytest.mark.asyncio
    async def test_a_list_with_no_usable_entry_checks_the_builtin_feeds(self):
        """Until 2026-09-28 this watched nothing: "0 feed(s) checked; all fresh"."""
        bad = [
            "cost_logs",
            {"name": "gpu", "table": "gpu metrics", "column": "timestamp", "threshold_minutes": 30},
            {"name": "views", "table": "page_views", "column": "created_at", "threshold_minutes": 0},
        ]
        db, clock, notify = _setup(feeds=bad)

        summary = await _run(db, clock, notify)

        assert notify.titles() == [f"Data-freshness probe cannot use {_REF}"]
        assert notify.calls[0]["detail"].startswith("\n".join([
            f"The data-freshness probe cannot use any of the 3 entries in {_REF}, so "
            f"it watches its built-in feeds instead ({_BUILTIN_NAMES}):",
            '- entry 1 is not an object: "cost_logs"',
            '- entry 2 (gpu): table must be a lowercase SQL identifier, got "gpu metrics"',
            "- entry 3 (views): threshold_minutes must be a whole number of minutes "
            "above 0, got 0",
            f"Fix or remove them with `poindexter settings set {dfp.FEEDS_SETTING_KEY} "
            f"'<json>'`, which replaces the whole list.",
            "",
            "Until then, any feed your list adds, and any threshold it changes, is "
            "not in effect.",
        ]))
        assert summary["ok"] is False
        assert summary["config"]["signature"].startswith("config:all-entries-invalid:3:")
        assert set(summary["feeds"]) == {f["name"] for f in dfp.DEFAULT_FEEDS}
        assert summary["detail"] == (
            f"all 4 assessed feed(s) fresh; all 3 entries in {_REF} unusable; watching "
            f"the built-in feeds (entry 1: not an object; entry 2: table; entry 3: "
            f"threshold_minutes)"
        )


# ---------------------------------------------------------------------------
# Entries the probe cannot use
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnusableEntries:
    @pytest.mark.asyncio
    async def test_a_partly_invalid_list_pages_and_watches_the_rest(self, caplog):
        bad = [
            _feed("sensors", table="sensor_samples", filter_column="source"),
            {**_FEEDS[0], "threshold_minutes": 5},
        ]
        db, clock, notify = _setup(feeds=_FEEDS + bad)

        with caplog.at_level("WARNING", logger=dfp.logger.name):
            results = await _cycles(12, db, clock, notify)

        assert notify.titles() == [f"Data-freshness probe cannot use 2 entries in {_REF}"]
        assert notify.calls[0]["detail"].startswith("\n".join([
            f"The data-freshness probe cannot use 2 of the 6 entries in {_REF} and "
            f"watches the other 4:",
            "- entry 5 (sensors): filter_column source has no filter_value, so the "
            "whole table would be watched",
            "- entry 6 (cost_logs): entry 1 already uses this name; only entry 1 is watched",
            f"Fix or remove them with `poindexter settings set {dfp.FEEDS_SETTING_KEY} "
            f"'<json>'`, which replaces the whole list.",
            "",
            "Until then, nothing checks those feeds, so a dashboard reading them can "
            "show stale data without looking broken.",
        ]))
        assert [r["ok"] for r in results] == [False] * 12
        assert results[0]["detail"] == (
            f"all 4 assessed feed(s) fresh; 2 of 6 entries in {_REF} unusable "
            f"(entry 5: filter; entry 6: duplicate name)"
        )
        assert len(db.queries) == 12 * 4  # the four valid feeds, every cycle
        assert len(_config_warnings(caplog)) == 1  # one recheck window

    @pytest.mark.asyncio
    async def test_a_duplicate_name_no_longer_flaps_a_finding(self):
        """Two feeds under one name share one state row. One stale and one
        fresh wrote stale / fresh / stale / ..., re-emitting data_feed_stale
        every cycle. The second entry is now ignored, and named in the page."""
        db, clock, notify = _setup(feeds=[_FEEDS[0], {**_FEEDS[0], "threshold_minutes": 5}])
        db.ages["cost_logs"] = 10.0  # fresh for entry 1 (180m), stale for entry 2 (5m)

        await _cycles(24, db, clock, notify)

        assert db.findings() == []
        assert db.knowledge[("data_freshness_watchdog:cost_logs", "last_state")] == "fresh"
        assert "entry 1 already uses this name" in notify.calls[0]["detail"]

    @pytest.mark.parametrize("raw_minutes", ["Infinity", "NaN", "1e400", '"30m"', "true", "-5"])
    @pytest.mark.asyncio
    async def test_a_bad_threshold_is_ignored_and_never_crashes_the_probe(self, raw_minutes):
        """int() of a JSON Infinity raises OverflowError, and nothing caught it
        until 2026-09-28: every brain cycle crashed the probe."""
        raw = (
            '[{"name": "cost_logs", "table": "cost_logs", "column": "created_at", '
            f'"threshold_minutes": {raw_minutes}}}, '
            '{"name": "atom_runs", "table": "atom_runs", "column": "created_at", '
            '"threshold_minutes": 720}]'
        )
        db, clock, notify = _setup(feeds=raw)

        summary = await _run(db, clock, notify)

        assert summary["ok"] is False
        assert summary["config"]["problems"][0].startswith(
            "entry 1 (cost_logs): threshold_minutes must be a whole number of minutes above 0"
        )
        assert db.queries == ["atom_runs"]

    @pytest.mark.asyncio
    async def test_a_filter_without_its_other_half_is_not_silently_widened(self):
        """A filter_column with no filter_value used to drop the filter and
        watch the whole table, so another source's rows hid a dead producer."""
        db, clock, notify = _setup(feeds=[
            _FEEDS[0],
            _feed("corsair", table="sensor_samples", column="sampled_at", filter_column="source"),
            _feed("other", table="sensor_samples", column="sampled_at", filter_value="x"),
        ])

        summary = await _run(db, clock, notify)

        assert "sensor_samples" not in db.queries
        assert summary["config"]["problems"] == [
            "entry 2 (corsair): filter_column source has no filter_value, so the whole "
            "table would be watched",
            'entry 3 (other): filter_value "x" has no filter_column to apply to',
        ]

    @pytest.mark.asyncio
    async def test_a_table_postgres_rejects_is_paged_not_left_unassessed(self, caplog):
        """A typo in a table name passes every check and used to stay "not
        assessed" forever, with a WARNING every cycle."""
        db, clock, notify = _setup(feeds=_FEEDS + [_feed("cost_log")])
        db.ages["cost_log"] = _NO_TABLE

        with caplog.at_level("WARNING", logger=dfp.logger.name):
            results = await _cycles(12, db, clock, notify)

        assert notify.titles() == [f"Data-freshness probe cannot use 1 entry in {_REF}"]
        assert (
            '- entry 5 (cost_log): Postgres rejects its query: relation "cost_log" '
            'does not exist' in notify.calls[0]["detail"]
        )
        assert results[0]["feeds"]["cost_log"] == {
            "state": "not_assessed", "reason": "rejected by Postgres",
        }
        assert all(r["ok"] is False for r in results)
        assert results[0]["detail"] == (
            f"all 4 assessed feed(s) fresh (1 not assessed); 1 of 5 entries in {_REF} "
            f"unusable (entry 5: rejected by Postgres)"
        )
        # Logged once per recheck window through the config episode, never
        # per cycle as a failed query.
        assert not [r for r in caplog.records if "query for feed" in r.getMessage()]
        assert len(_config_warnings(caplog)) == 1

    @pytest.mark.asyncio
    async def test_postgres_hint_joins_the_message_on_one_line(self):
        """asyncpg puts the HINT on a line of its own; in a page that broke the
        list. The pre-merge prod replay (2026-09-28) caught it with a real
        UndefinedColumnError."""
        db, clock, notify = _setup(feeds=_FEEDS + [_feed("atom_runs_col", table="atom_runs", column="created")])
        db.ages["atom_runs.created"] = _PgError(
            "42703",
            'column "created" does not exist\n'
            'HINT:  Perhaps you meant to reference the column "atom_runs.created_at".',
        )

        summary = await _run(db, clock, notify)

        assert summary["config"]["problems"] == [
            'entry 5 (atom_runs_col): Postgres rejects its query: column "created" does '
            'not exist; hint: Perhaps you meant to reference the column "atom_runs.created_at".',
        ]
        body_lines = notify.calls[0]["detail"].split("\n")
        assert not [line for line in body_lines if line.startswith("HINT")]

    @pytest.mark.asyncio
    async def test_every_feed_rejected_falls_back_to_the_builtin_feeds(self):
        db, clock, notify = _setup(feeds=[_feed("cost_log"), _feed("page_view")])
        db.ages["cost_log"] = _NO_TABLE
        db.ages["page_view"] = _PgError("42P01", 'relation "page_view" does not exist')

        summary = await _run(db, clock, notify)

        assert notify.titles() == [f"Data-freshness probe cannot use {_REF}"]
        assert notify.calls[0]["detail"].startswith(
            f"The data-freshness probe cannot use any of the 2 entries in {_REF}, so "
            f"it watches its built-in feeds instead ({_BUILTIN_NAMES}):"
        )
        assert summary["config"]["signature"].startswith("config:all-entries-invalid:2:")
        assert _BUILTIN_TABLES <= set(db.queries)

    @pytest.mark.asyncio
    async def test_a_transient_query_failure_is_not_a_config_problem(self, caplog):
        db, clock, notify = _setup()
        db.ages["atom_runs"] = _TIMEOUT

        with caplog.at_level("WARNING", logger=dfp.logger.name):
            results = await _cycles(3, db, clock, notify)

        assert notify.calls == []
        assert db.episode() is None
        assert all(r["ok"] is True for r in results)
        assert results[0]["detail"] == "all 3 assessed feed(s) fresh (1 not assessed)"
        assert results[0]["feeds"]["atom_runs"]["reason"] == "query failed"
        assert len([r for r in caplog.records if "query for feed atom_runs failed" in r.getMessage()]) == 3

    @pytest.mark.asyncio
    async def test_a_builtin_feed_postgres_rejects_is_paged_too(self):
        """An empty value means the built-in feeds. One of them missing on
        this install is not the operator's typo, but it is still unwatched."""
        db, clock, notify = _setup(feeds="")
        db.ages["gpu_metrics"] = _PgError("42P01", 'relation "gpu_metrics" does not exist')

        summary = await _run(db, clock, notify)

        assert notify.titles() == ["Data-freshness probe cannot check 1 built-in feed"]
        assert notify.calls[0]["detail"].startswith("\n".join([
            f"{_REF} is empty, so the probe watches its built-in feeds, and Postgres "
            f"rejects the query for one of them:",
            '- built-in feed gpu_metrics: Postgres rejects its query: relation '
            '"gpu_metrics" does not exist',
        ]))
        assert summary["config"]["signature"].startswith("config:builtin-feeds:1:")


# ---------------------------------------------------------------------------
# Lifecycle: news, rechecks, recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLifecycle:
    @pytest.mark.asyncio
    async def test_fixing_the_list_sends_one_recovery_note(self):
        db, clock, notify = _setup(feeds=_BROKEN_JSON)
        await _cycles(3, db, clock, notify, every=timedelta(hours=1))

        db.settings[dfp.FEEDS_SETTING_KEY] = json.dumps(_FEEDS)
        healed = await _cycles(12, db, clock, notify)

        assert healed[0]["ok"] is True
        assert healed[0]["detail"] == "all 4 assessed feed(s) fresh"
        assert "config" not in healed[0]
        assert notify.titles() == [
            f"Data-freshness probe cannot use {_REF}",
            f"Data-freshness probe can use {_REF} again",
        ]
        note = notify.calls[1]
        assert note["severity"] == "info"
        assert note["dedup_key"] == "data_freshness_config_recovered"
        assert note["detail"] == (
            f"The data-freshness probe can use {_REF} again after 3 failed attempts "
            f"since 2026-09-28 16:00 UTC (last failure: {_BROKEN_AT}). It watches 4 "
            f"feeds from this pass on."
        )
        assert db.episode() is None
        recovered = db.audit_of("probe.data_freshness_config_recovered")
        assert len(recovered) == 1
        assert recovered[0]["was_paged"] is True

    @pytest.mark.parametrize("raw, now", [
        ("", f"{_REF} is empty now, so it watches its built-in feeds."),
        ("[]", f"{_REF} is an empty list now, so it watches no feed."),
    ], ids=["empty", "empty-list"])
    @pytest.mark.asyncio
    async def test_emptying_the_list_closes_the_episode(self, raw, now):
        db, clock, notify = _setup(feeds=_BROKEN_JSON)
        await _cycles(1, db, clock, notify)

        db.settings[dfp.FEEDS_SETTING_KEY] = raw
        await _cycles(3, db, clock, notify)

        assert notify.titles()[-1] == f"Data-freshness probe can use {_REF} again"
        assert notify.calls[-1]["detail"].endswith(now)
        assert len(notify.calls) == 2
        assert db.episode() is None

    @pytest.mark.parametrize("raw, detail", [
        ("", "all 4 assessed feed(s) fresh"),
        ("[]", "no feeds configured"),
        ("  [ ]\n", "no feeds configured"),
    ], ids=["empty", "empty-list", "empty-list-spaced"])
    @pytest.mark.asyncio
    async def test_only_an_empty_value_is_quiet(self, raw, detail, caplog):
        db, clock, notify = _setup(feeds=raw)

        with caplog.at_level("WARNING", logger=dfp.logger.name):
            results = await _cycles(24, db, clock, notify)

        assert all(r == {"ok": True, "detail": detail, "feeds": r["feeds"]} for r in results)
        assert notify.calls == []
        assert db.episode() is None
        assert db.audit_of("probe.data_freshness_config_failed") == []
        assert db.knowledge_deletes == 0
        assert caplog.records == []

    @pytest.mark.asyncio
    async def test_a_new_problem_is_news_at_once_and_an_unrelated_edit_is_not(self):
        """A changed problem bypasses the recheck window; an unchanged one waits."""
        db, clock, notify = _setup(feeds=_BROKEN_JSON)
        await _cycles(1, db, clock, notify)

        # Five minutes later: the first comma is fixed and the third one lost.
        parts = json.dumps(_FEEDS).split("}, {")
        db.settings[dfp.FEEDS_SETTING_KEY] = "}, {".join(parts[:3]) + "} {" + "}, {".join(parts[3:])
        moved = await _cycles(1, db, clock, notify)
        assert moved[0]["config"]["page_reason"] == fe.PAGE_CHANGED
        assert f"The failure changed (was {_BROKEN_AT}, now config:invalid-json:char-" in (
            notify.calls[1]["detail"]
        )

        # Valid JSON at last, with one bad entry.
        bad = _feed("sensors", table="sensor_samples", filter_column="source")
        db.settings[dfp.FEEDS_SETTING_KEY] = json.dumps(_FEEDS + [bad])
        await _cycles(1, db, clock, notify)
        assert notify.titles()[2] == f"Data-freshness probe cannot use 1 entry in {_REF}"

        # The same entry "fixed" into another bad value: still news.
        db.settings[dfp.FEEDS_SETTING_KEY] = json.dumps(_FEEDS + [{**bad, "filter_column": "Source"}])
        await _cycles(1, db, clock, notify)
        assert len(notify.calls) == 4
        assert 'filter_column must be a lowercase SQL identifier, got "Source"' in (
            notify.calls[3]["detail"]
        )

        # Another valid entry appended, the bad one unchanged: not news.
        db.settings[dfp.FEEDS_SETTING_KEY] = json.dumps(
            _FEEDS + [{**bad, "filter_column": "Source"}, _feed("affiliate_clicks")],
        )
        # The last page went out at T0+15m, so its reminder is due at T0+24h15m.
        await _cycles(25, db, clock, notify, every=timedelta(hours=1))
        assert len(notify.calls) == 5  # only the 24h reminder
        assert notify.titles()[-1] == f"Data-freshness probe still cannot use 1 entry in {_REF}"

    @pytest.mark.asyncio
    async def test_losing_every_usable_entry_is_news(self):
        bad = _feed("sensors", table="sensor_samples", filter_column="source")
        db, clock, notify = _setup(feeds=[bad] + _FEEDS)
        await _cycles(1, db, clock, notify)

        db.settings[dfp.FEEDS_SETTING_KEY] = json.dumps([bad])
        summary = await _run(db, clock, notify)

        assert summary["config"]["page_reason"] == fe.PAGE_CHANGED
        assert notify.titles() == [
            f"Data-freshness probe cannot use 1 entry in {_REF}",
            f"Data-freshness probe cannot use {_REF}",
        ]

    @pytest.mark.parametrize("recheck, records", [("30", 2), ("0", 12), ("120", 1)])
    @pytest.mark.asyncio
    async def test_an_unchanged_problem_is_recorded_once_per_recheck(self, recheck, records):
        db, clock, notify = _setup(
            feeds=_BROKEN_JSON, settings={dfp.CONFIG_RECHECK_MINUTES_KEY: recheck},
        )

        results = await _cycles(12, db, clock, notify)  # one hour

        assert len(db.audit_of("probe.data_freshness_config_failed")) == records
        assert db.episode()["attempts"] == records
        assert len(notify.calls) == 1
        # A cycle that does not record still reports the problem.
        assert all(r["ok"] is False and "config" in r for r in results)
        assert [r["config"]["recorded"] for r in results].count(True) == records

    @pytest.mark.asyncio
    async def test_an_undelivered_page_is_retried_at_the_next_recheck(self):
        db, clock, _ = _setup(feeds=_BROKEN_JSON)
        notify = _Notifier({
            "telegram": "skipped (severity below error)",
            "discord": "discord send failed: URLError('name resolution')",
        })

        await _cycles(12, db, clock, notify)  # the first hour: one failed attempt
        assert len(notify.calls) == 1
        assert db.episode()["owed"] == fe.PAGE_NEW

        notify.result = {"telegram": "skipped (severity below error)", "discord": "discord"}
        retried = await _cycles(12, db, clock, notify)

        assert retried[0]["config"]["page_reason"] == fe.PAGE_UNDELIVERED
        assert "The previous page about this failure reached no channel." in (
            notify.calls[1]["detail"]
        )
        assert len(notify.calls) == 2

    @pytest.mark.asyncio
    async def test_an_episode_nobody_heard_about_ends_without_a_note(self):
        db, clock, _ = _setup(feeds=_BROKEN_JSON)
        notify = _Notifier({
            "telegram": "skipped (severity below error)",
            "discord": "discord send failed: TimeoutError()",
        })
        await _cycles(1, db, clock, notify)

        db.settings[dfp.FEEDS_SETTING_KEY] = json.dumps(_FEEDS)
        await _cycles(1, db, clock, notify)

        assert notify.titles() == [f"Data-freshness probe cannot use {_REF}"]
        assert db.episode() is None
        assert db.audit_of("probe.data_freshness_config_recovered")[0]["was_paged"] is False

    @pytest.mark.asyncio
    async def test_a_failed_read_checks_the_builtin_feeds_and_is_not_ok(self, caplog):
        """Read as '', a failed read silently swapped the operator's list for
        the built-in feeds and reported health."""
        db, clock, notify = _setup(feeds=_BROKEN_JSON)
        await _cycles(1, db, clock, notify)
        db.fail_setting_reads.add(dfp.FEEDS_SETTING_KEY)

        with caplog.at_level("WARNING", logger=dfp.logger.name):
            summary = await _run(db, clock, notify)

        assert summary["ok"] is False
        assert summary["detail"] == (
            f"all 4 assessed feed(s) fresh; could not read {_REF}, checked the "
            f"built-in feeds"
        )
        assert db.episode()["attempts"] == 1  # neither recorded nor closed
        assert len(notify.calls) == 1
        assert any(f"could not read {_REF}" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_a_feed_name_cannot_collide_with_the_config_episode(self):
        """Feed state lives under data_freshness_watchdog:<name>; the episode
        under its own entity, so a feed called _config is just a feed."""
        db, clock, notify = _setup(feeds=[_feed("_config", table="cost_logs")])
        db.ages["cost_logs"] = 500.0

        summary = await _run(db, clock, notify)

        assert summary["feeds"]["_config"]["state"] == "stale"
        assert [f["kind"] for f in db.findings()] == ["data_feed_stale"]
        assert db.episode() is None
        assert notify.calls == []

    @pytest.mark.asyncio
    async def test_a_healthy_probe_never_touches_episode_state(self):
        db, clock, notify = _setup()

        results = await _cycles(36, db, clock, notify)

        assert all(r["ok"] is True for r in results)
        assert db.episode() is None
        assert db.knowledge_deletes == 0
        assert notify.calls == []
        assert db.audit_of("probe.data_freshness_config_failed") == []


# ---------------------------------------------------------------------------
# What the page says
# ---------------------------------------------------------------------------

# The longest config page a hand edit can produce: twelve bad entries beside
# valid ones, each with a long name and a long bad table, so every line clips.
_WORST_CASE = _FEEDS + [
    {"name": "n" * 120 + str(i), "table": "T" * 300, "column": "created_at", "threshold_minutes": 60}
    for i in range(12)
]


async def _state_for(raw: str, db: _FakeDB | None = None) -> dfp._ConfigState:
    """The _ConfigState one pass builds for ``raw``, via the real check path."""
    db = db or _FakeDB(feeds=raw)
    feed_list = dfp._parse_feed_list(raw)
    results: dict[str, Any] = {}
    stale: list[str] = []
    rejected: list[Any] = []
    watched = await dfp._check_feeds(db, feed_list.feeds, results, stale, rejected)
    fallback = feed_list.origin == "unusable" or (feed_list.total > 0 and watched == 0)
    if fallback:
        watched = await dfp._check_feeds(db, dfp._builtin_feeds(), results, stale, rejected)
    return dfp._ConfigState(
        feed_list=feed_list, problems=(*feed_list.ignored, *rejected),
        fallback=fallback, watched=watched,
    )


@pytest.mark.unit
class TestConfigPageWording:
    @pytest.mark.parametrize(
        "raw",
        [
            _BROKEN_JSON,
            "[" + "1" * 5000 + "]",
            json.dumps(_FEEDS[0]),
            json.dumps(json.dumps(_FEEDS)),
            json.dumps(["x", {"name": "y"}]),
            json.dumps(_FEEDS + [_feed("cost_log")]),
            json.dumps(_WORST_CASE),
        ],
        ids=[
            "invalid-json", "huge-integer", "object", "encoded-twice",
            "no-valid-entry", "rejected", "worst-case",
        ],
    )
    @pytest.mark.parametrize(
        "reason",
        # No credential backs the config episode, so token_replaced cannot fire.
        [fe.PAGE_NEW, fe.PAGE_CHANGED, fe.PAGE_UNDELIVERED, fe.PAGE_REMINDER],
    )
    @pytest.mark.asyncio
    async def test_the_page_survives_the_redaction_and_fits_discord(self, reason, raw):
        """``_fmt_message`` masks ``token: <x>`` shapes, and Discord cuts a
        message past 1,900 characters from the end, where the episode lines
        are. Neither may touch a config page."""
        db = _FakeDB(feeds=raw)
        db.ages["cost_log"] = _NO_TABLE
        state = await _state_for(raw, db)
        episode = {
            "signature": state.signature,
            "previous_signature": "config:all-entries-invalid:12:0123abcd",
            "since": _T0.isoformat(), "attempts": 1234, "owed": fe.PAGE_NEW,
        }
        title, body = dfp._build_config_page(
            state, reason=reason, episode=episode,
            config={"recheck_minutes": 60, "repage_hours": 24},
        )
        rendered = on._fmt_message(title, body, "brain.data_freshness_probe", "warning")

        assert state.signature is not None
        assert "***" not in rendered
        assert body in rendered
        assert len(rendered) <= 1900, len(rendered)
        assert "Next reminder in 24h" in rendered

    @pytest.mark.asyncio
    async def test_the_worst_case_names_five_entries_and_counts_the_rest(self):
        state = await _state_for(json.dumps(_WORST_CASE))
        title, body = dfp._build_config_page(
            state, reason=fe.PAGE_NEW, episode={"since": _T0.isoformat()},
            config={"recheck_minutes": 60, "repage_hours": 24},
        )

        assert title == f"Data-freshness probe cannot use 12 entries in {_REF}"
        entry_lines = [line for line in body.split("\n") if line.startswith("- entry ")]
        assert len(entry_lines) == 5
        assert all(
            len(line) == dfp._ENTRY_LINE_LIMIT + 2 and line.endswith("…") for line in entry_lines
        )
        assert "\n- and 7 more, listed in the brain log\n" in body

    @pytest.mark.asyncio
    async def test_a_long_rejection_line_keeps_the_error_whole(self):
        """The clip must not reach the end of an ordinary long line, where
        the useful part is: here Postgres naming the table it cannot find.
        The same 150-character clip cut the offending value off the
        scheduled-CI watchdog's pages (caught by a prod replay, 2026-09-28)."""
        name = "newsletter_subscriber_signups_from_the_site"
        typo = "newsletter_subscriber_signups_from_the_sit"
        raw = json.dumps(_FEEDS + [_feed(name, table=typo)])
        db = _FakeDB(feeds=raw)
        db.ages[typo] = _PgError("42P01", f'relation "{typo}" does not exist')
        state = await _state_for(raw, db)

        reason = state.problems[0].reason
        _, body = dfp._build_config_page(
            state, reason=fe.PAGE_NEW, episode={"since": _T0.isoformat()},
            config={"recheck_minutes": 60, "repage_hours": 24},
        )

        assert 150 < len(reason) <= 180, len(reason)  # the fixture must sit between the two
        assert f"- {reason}\n" in body

    @pytest.mark.parametrize("raw", [json.dumps(_FEEDS), "", "[]"], ids=["fixed", "empty", "empty-list"])
    @pytest.mark.asyncio
    async def test_the_closing_notes_survive_the_redaction(self, raw):
        db, clock, notify = _setup(feeds=_BROKEN_JSON)
        await _cycles(1, db, clock, notify)
        db.settings[dfp.FEEDS_SETTING_KEY] = raw
        await _run(db, clock, notify)

        note = notify.calls[-1]
        assert note["severity"] == "info"
        rendered = on._fmt_message(note["title"], note["detail"], note["source"], note["severity"])
        assert "***" not in rendered
        assert note["detail"] in rendered


# ---------------------------------------------------------------------------
# Parsing and settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParsingAndSettings:
    def test_builtin_feeds_are_all_valid(self):
        """_builtin_feeds() drops nothing: every DEFAULT_FEEDS entry passes."""
        assert [f["name"] for f in dfp._builtin_feeds()] == [f["name"] for f in dfp.DEFAULT_FEEDS]

    @pytest.mark.parametrize("entry, field, reason", [
        ("cost_logs", "entry", 'entry 1 is not an object: "cost_logs"'),
        ({"table": "cost_logs", "column": "created_at", "threshold_minutes": 5},
         "name", "entry 1 has no name"),
        ({"name": "a", "column": "created_at", "threshold_minutes": 5},
         "table", "entry 1 (a) has no table"),
        ({"name": "a", "table": "public.cost_logs", "column": "created_at", "threshold_minutes": 5},
         "table", 'entry 1 (a): table must be a lowercase SQL identifier, got "public.cost_logs"'),
        ({"name": "a", "table": "cost_logs", "threshold_minutes": 5},
         "column", "entry 1 (a) has no column"),
        ({"name": "a", "table": "cost_logs", "column": "created_at"},
         "threshold_minutes", "entry 1 (a) has no threshold_minutes"),
        ({"name": "a", "table": "cost_logs", "column": "created_at", "threshold_minutes": True},
         "threshold_minutes",
         "entry 1 (a): threshold_minutes must be a whole number of minutes above 0, got true"),
    ])
    def test_a_bad_entry_is_ignored_with_its_reason(self, entry, field, reason):
        feed_list = dfp._parse_feed_list(json.dumps([entry]))

        assert feed_list.feeds == []
        assert [(ig.index, ig.field, ig.reason) for ig in feed_list.ignored] == [(1, field, reason)]

    def test_the_parse_is_silent_because_it_runs_every_cycle(self, caplog):
        with caplog.at_level("DEBUG", logger=dfp.logger.name):
            for raw in ("{not json", '{"a": 1}', json.dumps(["x", {"name": "y"}])):
                dfp._parse_feed_list(raw)

        assert caplog.records == []

    def test_seeded_defaults_match_the_in_code_fallbacks(self):
        from poindexter.services.settings_defaults import DEFAULTS, METADATA

        assert DEFAULTS[dfp.CONFIG_RECHECK_MINUTES_KEY] == str(dfp.DEFAULT_CONFIG_RECHECK_MINUTES)
        assert DEFAULTS[dfp.CONFIG_REPAGE_HOURS_KEY] == str(dfp.DEFAULT_CONFIG_REPAGE_HOURS)
        for key in (dfp.CONFIG_RECHECK_MINUTES_KEY, dfp.CONFIG_REPAGE_HOURS_KEY):
            assert METADATA[key] == {"owner": "data_freshness_probe", "value_type": "integer"}

    @pytest.mark.asyncio
    async def test_a_setting_that_is_not_a_whole_number_falls_back_loudly(self, caplog):
        db = _FakeDB(settings={
            dfp.CONFIG_RECHECK_MINUTES_KEY: "hourly", dfp.CONFIG_REPAGE_HOURS_KEY: "-3",
        })
        with caplog.at_level("WARNING", logger=dfp.logger.name):
            recheck = await dfp._read_whole(db, dfp.CONFIG_RECHECK_MINUTES_KEY, 60)
        assert recheck == 60
        assert any("is not a whole number" in r.getMessage() for r in caplog.records)
        assert await dfp._read_whole(db, dfp.CONFIG_REPAGE_HOURS_KEY, 24) == 0
