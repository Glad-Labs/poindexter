"""brain/data_freshness_probe.py — dead-man's switch for DATA feeds.

2026-07-01 observability audit: the stack alerts when a *service* dies
but not when a *data feed* does — a dashboard showing a number and a
dashboard showing a stale number look identical, which is exactly how
trust in the whole observability surface erodes. The one feed that had
a freshness watchdog (``corsair_feed_probe``, #868) is the pattern this
probe generalizes: per-feed ``max(timestamp)`` age vs an app_settings
threshold, finding emitted on the fresh→stale EDGE only (state in
``brain_knowledge``), routed via the worker's ``findings_alert_router``
(warn → Discord) with the brain's ``alert_dispatcher`` deduping.

Feeds are declarative JSON in ``app_settings.data_freshness_feeds``:

.. code-block:: json

    [{"name": "cost_logs", "table": "cost_logs", "column": "created_at",
      "threshold_minutes": 180},
     {"name": "gpu_metrics", "table": "gpu_metrics", "column": "timestamp",
      "threshold_minutes": 30,
      "filter_column": null, "filter_value": null}]

- ``table`` / ``column`` / ``filter_column`` are validated as SQL
  identifiers (``^[a-z_][a-z0-9_]*$``) before interpolation; the
  optional ``filter_value`` is always bound as a query parameter.
- A feed with zero rows is **not assessed** (mirrors corsair: an
  operator who never enabled the producer gets no false alarms).
- Findings use kind ``data_feed_stale`` (dot-free per #756 so a
  per-kind ``findings.data_feed_stale.delivery`` policy can attach) and
  a stable per-feed ``dedup_key``.

The ``corsair_csv`` sensor feed (iCUE PSU wall-power, #868) was a
filtered feed in the default list from 2026-07-02, when its dedicated
``corsair_feed_probe`` was retired, until 2026-07-28 (see DEFAULT_FEEDS).

When the feed list itself cannot be used
----------------------------------------
Only an empty value means "use the built-in feeds" (``DEFAULT_FEEDS``,
the same list ``settings_defaults.py`` seeds), and only ``[]`` means
"watch no feed". Anything else that cannot be used as written reports
ok=False and pages once per episode (``brain/failure_episode.py``, key
``failure_episode:_config`` under the ``data_freshness_probe`` entity):

* invalid JSON, or JSON that is not a list: the probe watches the built-in
  feeds instead, as it always did, but it no longer does so silently;
* an entry it has to ignore. That is one that is not an object; one with no
  name; one whose table, column or filter_column is not a lowercase SQL
  identifier; one with a ``filter_column`` and no ``filter_value`` (or the
  reverse); one whose ``threshold_minutes`` is not a whole number of minutes
  above 0; or a repeat of a name an earlier entry already uses. The valid
  entries are still watched;
* a feed Postgres rejects as written (SQLSTATE class 42: the table or column
  does not exist, the column is not a timestamp, the role may not read it).
  It passes every check above and can never be read, so it is reported like
  an ignored entry rather than left "not assessed" forever.

When nothing in the operator's list can be checked, the probe checks the
built-in feeds instead, the same as for a list that does not parse. The page
names each entry and why, and gives the fix. It repeats when the problem
changes, when it reached no channel, and every
``data_freshness_config_failure_repage_hours``. One recovery note follows
when the list is usable again. The probe runs on every brain cycle, so an
unchanged problem is recorded again (logged, written to audit_log, checked
for a reminder) only every ``data_freshness_config_recheck_minutes``. The
parse itself is silent.

Until 2026-09-28 none of this paged:
- a bad entry was dropped with a WARNING on every 5-minute cycle (a
  non-object entry with no log at all);
- a list with no valid entry watched nothing and reported "0 feed(s)
  checked; all fresh";
- two entries with one name flapped a finding every cycle;
- an ``Infinity`` threshold crashed the probe every cycle;
- a ``filter_column`` with no value silently watched the whole table;
- a misspelled table stayed "not assessed" forever;
- a failed read of the setting silently swapped the operator's list for the
  built-in feeds.

Standalone — stdlib plus the brain's own modules (asyncpg pool is injected
by the daemon).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from poindexter.brain import failure_episode
from poindexter.brain.config_value import (
    Ignored,
    ValueProblem,
    clip,
    digest,
    load_list,
    show,
)
from poindexter.brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.data_freshness_probe")

ENABLED_SETTING_KEY = "data_freshness_probe_enabled"
FEEDS_SETTING_KEY = "data_freshness_feeds"
CONFIG_RECHECK_MINUTES_KEY = "data_freshness_config_recheck_minutes"
CONFIG_REPAGE_HOURS_KEY = "data_freshness_config_failure_repage_hours"

# How often an unchanged problem with the feed list is recorded again (log
# line, audit row, reminder and undelivered-page check). The probe itself runs
# on every 5-minute brain cycle, and a new or changed problem is recorded at
# once.
DEFAULT_CONFIG_RECHECK_MINUTES = 60
# Hours between reminders while the feed list stays unusable (0 = never).
DEFAULT_CONFIG_REPAGE_HOURS = 24

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_STATE_ENTITY_PREFIX = "data_freshness_watchdog"
_FEEDS_REF = f"app_settings.{FEEDS_SETTING_KEY}"

# In-code fallback mirroring the settings_defaults.py seed (drift-guarded
# by test_data_freshness_probe.test_seeded_default_matches_in_code_fallback)
# — used when the app_settings row is missing/unparseable so a fresh brain
# still watches the core feeds. Thresholds are deliberately generous
# (page on DEAD, not on slow): cost_logs is written every 5-min brain
# cycle (electricity cost) so 3h = many missed cycles; gpu_metrics comes
# from the always-on host gpu-scraper daemon; atom_runs goes quiet only
# when the content pipeline is dark for half a day; page_views can be
# legitimately quiet, so only a 2-day silence is worth a look; the
# corsair_csv iCUE feed is re-ingested every 5 min by IngestCorsairCsvJob
# (a local CSV that iCUE rewrites every 30s), so it stays ~5-10m fresh and
# 30m pages on a genuinely dead sampler while still clearing a missed tick
# or short worker blip. (Was 120m when corsair only rode the hourly
# RunTapsJob — an hourly ingest legitimately looks ~60m stale.)
DEFAULT_FEEDS: list[dict[str, Any]] = [
    {"name": "cost_logs", "table": "cost_logs", "column": "created_at",
     "threshold_minutes": 180},
    {"name": "gpu_metrics", "table": "gpu_metrics", "column": "timestamp",
     "threshold_minutes": 30},
    {"name": "atom_runs", "table": "atom_runs", "column": "created_at",
     "threshold_minutes": 720},
    {"name": "page_views", "table": "page_views", "column": "created_at",
     "threshold_minutes": 2880},
    # No corsair_csv feed (retired 2026-07-28). The iCUE CSV sampler is gone —
    # it was the Windows-era path, and on Linux the same HX1500i is read
    # natively by node_exporter's corsairpsu hwmon. PSU wall-power is therefore
    # still covered, by Prometheus rather than by this probe: the Hardware &
    # Power dashboard reads node_hwmon_power_watt{chip=~".*1b1c.*"} plus the
    # Shelly psu_total_power_watts, both live. Watching sensor_samples now
    # would only ever report a producer we deliberately retired.
]


async def _read_setting(pool: Any, key: str, default: str = "") -> str:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001 — probe must never crash the cycle
        logger.warning("[data_freshness] setting read %s failed: %s", key, exc)
        return default
    return str(val) if val else default


async def _read_feeds_setting(pool: Any) -> str | None:
    """The raw ``data_freshness_feeds`` ('' when unset), or None when the read failed.

    A failed read is not an empty value. Read as one, it silently swapped the
    operator's list for the built-in feeds.
    """
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", FEEDS_SETTING_KEY,
        )
    except Exception as exc:  # noqa: BLE001 — probe must never crash the cycle
        logger.warning("[data_freshness] could not read %s: %s", _FEEDS_REF, exc)
        return None
    return str(val) if val else ""


async def _read_whole(pool: Any, key: str, default: int) -> int:
    """A whole-number setting; a negative one means 0."""
    raw = await _read_setting(pool, key, str(default))
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        logger.warning(
            "[data_freshness] %s=%r is not a whole number, using %d", key, raw, default,
        )
        return default


# ---------------------------------------------------------------------------
# The feed list: what data_freshness_feeds holds, and what can be used of it.
# ---------------------------------------------------------------------------

# How the operator replaces the feed list; quoted by every config page.
_CONFIG_FIX = f"`poindexter settings set {FEEDS_SETTING_KEY} '<json>'`"
_ENTRY_SHAPE = (
    '{"name": "<name>", "table": "<table>", "column": "<timestamp column>", '
    '"threshold_minutes": <minutes>}'
)
# What each kind of unusable entry is called in the one-line pass detail.
_FIELD_LABEL = {
    "entry": "not an object",
    "name": "name",
    "table": "table",
    "column": "column",
    "filter": "filter",
    "threshold_minutes": "threshold_minutes",
    "duplicate": "duplicate name",
    "query": "rejected by Postgres",
}
# Discord cuts a message at 1,900 characters (operator_notifier._try_discord),
# and the cut falls on the end of the page, where the episode lines are. So a
# page names at most this many entries, each line clipped; the log has them all.
_MAX_ENTRIES_SHOWN = 5
_ENTRY_LINE_LIMIT = 180
# Why a feed was not assessed; "rejected" is a config problem, the rest are not.
_NO_ROWS, _QUERY_FAILED, _REJECTED = "no rows", "query failed", "rejected by Postgres"


@dataclass(frozen=True)
class _FeedList:
    """What ``app_settings.data_freshness_feeds`` holds, and what it gives to check.

    ``origin`` is where ``feeds`` came from: ``list`` (the operator's valid
    entries, none for ``[]``), ``defaults`` (the value is empty, so the
    built-in feeds) or ``unusable`` (the value is not a JSON list, so no feed
    of the operator's; the caller checks the built-in feeds instead).
    """

    feeds: list[dict[str, Any]]
    origin: str
    ignored: tuple[Ignored, ...] = ()  # entries it cannot use; field is a _FIELD_LABEL key
    total: int = 0  # entries in the list; 0 when the value is not a list
    value_problem: ValueProblem | None = None


def _minutes(value: Any) -> int | None:
    """A usable ``threshold_minutes``: a whole number above 0. A numeric string counts.

    ``int()`` of a JSON ``Infinity`` raises OverflowError. Nothing caught it
    until 2026-09-28, so such a threshold crashed the probe every cycle.
    """
    if isinstance(value, bool):
        return None  # int(True) is 1, and a JSON true is not one minute
    try:
        minutes = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return minutes if minutes > 0 else None


def _check_feed(index: int, entry: Any, names: dict[str, int]) -> dict[str, Any] | Ignored:
    """Return ``entry`` as a feed to check, or say why it cannot be one."""
    if not isinstance(entry, dict):
        return Ignored(index, "entry", f"entry {index} is not an object: {show(entry)}", entry)
    name = str(entry.get("name") or "").strip()
    if not name:
        return Ignored(index, "name", f"entry {index} has no name", entry)
    label = f"entry {index} ({name})"
    idents: dict[str, str] = {}
    for key in ("table", "column"):
        value = str(entry.get(key) or "").strip()
        if not _IDENTIFIER_RE.match(value):
            why = (
                f"{label} has no {key}" if not entry.get(key)
                else f"{label}: {key} must be a lowercase SQL identifier, got {show(entry[key])}"
            )
            return Ignored(index, key, why, entry)
        idents[key] = value
    filter_column = str(entry.get("filter_column") or "").strip()
    filter_value = entry.get("filter_value")
    if filter_column and not _IDENTIFIER_RE.match(filter_column):
        return Ignored(index, "filter", (
            f"{label}: filter_column must be a lowercase SQL identifier, got "
            f"{show(entry['filter_column'])}"
        ), entry)
    if filter_column and filter_value is None:
        # Until 2026-09-28 the filter was dropped instead: the whole table was
        # watched, so another source's rows hid a dead producer.
        return Ignored(index, "filter", (
            f"{label}: filter_column {filter_column} has no filter_value, so the "
            f"whole table would be watched"
        ), entry)
    if filter_value is not None and not filter_column:
        return Ignored(index, "filter", (
            f"{label}: filter_value {show(filter_value)} has no filter_column to apply to"
        ), entry)
    minutes = _minutes(entry.get("threshold_minutes"))
    if minutes is None:
        why = (
            f"{label} has no threshold_minutes" if "threshold_minutes" not in entry
            else (
                f"{label}: threshold_minutes must be a whole number of minutes above "
                f"0, got {show(entry['threshold_minutes'])}"
            )
        )
        return Ignored(index, "threshold_minutes", why, entry)
    if name in names:
        # One name is one state row, so two feeds under it flap the edge: a
        # stale one and a fresh one re-emitted the finding every cycle.
        was = names[name]
        return Ignored(index, "duplicate", (
            f"{label}: entry {was} already uses this name; only entry {was} is watched"
        ), entry)
    names[name] = index
    return {
        "index": index, "name": name, "table": idents["table"],
        "column": idents["column"], "threshold_minutes": minutes,
        "filter_column": filter_column or None, "filter_value": filter_value,
    }


def _builtin_feeds() -> list[dict[str, Any]]:
    """``DEFAULT_FEEDS`` as feeds to check; ``index`` None marks them built-in."""
    names: dict[str, int] = {}
    feeds = []
    for index, entry in enumerate(DEFAULT_FEEDS, start=1):
        checked = _check_feed(index, entry, names)
        # Valid by construction, and pinned by test_builtin_feeds_are_all_valid.
        if isinstance(checked, dict):
            feeds.append({**checked, "index": None})
    return feeds


def _parse_feed_list(raw: str) -> _FeedList:
    """Read ``data_freshness_feeds``. Pure: logs nothing, never raises.

    It runs on every brain cycle, so it stays quiet. What it finds is logged
    and paged by :func:`_record_config_failure`, which records an unchanged
    problem only every ``data_freshness_config_recheck_minutes``.
    """
    if not raw.strip():
        return _FeedList(feeds=_builtin_feeds(), origin="defaults")
    parsed = load_list(raw, _FEEDS_REF, noun="feed")
    if isinstance(parsed, ValueProblem):
        return _FeedList(feeds=[], origin="unusable", value_problem=parsed)
    feeds: list[dict[str, Any]] = []
    ignored: list[Ignored] = []
    names: dict[str, int] = {}
    for index, entry in enumerate(parsed, start=1):
        checked = _check_feed(index, entry, names)
        if isinstance(checked, Ignored):
            ignored.append(checked)
        else:
            feeds.append(checked)
    return _FeedList(feeds=feeds, origin="list", ignored=tuple(ignored), total=len(parsed))


# ---------------------------------------------------------------------------
# Checking one feed
# ---------------------------------------------------------------------------


def _rejected_by_postgres(exc: BaseException) -> bool:
    """True when Postgres refuses the query as written: SQLSTATE class 42.

    Class 42 is "syntax error or access rule violation". It covers a table or
    column that does not exist (42P01, 42703), a column that is not a
    timestamp (42883: no ``timestamptz - <type>`` operator), and a role that
    may not read the table (42501). Retrying never helps; only an edit to the
    feed, or a grant, does. Everything else (a dropped connection, a statement
    timeout) is transient.
    """
    state = getattr(exc, "sqlstate", None)
    return isinstance(state, str) and state.startswith("42")


def _error_text(exc: BaseException) -> str:
    """The error on one line, for a log line or a page's list item.

    asyncpg puts Postgres's HINT on a line of its own, which broke the page's
    list; it is worth keeping ("Perhaps you meant to reference the column
    atom_runs.created_at"), so it joins the message. str() of some exceptions
    (an asyncio timeout) is empty, so the class name stands in.
    """
    text = " ".join(str(exc).split()).replace(" HINT: ", "; hint: ")
    return text or type(exc).__name__


async def _feed_age_minutes(
    pool: Any, feed: dict[str, Any],
) -> tuple[float | None, BaseException | None]:
    """``(minutes since the feed's newest row, None)``, or ``(None, error)``.

    The age is None for a feed with no rows. Identifiers were validated by
    ``_check_feed``; the optional filter VALUE is bound as a parameter.
    """
    where = ""
    args: list[Any] = []
    if feed["filter_column"] and feed["filter_value"] is not None:
        where = f"WHERE {feed['filter_column']} = $1"
        args.append(str(feed["filter_value"]))
    sql = (
        f"SELECT EXTRACT(EPOCH FROM (now() - max({feed['column']}))) / 60.0 "  # nosec B608 - column/table/filter_column are regex-validated identifiers (_IDENTIFIER_RE) in _check_feed; filter value is bound as $1
        f"AS age_min FROM {feed['table']} {where}"
    )
    try:
        row = await pool.fetchrow(sql, *args)
    except Exception as exc:  # noqa: BLE001 — the caller reports it, by kind
        return None, exc
    if row is None or row["age_min"] is None:
        return None, None
    return float(row["age_min"]), None


async def _read_prev_state(pool: Any, feed_name: str) -> str | None:
    try:
        row = await pool.fetchrow(
            "SELECT value FROM brain_knowledge "
            "WHERE entity = $1 AND attribute = 'last_state'",
            f"{_STATE_ENTITY_PREFIX}:{feed_name}",
        )
    except Exception as exc:  # noqa: BLE001
        # Treat unknown as no-prior — worst case is one duplicate finding —
        # but say so loudly: a persistently failing state read would
        # otherwise silently re-emit every cycle.
        logger.warning(
            "[data_freshness] prev-state read for %s failed (%s) — "
            "treating as no-prior", feed_name, exc,
        )
        return None
    return row["value"] if row else None


async def _write_state(pool: Any, feed_name: str, state: str) -> None:
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ($1, 'last_state', $2, 1.0, 'data_freshness_probe')
            ON CONFLICT (entity, attribute)
              DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            f"{_STATE_ENTITY_PREFIX}:{feed_name}", state,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[data_freshness] state write for %s failed: %s — edge "
            "detection may re-emit next cycle", feed_name, exc,
        )


async def _emit_stale_finding(
    pool: Any, feed: dict[str, Any], age_min: float,
) -> None:
    details = {
        "kind": "data_feed_stale",
        "title": (
            f"Data feed stale: {feed['name']} "
            f"({age_min:.0f}m old, threshold {feed['threshold_minutes']}m)"
        ),
        "body": (
            f"No new {feed['table']}.{feed['column']} row in "
            f"{age_min:.0f} minutes (threshold "
            f"{feed['threshold_minutes']}m). Every dashboard panel reading "
            f"this table is now showing stale data WITHOUT looking broken — "
            f"check the producer for this feed and restart it. Tune or "
            f"remove the feed via app_settings.{FEEDS_SETTING_KEY}."
        ),
        "dedup_key": f"data_feed_stale:{feed['name']}",
        "extra": {
            "feed": feed["name"],
            "age_minutes": round(age_min, 1),
            "threshold_minutes": feed["threshold_minutes"],
        },
    }
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ('finding', 'data_freshness_probe', $1::jsonb, 'warn')",
            json.dumps(details),
        )
        logger.warning(
            "[data_freshness] feed %s STALE (%.0fm > %dm) — finding emitted",
            feed["name"], age_min, feed["threshold_minutes"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[data_freshness] finding insert failed: %s", exc)


def _feed_label(feed: dict[str, Any]) -> str:
    if feed.get("index") is None:
        return f"built-in feed {feed['name']}"
    return f"entry {feed['index']} ({feed['name']})"


async def _check_feeds(
    pool: Any,
    feeds: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    stale: list[str],
    rejected: list[Ignored],
) -> int:
    """Check each feed, filling ``results`` / ``stale`` / ``rejected``.

    Returns how many of ``feeds`` Postgres did not reject: the feeds actually
    being watched.
    """
    watched = 0
    for feed in feeds:
        age_min, error = await _feed_age_minutes(pool, feed)
        if error is not None and _rejected_by_postgres(error):
            # Not logged here: it is a config problem, reported (and logged)
            # by _record_config_failure once per recheck, not every cycle.
            rejected.append(Ignored(
                feed.get("index"),
                "query",
                f"{_feed_label(feed)}: Postgres rejects its query: "
                f"{clip(_error_text(error), 140)}",
                {
                    "name": feed["name"], "table": feed["table"],
                    "column": feed["column"],
                    "sqlstate": getattr(error, "sqlstate", None),
                },
            ))
            results[feed["name"]] = {"state": "not_assessed", "reason": _REJECTED}
            continue
        watched += 1
        if error is not None:
            logger.warning(
                "[data_freshness] query for feed %s failed: %s",
                feed["name"], _error_text(error),
            )
            results[feed["name"]] = {"state": "not_assessed", "reason": _QUERY_FAILED}
            continue
        if age_min is None:
            # Zero rows ever (producer not enabled for this operator): not an
            # alert condition, mirrors corsair_feed.
            results[feed["name"]] = {"state": "not_assessed", "reason": _NO_ROWS}
            continue

        is_stale = age_min > feed["threshold_minutes"]
        new_state = "stale" if is_stale else "fresh"
        prev_state = await _read_prev_state(pool, feed["name"])

        # Edge-triggered: one finding per stale episode, not per cycle.
        # prev=None + stale still emits so a feed that is already dead at
        # brain boot gets surfaced.
        if is_stale and prev_state != "stale":
            await _emit_stale_finding(pool, feed, age_min)
        elif not is_stale and prev_state == "stale":
            logger.info(
                "[data_freshness] feed %s recovered (%.0fm old)",
                feed["name"], age_min,
            )
        if new_state != prev_state:
            await _write_state(pool, feed["name"], new_state)

        results[feed["name"]] = {
            "age_minutes": round(age_min, 1),
            "threshold_minutes": feed["threshold_minutes"],
            "state": new_state,
        }
        if is_stale:
            stale.append(feed["name"])
    return watched


# ---------------------------------------------------------------------------
# When the feed list cannot be used: page once per episode.
# ---------------------------------------------------------------------------

FAILURE_STATE_ENTITY = "data_freshness_probe"
_SOURCE = "brain.data_freshness_probe"
# One episode for the whole feed list. Under its own entity, apart from the
# per-feed state rows (data_freshness_watchdog:<name>), so no feed name can
# collide with it.
_CONFIG_KEY = failure_episode.EpisodeKey(
    entity=FAILURE_STATE_ENTITY,
    attribute="failure_episode:_config",
    label="data_freshness",
)


@dataclass(frozen=True)
class _ConfigState:
    """One pass's view of the feed list: what is wrong, and what is watched."""

    feed_list: _FeedList
    problems: tuple[Ignored, ...]  # entries it ignored, then feeds Postgres rejected
    fallback: bool  # the built-in feeds were checked instead of the operator's list
    watched: int  # feeds actually watched, the built-in ones when fallback

    @property
    def signature(self) -> str | None:
        """The config episode's identity, or None when the list is usable."""
        value_problem = self.feed_list.value_problem
        if value_problem is None and not self.problems:
            return None
        if value_problem is not None:
            base = f"config:{value_problem.kind}"
        elif self.feed_list.origin == "defaults":
            base = "config:builtin-feeds"
        elif self.fallback:
            base = "config:all-entries-invalid"
        else:
            # Watching part of the list and none of it are different failures,
            # so a list that slides from one to the other is news.
            base = "config:invalid-entries"
        if self.problems:
            base += f":{len(self.problems)}:{digest(list(self.problems))}"
        return base

    def _head(self) -> str:
        value_problem = self.feed_list.value_problem
        total = self.feed_list.total
        if value_problem is not None:
            return f"{value_problem.summary}; watching the built-in feeds"
        if self.feed_list.origin == "defaults":
            return f"{_count(len(self.problems), 'built-in feed')} rejected by Postgres"
        if self.fallback:
            whole = "the only entry" if total == 1 else f"all {total} entries"
            return f"{whole} in {_FEEDS_REF} unusable; watching the built-in feeds"
        return f"{len(self.problems)} of {total} entries in {_FEEDS_REF} unusable"

    @property
    def summary(self) -> str:
        """The problem in one line, for the pass detail."""
        which = "; ".join(
            f"{f'entry {ig.index}' if ig.index is not None else ig.entry['name']}: "
            f"{_FIELD_LABEL[ig.field]}"
            for ig in self.problems
        )
        return f"{self._head()} ({which})" if which else self._head()

    @property
    def detail(self) -> str:
        """The problem in full, every entry included, for logs and audit_log."""
        reasons = "; ".join(ig.reason for ig in self.problems)
        return f"{self._head()}: {reasons}" if reasons else self._head()


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _builtin_names() -> str:
    return ", ".join(feed["name"] for feed in DEFAULT_FEEDS)


def _build_config_page(
    state: _ConfigState,
    *,
    reason: str,
    episode: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str, str]:
    """Render ``(title, body)`` for a feed list the probe cannot use."""
    still = " still" if reason == failure_episode.PAGE_REMINDER else ""
    feed_list, problems = state.feed_list, state.problems
    n, total = len(problems), feed_list.total
    them = "it" if n == 1 else "them"
    not_in_effect = (
        "Until then, any feed your list adds, and any threshold it changes, is "
        "not in effect."
    )
    if feed_list.value_problem is not None:
        title = f"Data-freshness probe{still} cannot use {_FEEDS_REF}"
        head = (
            f"{feed_list.value_problem.problem} So the probe ignores the whole list "
            f"and watches its built-in feeds instead: {_builtin_names()}."
        )
        fix = f"Fix it with {_CONFIG_FIX}: a JSON list of {_ENTRY_SHAPE} objects."
        consequence = not_in_effect
    elif feed_list.origin == "defaults":
        title = f"Data-freshness probe{still} cannot check {_count(n, 'built-in feed')}"
        head = (
            f"{_FEEDS_REF} is empty, so the probe watches its built-in feeds, and "
            f"Postgres rejects the query for {'one of them' if n == 1 else f'{n} of them'}:"
        )
        fix = f"Set a list without {them} with {_CONFIG_FIX}."
        consequence = (
            f"Until then, nothing checks {'that feed' if n == 1 else 'those feeds'}, "
            f"so a dashboard reading {them} can show stale data without looking broken."
        )
    elif state.fallback:
        title = f"Data-freshness probe{still} cannot use {_FEEDS_REF}"
        whole = "the only entry" if total == 1 else f"any of the {total} entries"
        head = (
            f"The data-freshness probe cannot use {whole} in {_FEEDS_REF}, so it "
            f"watches its built-in feeds instead ({_builtin_names()}):"
        )
        fix = f"Fix or remove {them} with {_CONFIG_FIX}, which replaces the whole list."
        consequence = not_in_effect
    else:
        title = (
            f"Data-freshness probe{still} cannot use {n} "
            f"{'entry' if n == 1 else 'entries'} in {_FEEDS_REF}"
        )
        head = (
            f"The data-freshness probe cannot use {n} of the {total} entries in "
            f"{_FEEDS_REF} and watches the other {state.watched}:"
        )
        fix = f"Fix or remove {them} with {_CONFIG_FIX}, which replaces the whole list."
        consequence = (
            "Until then, nothing checks those feeds, so a dashboard reading them "
            "can show stale data without looking broken."
        )
    lines = [head]
    shown = problems[:_MAX_ENTRIES_SHOWN]
    lines += [f"- {clip(ig.reason, _ENTRY_LINE_LIMIT)}" for ig in shown]
    if n > len(shown):
        lines.append(f"- and {n - len(shown)} more, listed in the brain log")
    if feed_list.value_problem is not None and feed_list.value_problem.hint:
        lines.append(feed_list.value_problem.hint)
    lines += [fix, "", consequence]
    lines += failure_episode.episode_lines(
        episode,
        reason=reason,
        retry_minutes=max(1, int(config["recheck_minutes"])),
        repage_hours=int(config["repage_hours"]),
        repage_setting_key=CONFIG_REPAGE_HOURS_KEY,
    )
    return title, "\n".join(lines)


async def _emit_audit_event(
    pool: Any, event: str, detail: str, *, extra: dict[str, Any], severity: str = "info",
) -> None:
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ($1, $2, $3::jsonb, $4)",
            event, _SOURCE, json.dumps({"detail": detail, **extra}), severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[data_freshness] audit_log insert for %s failed: %s", event, exc)


async def _record_config_failure(
    pool: Any,
    state: _ConfigState,
    signature: str,
    *,
    now_utc: datetime,
    notify_fn: Callable[..., Any],
) -> dict[str, Any]:
    """Record a feed list the probe cannot use; page when the episode says so.

    A new or changed problem is recorded at once. An unchanged one is recorded
    again only every ``data_freshness_config_recheck_minutes``, because the
    probe runs on every brain cycle: one WARNING and one audit row an hour, not
    one every five minutes. Either way the pass reports it and is not ok.

    Every such failure is LOUD: only the operator can fix the list. There is
    no credential behind it, so ``credential_key`` is None, and a rewrite is
    news only when it changes the signature.
    """
    summary: dict[str, Any] = {
        "signature": signature,
        "detail": state.summary,
        "entries": state.feed_list.total,
        "watched": state.watched,
        "fallback": state.fallback,
        "problems": [ig.reason for ig in state.problems],
    }
    recheck = await _read_whole(pool, CONFIG_RECHECK_MINUTES_KEY, DEFAULT_CONFIG_RECHECK_MINUTES)
    episode = await failure_episode.read_episode(pool, _CONFIG_KEY)
    last = failure_episode.parse_iso8601_utc((episode or {}).get("last_attempt_at"))
    if (
        episode
        and episode.get("signature") == signature
        and last is not None
        and now_utc - last < timedelta(minutes=recheck)
    ):
        return {
            **summary, "recorded": False, "failing_since": episode.get("since"),
            "failed_attempts": episode.get("attempts"), "page_reason": None, "paged": False,
        }

    config = {
        "recheck_minutes": recheck,
        "repage_hours": await _read_whole(
            pool, CONFIG_REPAGE_HOURS_KEY, DEFAULT_CONFIG_REPAGE_HOURS,
        ),
    }
    detail = state.detail
    logger.warning("[data_freshness] cannot use the feed list as written: %s", detail)
    outcome = await failure_episode.record_failure(
        pool,
        _CONFIG_KEY,
        signature=signature,
        detail=detail,
        now_utc=now_utc,
        notify_fn=notify_fn,
        render=lambda ep, reason: _build_config_page(
            state, reason=reason, episode=ep, config=config,
        ),
        source=_SOURCE,
        dedup_prefix="data_freshness_config",
        loud=True,
        repage_hours=int(config["repage_hours"]),
        credential_key=None,
    )
    recorded = {
        **summary, "recorded": True,
        "failing_since": outcome.episode.get("since"),
        "failed_attempts": outcome.episode.get("attempts"),
        "page_reason": outcome.reason, "paged": outcome.paged,
    }
    await _emit_audit_event(
        pool, "probe.data_freshness_config_failed", detail,
        extra={k: v for k, v in recorded.items() if k != "detail"}, severity="warning",
    )
    return recorded


async def _close_config_episode(
    pool: Any, state: _ConfigState, *, notify_fn: Callable[..., Any],
) -> None:
    """End the config episode once the feed list is usable again.

    One recovery note, and only when the episode paged: an episode nobody was
    told about has nothing to take back.
    """
    episode = await failure_episode.close_episode(pool, _CONFIG_KEY)
    if not episode:
        return
    feed_list = state.feed_list
    if feed_list.origin == "defaults":
        now = f"{_FEEDS_REF} is empty now, so it watches its built-in feeds"
    elif feed_list.total == 0:
        now = f"{_FEEDS_REF} is an empty list now, so it watches no feed"
    else:
        now = f"It watches {_count(state.watched, 'feed')} from this pass on"
    note = (
        f"The data-freshness probe can use {_FEEDS_REF} again "
        f"{failure_episode.recovery_summary(episode)}. {now}."
    )
    logger.info("[data_freshness] %s", note)
    await _emit_audit_event(
        pool, "probe.data_freshness_config_recovered", note,
        extra={
            "signature": episode.get("signature") or "unknown",
            "attempts": episode.get("attempts"),
            "failing_since": episode.get("since"),
            "was_paged": bool(episode.get("paged_at")),
            "watched": state.watched,
        },
    )
    if episode.get("paged_at"):
        failure_episode.send_page(
            notify_fn,
            label="data_freshness",
            title=f"Data-freshness probe can use {_FEEDS_REF} again",
            detail=note,
            source=_SOURCE,
            severity="info",
            dedup_key="data_freshness_config_recovered",
            if_undelivered="the operator still believes the feed list is broken",
        )


# ---------------------------------------------------------------------------
# Top-level entry point.
# ---------------------------------------------------------------------------


def _pass_detail(results: dict[str, dict[str, Any]], stale: list[str]) -> str:
    """Count ASSESSED feeds, never configured ones: "all 4 fresh" with 2 of
    them unread is the claim of health this probe exists to refuse."""
    assessed = [n for n, r in results.items() if r.get("state") != "not_assessed"]
    n_unassessed = len(results) - len(assessed)
    if stale:
        detail = f"STALE: {', '.join(stale)} ({len(stale)} of {len(assessed)} assessed feed(s))"
    elif assessed:
        detail = f"all {len(assessed)} assessed feed(s) fresh"
    elif not results:
        detail = "no feeds configured"
    else:
        detail = "no feed assessed"
    if n_unassessed:
        detail += f" ({n_unassessed} not assessed)"
    return detail


async def run_data_freshness_probe(
    pool: Any,
    *,
    now_fn: Callable[[], datetime] | None = None,
    notify_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """One pass over every configured feed. Never raises.

    Returns ``{ok, detail, feeds: {name: {age_minutes, threshold, state}}}``
    for the brain's ``run_cycle`` aggregation, plus ``config`` when the feed
    list cannot be used as written. ``ok=False`` when an assessed feed is
    stale or the list is unusable. ``now_fn`` and ``notify_fn`` are test
    seams; the daemon passes neither.
    """
    now_utc = now_fn() if now_fn else datetime.now(UTC)
    notify_fn = notify_fn or notify_operator

    enabled = (await _read_setting(pool, ENABLED_SETTING_KEY, "true")).lower()
    if enabled in ("false", "0", "no", "off"):
        return {"ok": True, "detail": "disabled", "feeds": {}}

    raw = await _read_feeds_setting(pool)
    results: dict[str, dict[str, Any]] = {}
    stale: list[str] = []
    rejected: list[Ignored] = []
    if raw is None:
        # Nobody knows what the operator configured, so the episode is left
        # alone; the built-in feeds are still worth checking.
        await _check_feeds(pool, _builtin_feeds(), results, stale, rejected)
        detail = f"{_pass_detail(results, stale)}; could not read {_FEEDS_REF}, checked the built-in feeds"
        return {"ok": False, "detail": detail, "feeds": results}

    feed_list = _parse_feed_list(raw)
    watched = await _check_feeds(pool, feed_list.feeds, results, stale, rejected)
    fallback = feed_list.origin == "unusable" or (
        feed_list.origin == "list" and feed_list.total > 0 and watched == 0
    )
    if fallback:
        # Nothing in the operator's list can be checked (it does not parse,
        # or every entry was ignored or rejected): check the built-in feeds
        # rather than none, and say so.
        watched = await _check_feeds(pool, _builtin_feeds(), results, stale, rejected)
    state = _ConfigState(
        feed_list=feed_list,
        problems=(*feed_list.ignored, *rejected),
        fallback=fallback,
        watched=watched,
    )

    config_failure: dict[str, Any] | None = None
    signature = state.signature
    if signature is None:
        await _close_config_episode(pool, state, notify_fn=notify_fn)
    else:
        config_failure = await _record_config_failure(
            pool, state, signature, now_utc=now_utc, notify_fn=notify_fn,
        )

    detail = _pass_detail(results, stale)
    if config_failure:
        detail += f"; {config_failure['detail']}"
    summary: dict[str, Any] = {
        "ok": not stale and not config_failure, "detail": detail, "feeds": results,
    }
    if config_failure:
        summary["config"] = config_failure
    return summary
