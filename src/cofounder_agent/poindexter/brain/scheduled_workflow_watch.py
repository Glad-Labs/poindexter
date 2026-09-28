"""brain/scheduled_workflow_watch.py — dead-man's switch for SCHEDULED CI.

A required status check that goes red blocks a merge, so somebody notices
within the hour. A *scheduled* workflow has no PR to block: when it starts
failing — or stops firing entirely — nothing anywhere changes colour. The
2026-08-25 sweep found `benchmarks` had never once passed in 71 runs and the
weekly `playwright-e2e` had never passed in 11, both silently, for months.
Gating cannot fix that class; only something that watches on a clock can.

This is the CI sibling of ``data_freshness_probe`` (dead-man's switch for
DATA) and follows the same shape deliberately: declarative JSON config in
app_settings, per-target state in ``brain_knowledge``, edge-triggered
findings routed through the worker's ``findings_alert_router``.

Two distinct failure modes, reported differently because they diagnose
differently:

- ``stale`` — the workflow's last SUCCESSFUL scheduled run is older than its
  window. Either the cron stopped firing or every run since has failed.
- ``never_green`` — scheduled runs exist and NONE has ever succeeded. This is
  the benchmarks/playwright shape: the job was wired up, has been burning
  runner minutes on a timer ever since, and has never produced a green result.

**Runs are filtered to ``event=schedule``, and that filter is load-bearing.**
Four of the watched workflows (``security``, ``unit-tests``,
``release-please``, ``console-contract-drift``) also run on pushes or PRs. Ask
GitHub for their last successful run unfiltered and you get today's push —
so a cron that has not fired in three weeks reports perfectly healthy. That
would make this probe itself an instance of the "green while checking
nothing" failure it exists to catch.

Config is ``app_settings.scheduled_workflows``:

.. code-block:: json

    [{"repo": "Glad-Labs/glad-labs-stack", "workflow": "benchmarks.yml",
      "max_age_hours": 30}]

``max_age_hours`` should be roughly 1.5x the cron period: GitHub's scheduler
is best-effort and routinely runs late under load, so a window equal to the
period produces false alarms. Left out, it is 30.

A workflow with no scheduled runs at all is not assessed and raises nothing.
Mirrors ``data_freshness_probe``'s zero-rows rule: an operator who never
enabled a cron gets no alarms about it.

When the watch list itself cannot be used
-----------------------------------------
Only an empty value, ``''`` or ``[]`` (the OSS default), means "not
configured": ok, no page. A value that is set but cannot be used as written
pages once per episode (``failure_episode:_config``, the same mechanism as a
repo, below) and reports ok=False:

* invalid JSON, or JSON that is not a list: nothing is watched;
* a list with entries the watchdog must ignore: a repo that is not
  ``owner/name``, a workflow that is not a bare ``.yml``/``.yaml`` file name,
  a ``max_age_hours`` that is not a finite number above 0, or a repeat of an
  earlier entry. The page names each one and why. The valid entries are still
  checked. Every entry is either watched or named in a page, never dropped
  unreported.

The page repeats when the problem changes (an edit that moves a JSON error,
or changes any ignored entry), when it reached no channel, and on the
reminder. One recovery note follows once the list is usable again, or
emptied. The parse runs every brain cycle but stays silent; the WARNING is
logged once per real pass, and a throttled cycle reports that verdict.

Until 2026-09-28 all of these read as "no workflows configured", ok, with a
WARNING per dropped entry on every 5-minute cycle and no page. The watchdog
watched nothing, or less than the operator believed, and reported healthy:
the gh_token incident's failure class, one layer up. A failed read of the
setting read the same way; it now reports ok=False.

When the watchdog itself cannot read the runs
---------------------------------------------
A dead-man's switch that cannot see is the failure it exists to catch, so it
says so, once per failure episode (``brain/failure_episode.py``, shared with
the branch-drift canary and the PR staleness probe). Episodes are kept per
repo in ``brain_knowledge``, so a brain restart does not page again.

* LOUD failures page when the episode opens, because only the operator can
  fix them: a ``gh_token`` GitHub rejects (401), one it forbids (a 403 that is
  not a rate limit, usually a token without Actions (read)), one that cannot
  see the private repo, a missing token or httpx, a redirect (the repo moved),
  and a watched workflow that does not exist. They page again when the
  failure changes, when a replaced ``gh_token`` fails too, when the last page
  reached no channel, and every ``scheduled_workflow_watch_failure_repage_hours``.
* QUIET failures (5xx, timeouts, DNS, rate limits) stay in the log and
  audit_log unless they last
  ``scheduled_workflow_watch_transient_failure_page_hours`` without a break.

One recovery note follows on the first clean pass after a page. A repo taken
out of ``scheduled_workflows`` is never checked again, so its open episode is
closed when the list stops naming it validly, with one closing note if it
paged. A value that is not a list closes nothing, so a JSON typo does not end
every episode at once. That sweep only closes repo-shaped episodes, never the
watch list's own.

A 404 means two different things on this endpoint. GitHub answers 404, not
403, for a private repo the token cannot see, and it also answers 404 for a
workflow file name that does not exist. The other workflows watched in the
same repo tell them apart. When every one of them 404s, the token is the
likely problem, and the page names both causes. When some 404 while others
answer, the token can see the repo, so those names are wrong. A pass where
some 404 and none answer (the rest failing transiently) proves neither, so
it is treated as transient until GitHub answers the rest.

Until 2026-09-25 each of these failures only left the target "not assessed"
with a WARNING log, and a pass that assessed nothing reported ok. From
2026-09-23 23:13 UTC the replaced ``gh_token`` could not see the repo, so
every workflow 404'd on every hourly pass, and every brain cycle since
reported the watchdog ok. The last cycle to report a problem was 22:51 UTC,
while ``playwright-e2e`` was still visibly stale. A pass that assesses
nothing, or cannot check every workflow, now reports ok=False.

Throttled to ``scheduled_workflow_watch_interval_minutes`` (default 60)
rather than running on every 5-minute brain cycle: each target costs two
GitHub API calls and nothing here changes minute to minute. A throttled cycle
reports the verdict of the last real pass, which is kept in
``brain_knowledge``, so a failing watchdog does not read as healthy on the
eleven cycles in twelve that skip GitHub. With no verdict recorded yet (the
first cycle after an upgrade, or a lost row), the cycle runs a real pass
instead of reporting health nobody measured.

Standalone — stdlib + asyncpg + httpx (asyncpg pool injected by the daemon).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover — brain ships httpx; degrade loudly
    httpx = None  # type: ignore[assignment]

from poindexter.brain import failure_episode
from poindexter.brain.config_value import (
    Ignored,
    ValueProblem,
    clip,
    digest,
    load_list,
    show,
)
from poindexter.brain.github_errors import TOKEN_FIX, GitHubAPIError, github_message
from poindexter.brain.operator_notifier import notify_operator
from poindexter.brain.secret_reader import read_app_setting as _shared_read_app_setting

logger = logging.getLogger(__name__)

ENABLED_SETTING_KEY = "scheduled_workflow_watch_enabled"
WATCHES_SETTING_KEY = "scheduled_workflows"
INTERVAL_SETTING_KEY = "scheduled_workflow_watch_interval_minutes"
FAILURE_REPAGE_HOURS_KEY = "scheduled_workflow_watch_failure_repage_hours"
TRANSIENT_FAILURE_PAGE_HOURS_KEY = "scheduled_workflow_watch_transient_failure_page_hours"
TOKEN_SETTING_KEY = "gh_token"
_WATCHES_REF = f"app_settings.{WATCHES_SETTING_KEY}"

DEFAULT_INTERVAL_MINUTES = 60.0
# Hours between reminders while the watchdog keeps failing (0 = never remind).
DEFAULT_FAILURE_REPAGE_HOURS = 24
# Hours a transient failure must last, unbroken, before the watchdog pages that
# it is blind (0 = never). The same as the branch-drift canary. A blind spell
# only delays detection: the first clean pass reads every workflow's latest
# run afresh, so nothing that went stale meanwhile is lost.
DEFAULT_TRANSIENT_FAILURE_PAGE_HOURS = 6

_STATE_ENTITY_PREFIX = "scheduled_workflow_watchdog"
_LAST_RUN_ENTITY = f"{_STATE_ENTITY_PREFIX}:_last_checked"
# The last real pass's verdict, which the throttled cycles after it report.
_LAST_PASS_NAME = "_last_pass"

# owner/name — GitHub's own allowed character set for both halves.
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
# A workflow FILENAME, not a path: the API takes the basename.
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")

_HTTP_TIMEOUT = 20.0
# GitHubAPIError.endpoint for the one GitHub call this watchdog makes.
_ENDPOINT = "workflow-runs"


async def _read_setting(pool: Any, key: str, default: str = "") -> str:
    try:
        row = await pool.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sched_wf] setting read for %s failed: %s", key, exc)
        return default
    if not row or row["value"] is None:
        return default
    return str(row["value"])


async def _read_hours(pool: Any, key: str, default: int) -> int:
    """A whole number of hours; a negative one means 0 (never)."""
    raw = await _read_setting(pool, key, str(default))
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        logger.warning(
            "[sched_wf] %s=%r is not a whole number of hours, using %d",
            key, raw, default,
        )
        return default


async def _read_token(pool: Any) -> str:
    val = await _shared_read_app_setting(pool, TOKEN_SETTING_KEY, default="")
    if val:
        return val
    return os.getenv("GITHUB_TOKEN", "").strip()


# ---------------------------------------------------------------------------
# The watch list: what scheduled_workflows holds, and what can be used of it.
# ---------------------------------------------------------------------------

# The window an entry gets when it leaves max_age_hours out.
_DEFAULT_MAX_AGE_HOURS = 30.0
# How the operator replaces the watch list; quoted by every config page.
_CONFIG_FIX = f"`poindexter settings set {WATCHES_SETTING_KEY} '<json>'`"
_ENTRY_SHAPE = (
    '{"repo": "<owner>/<name>", "workflow": "<file>.yml", "max_age_hours": <hours>}'
)
# What each kind of ignored entry is called in the one-line pass detail.
_FIELD_LABEL = {
    "entry": "not an object",
    "repo": "repo",
    "workflow": "workflow",
    "max_age_hours": "max_age_hours",
    "duplicate": "duplicate",
}


@dataclass(frozen=True)
class _WatchList:
    """What ``app_settings.scheduled_workflows`` holds, and what the watchdog can use.

    ``signature`` is None when the value is usable as written: empty (the OSS
    default, so nothing is configured) or a list whose every entry is valid.
    Otherwise it is the config failure episode's identity.
    """

    watches: list[dict[str, Any]]  # the valid entries, in list order
    ignored: tuple[Ignored, ...] = ()  # the entries it cannot use; field is a _FIELD_LABEL key
    total: int = 0  # entries in the list; 0 when the value is not a list
    signature: str | None = None
    summary: str = ""  # the problem in one line, for the pass detail
    problem: str = ""  # a value that is not a list: what the page says is wrong
    hint: str = ""  # ...and how that usually happens

    @property
    def empty(self) -> bool:
        """'' or [], the OSS default: not configured, so nothing to report."""
        return self.signature is None and not self.watches

    @property
    def detail(self) -> str:
        """The problem in full, every ignored entry included, for logs and audit_log."""
        if not self.ignored:
            return self.summary
        return (
            f"{len(self.ignored)} of {self.total} entries in {_WATCHES_REF} are "
            f"ignored: " + "; ".join(ig.reason for ig in self.ignored)
        )


def _hours(value: Any) -> float | None:
    """A usable ``max_age_hours``: a finite number above 0. A numeric string counts.

    Infinity and NaN were accepted until 2026-09-28. Neither ever compares
    below a workflow's age, so the workflow could never go stale: watched on
    paper, unwatched in fact.
    """
    if isinstance(value, bool):
        return None  # float(True) is 1.0, and a JSON true is not one hour
    try:
        hours = float(value)
    except (TypeError, ValueError, OverflowError):  # OverflowError: a 400-digit integer
        return None
    return hours if math.isfinite(hours) and hours > 0 else None


def _check_entry(
    index: int, entry: Any, first: dict[tuple[str, str], tuple[int, float]],
) -> dict[str, Any] | Ignored:
    """Return ``entry`` as a watch, or say why it cannot be one."""
    if not isinstance(entry, dict):
        return Ignored(index, "entry", f"entry {index} is not an object: {show(entry)}", entry)
    repo = str(entry.get("repo", "")).strip()
    if not _REPO_RE.match(repo):
        why = (
            f"entry {index} has no repo" if "repo" not in entry
            else f"entry {index}: repo must be <owner>/<name>, got {show(entry['repo'])}"
        )
        return Ignored(index, "repo", why, entry)
    workflow = str(entry.get("workflow", "")).strip()
    if not _WORKFLOW_RE.match(workflow):
        why = (
            f"entry {index} ({repo}) has no workflow" if "workflow" not in entry
            else (
                f"entry {index} ({repo}): workflow must be a bare .yml or .yaml "
                f"file name, got {show(entry['workflow'])}"
            )
        )
        return Ignored(index, "workflow", why, entry)
    raw_age = entry.get("max_age_hours", _DEFAULT_MAX_AGE_HOURS)
    max_age = _hours(raw_age)
    if max_age is None:
        return Ignored(index, "max_age_hours", (
            f"entry {index} ({repo}, {workflow}): max_age_hours must be a number "
            f"of hours above 0, got {show(raw_age)}"
        ), entry)
    seen = first.get((repo, workflow))
    if seen is not None:
        # Watched twice, the workflow would be checked and counted twice.
        was, was_age = seen
        if max_age == was_age:
            why = (
                f"repeats entry {was} and adds nothing; if it was meant for "
                f"another workflow, fix its name"
            )
        else:
            why = (
                f"repeats entry {was} with max_age_hours {max_age:g}, not "
                f"{was_age:g}; only entry {was}'s window is used"
            )
        return Ignored(index, "duplicate", f"entry {index} ({repo}, {workflow}): {why}", entry)
    first[(repo, workflow)] = (index, max_age)
    return {"repo": repo, "workflow": workflow, "max_age_hours": max_age}


def _parse_watch_list(raw: str) -> _WatchList:
    """Read ``scheduled_workflows``. Pure: logs nothing, never raises.

    It runs on every brain cycle, ahead of the throttle, so it stays quiet.
    What it finds is logged and paged by :func:`_check_watch_list`, once per
    real pass. Until 2026-09-28 this logged a WARNING per dropped entry on
    every 5-minute cycle and paged nothing, and a value that did not parse,
    or held no valid entry, read as "no workflows configured", ok.

    Every entry of a list ends up either watched or ignored with a reason,
    never dropped unreported.
    """
    if not raw.strip():
        return _WatchList(watches=[])
    parsed = load_list(raw, _WATCHES_REF, noun="workflow")
    if isinstance(parsed, ValueProblem):
        return _WatchList(
            watches=[],
            signature=f"config:{parsed.kind}",
            summary=parsed.summary,
            problem=parsed.problem,
            hint=parsed.hint,
        )
    watches: list[dict[str, Any]] = []
    ignored: list[Ignored] = []
    first: dict[tuple[str, str], tuple[int, float]] = {}
    for index, entry in enumerate(parsed, start=1):
        checked = _check_entry(index, entry, first)
        if isinstance(checked, Ignored):
            ignored.append(checked)
        else:
            watches.append(checked)
    total = len(parsed)
    if not ignored:
        return _WatchList(watches=watches, total=total)
    which = "; ".join(f"entry {ig.index}: {_FIELD_LABEL[ig.field]}" for ig in ignored)
    if watches:
        # Watching some of the list and watching none of it are different
        # failures, so a list that slides from one to the other is news.
        signature = f"config:invalid-entries:{len(ignored)}:{digest(ignored)}"
        summary = f"{len(ignored)} of {total} entries in {_WATCHES_REF} ignored ({which})"
    else:
        signature = f"config:all-entries-invalid:{total}:{digest(ignored)}"
        whole = "the only entry" if total == 1 else f"all {total} entries"
        summary = f"{whole} in {_WATCHES_REF} ignored ({which})"
    return _WatchList(
        watches=watches, ignored=tuple(ignored), total=total,
        signature=signature, summary=summary,
    )


def _target_name(watch: dict[str, Any]) -> str:
    return f"{watch['repo']}:{watch['workflow']}"


def _parse_iso8601_utc(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


async def _fetch_runs(
    client: Any, repo: str, workflow: str, *, only_success: bool
) -> tuple[int, datetime | None]:
    """Return ``(total_count, newest_created_at)`` for SCHEDULED runs.

    ``event=schedule`` is not optional — see the module docstring. Raises
    :class:`GitHubAPIError` on any non-200, so the caller can diagnose the
    failure (a bad token, a missing workflow, a GitHub outage) rather than
    inventing a verdict.
    """
    params: dict[str, Any] = {"event": "schedule", "per_page": 1}
    if only_success:
        params["status"] = "success"
    r = await client.get(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs",
        params=params,
    )
    if r.status_code != 200:
        raise GitHubAPIError.from_response(_ENDPOINT, r, ref=workflow)
    payload = r.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"non-object payload: {type(payload).__name__}")
    runs = payload.get("workflow_runs") or []
    newest = _parse_iso8601_utc(runs[0].get("created_at")) if runs else None
    return int(payload.get("total_count", 0)), newest


def _describe_error(exc: BaseException) -> str:
    """One line naming why a workflow could not be checked."""
    if isinstance(exc, GitHubAPIError):
        if exc.status_code >= 500:  # the body is GitHub's HTML error page
            return f"GitHub /{exc.endpoint} returned {exc.status_code}"
        return (
            f"GitHub /{exc.endpoint} returned {exc.status_code}: "
            f"{github_message(exc.body)}"
        )
    # str() of an httpx timeout is the EMPTY string; name the class alone.
    msg = str(exc).strip()
    return f"{type(exc).__name__}: {msg[:200]}" if msg else type(exc).__name__


async def _read_prev_state(pool: Any, name: str) -> str | None:
    try:
        row = await pool.fetchrow(
            "SELECT value FROM brain_knowledge "
            "WHERE entity = $1 AND attribute = 'last_state'",
            f"{_STATE_ENTITY_PREFIX}:{name}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sched_wf] prev-state read for %s failed (%s) — treating as no-prior",
            name, exc,
        )
        return None
    return row["value"] if row else None


async def _write_state(pool: Any, name: str, state: str) -> None:
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ($1, 'last_state', $2, 1.0, 'scheduled_workflow_watch')
            ON CONFLICT (entity, attribute)
              DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            f"{_STATE_ENTITY_PREFIX}:{name}", state,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sched_wf] state write for %s failed: %s — may re-emit next cycle",
            name, exc,
        )


async def _should_run(pool: Any, interval_minutes: float, now_utc: datetime) -> bool:
    """Throttle: each pass costs 2 API calls per target."""
    if interval_minutes <= 0:
        return True
    try:
        row = await pool.fetchrow(
            "SELECT value FROM brain_knowledge "
            "WHERE entity = $1 AND attribute = 'last_state'",
            _LAST_RUN_ENTITY,
        )
    except Exception as exc:  # noqa: BLE001
        # Fail OPEN (run anyway) — but a persistently failing throttle read
        # means every brain cycle spends 2 API calls per target, so it must
        # not pass in silence.
        logger.warning(
            "[sched_wf] throttle read failed (%s) — running unthrottled "
            "this cycle", exc,
        )
        return True
    last = _parse_iso8601_utc(row["value"]) if row else None
    if last is None:
        return True
    age_min = (now_utc - last).total_seconds() / 60.0
    return age_min >= interval_minutes


async def _stamp_run(pool: Any, now_utc: datetime) -> None:
    await _write_state(pool, "_last_checked", now_utc.isoformat())


async def _record_last_pass(
    pool: Any, *, ok: bool, detail: str, now_utc: datetime
) -> None:
    await _write_state(
        pool,
        _LAST_PASS_NAME,
        json.dumps({"ok": ok, "detail": detail[:500], "at": now_utc.isoformat()}),
    )


async def _read_last_pass(pool: Any) -> dict[str, Any] | None:
    raw = await _read_prev_state(pool, _LAST_PASS_NAME)
    if not raw:
        return None
    try:
        last = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(last, dict) or not isinstance(last.get("ok"), bool):
        return None
    return last


def _throttled_summary(last: dict[str, Any], interval_minutes: float) -> dict[str, Any]:
    """A cycle that skips GitHub reports the last real pass's verdict.

    Before 2026-09-25 it always reported ok. The brain heartbeat showed the
    result: while ``playwright-e2e`` was stale, one cycle an hour read
    "issue" and the other eleven "ok", and a blind watchdog read "ok" on all
    twelve. The caller measures instead when there is no verdict to report.
    """
    detail = f"throttled ({interval_minutes:.0f}m)"
    if last["ok"]:
        return {"ok": True, "detail": detail, "workflows": {}}
    return {
        "ok": False,
        "detail": f"{detail}; last pass: {last.get('detail') or 'failed'}",
        "workflows": {},
    }


async def _emit_finding(
    pool: Any, watch: dict[str, Any], mode: str, detail: str, extra: dict[str, Any]
) -> None:
    name = _target_name(watch)
    if mode == "never_green":
        title = f"Scheduled workflow has NEVER succeeded: {name}"
        body = (
            f"{detail} This workflow is on a timer, so nothing goes red when it "
            f"fails — it has been consuming runner minutes and producing no "
            f"usable signal. Check its most recent run, and either fix it or "
            f"remove the schedule. Tune or drop this watch via "
            f"app_settings.{WATCHES_SETTING_KEY}."
        )
    else:
        title = f"Scheduled workflow stale: {name}"
        body = (
            f"{detail} Either the cron stopped firing or every run since has "
            f"failed. Because it is scheduled rather than PR-triggered, no "
            f"check anywhere turned red. Tune or drop this watch via "
            f"app_settings.{WATCHES_SETTING_KEY}."
        )
    details = {
        "kind": "scheduled_workflow_stale",
        "title": title,
        "body": body,
        # Keyed by target, not by mode: a target that slides never_green ->
        # stale (or back) is one ongoing problem, not two.
        "dedup_key": f"scheduled_workflow_stale:{name}",
        "extra": {"repo": watch["repo"], "workflow": watch["workflow"],
                  "mode": mode, **extra},
    }
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ('finding', 'scheduled_workflow_watch', $1::jsonb, 'warn')",
            json.dumps(details),
        )
        logger.warning("[sched_wf] %s — finding emitted", title)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sched_wf] finding insert failed: %s", exc)


async def _assess(
    pool: Any, client: Any, watch: dict[str, Any], now_utc: datetime
) -> tuple[dict[str, Any], BaseException | None]:
    """Assess one workflow. Never raises.

    Returns ``(result, error)``. ``error`` is the exception when GitHub did not
    answer, left for the caller to diagnose per repo: a token that cannot see
    the repo is one failure, not one per workflow. It is None when GitHub
    answered.
    """
    name = _target_name(watch)
    try:
        total, _ = await _fetch_runs(
            client, watch["repo"], watch["workflow"], only_success=False
        )
        if total == 0:
            # Never scheduled-fired here (fresh install, or the operator
            # removed the cron). Not an alert condition.
            return {"state": "not_assessed", "reason": "no scheduled runs"}, None
        _, last_success = await _fetch_runs(
            client, watch["repo"], watch["workflow"], only_success=True
        )
    except Exception as exc:  # noqa: BLE001
        reason = _describe_error(exc)
        logger.warning("[sched_wf] %s not assessed: %s", name, reason)
        return {"state": "not_assessed", "reason": reason[:160]}, exc

    extra: dict[str, Any]
    if last_success is None:
        mode, is_bad = "never_green", True
        age_h: float | None = None
        detail = f"{total} scheduled run(s), none successful."
        extra = {"scheduled_runs": total, "successful_runs": 0}
    else:
        age_h = (now_utc - last_success).total_seconds() / 3600.0
        is_bad = age_h > watch["max_age_hours"]
        mode = "stale"
        detail = (
            f"Last successful scheduled run was {age_h:.0f}h ago "
            f"(window {watch['max_age_hours']:.0f}h)."
        )
        extra = {
            "age_hours": round(age_h, 1),
            "max_age_hours": watch["max_age_hours"],
            "last_success": last_success.isoformat(),
        }

    new_state = mode if is_bad else "ok"
    prev_state = await _read_prev_state(pool, name)

    # Edge-triggered: one finding per episode. prev=None + bad still emits so
    # something already dead at brain boot gets surfaced once.
    if is_bad and prev_state != new_state:
        await _emit_finding(pool, watch, mode, detail, extra)
    elif not is_bad and prev_state in ("stale", "never_green"):
        logger.info("[sched_wf] %s recovered (%s)", name, detail)
    if new_state != prev_state:
        await _write_state(pool, name, new_state)

    result: dict[str, Any] = {"state": new_state}
    if age_h is not None:
        result["age_hours"] = round(age_h, 1)
    result.update(extra)
    return result, None


# ---------------------------------------------------------------------------
# When the watchdog itself cannot read the runs: diagnose once per repo, and
# page once per failure episode (brain/failure_episode.py).
# ---------------------------------------------------------------------------

FAILURE_STATE_ENTITY = "scheduled_workflow_watch"
_SOURCE = "brain.scheduled_workflow_watch"
# What the watchdog needs the gh_token to grant, quoted by every credential page.
_NEEDS = "Actions (read)"


@dataclass(frozen=True)
class _Failure:
    """Why the watchdog could not check some of one repo's workflows."""

    signature: str  # the failure's identity in the episode
    detail: str  # operator-facing: what happened and what to do about it
    loud: bool  # only the operator can fix it, so it pages when it is news
    affected: tuple[str, ...]  # the watched workflow files it covers


def _failure_key(repo: str) -> failure_episode.EpisodeKey:
    """Where the open failure episode for ``repo`` lives in brain_knowledge."""
    return failure_episode.EpisodeKey(
        entity=FAILURE_STATE_ENTITY,
        attribute=f"failure_episode:{repo}",
        label="sched_wf",
    )


# The episode for a watch list the watchdog cannot use. "_config" is not
# repo-shaped, so the sweep that closes unwatched repos' episodes
# (_open_episode_repos) never touches it.
_CONFIG_KEY = failure_episode.EpisodeKey(
    entity=FAILURE_STATE_ENTITY,
    attribute="failure_episode:_config",
    label="sched_wf",
)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _token_needs(repo: str) -> str:
    return (
        f"The scheduled-CI watchdog needs {_NEEDS} on {repo}: a fine-grained "
        f"token with {repo} in its repository access and that permission, or "
        f"the classic `repo` scope."
    )


def _no_token_detail(repo: str, n_targets: int) -> str:
    return (
        f"gh_token is not set, so the scheduled-CI watchdog cannot read the "
        f"workflow runs of {repo} ({_plural(n_targets, 'workflow')} watched in "
        f"{_WATCHES_REF}). {_token_needs(repo)} Set it with {TOKEN_FIX}."
    )


def _no_httpx_detail(repo: str, n_targets: int) -> str:
    return (
        f"httpx is not installed in the brain image, so the scheduled-CI "
        f"watchdog cannot query GitHub for the {_plural(n_targets, 'workflow')} "
        f"it watches in {repo}. Rebuild the brain image."
    )


def _classify_repo_failure(
    repo: str,
    failed: list[tuple[str, BaseException]],
    *,
    n_targets: int,
    retry_minutes: int,
) -> _Failure:
    """Diagnose one pass's failures for ``repo`` as a single failure.

    ``failed`` holds ``(workflow, exception)`` for each watched workflow in the
    repo that GitHub did not answer; the other ``n_targets - len(failed)``
    answered. One diagnosis per repo, so a token that cannot see the repo is
    one page, not one per workflow.

    LOUD, in priority order: 401; a 403 that is not a rate limit; a 404 on
    every watched workflow (the token cannot see the repo); a 404 on some
    while others answered (those workflow names are wrong); a redirect; any
    other non-transient status. QUIET: a rate limit, a 5xx, and failures that
    are not an HTTP answer at all. A 404 alongside only transient failures,
    with nothing answering, proves neither cause, so it rides the quiet
    diagnosis until GitHub answers the rest.
    """
    n_answered = n_targets - len(failed)
    api = [(wf, e) for wf, e in failed if isinstance(e, GitHubAPIError)]
    everything = tuple(wf for wf, _ in failed)
    retry = f"The watchdog retries every {retry_minutes} min."

    def _first(pred: Callable[[GitHubAPIError], bool]) -> GitHubAPIError | None:
        return next((e for _, e in api if pred(e)), None)

    hit = _first(lambda e: e.status_code == 401)
    if hit:
        return _Failure(f"{_ENDPOINT}:401", (
            f"GitHub rejected the gh_token (HTTP 401: {github_message(hit.body)}). "
            f"It is invalid, expired or revoked. {_token_needs(repo)} Replace it "
            f"with {TOKEN_FIX}."
        ), True, everything)

    hit = _first(lambda e: e.status_code == 403 and not e.rate_limited)
    if hit:
        return _Failure(f"{_ENDPOINT}:403", (
            f"The gh_token may not list the workflow runs of {repo} (HTTP 403: "
            f"{github_message(hit.body)}), check its scopes. The scheduled-CI "
            f"watchdog needs {_NEEDS} on {repo}: on a fine-grained token that is "
            f"a permission of its own, which Contents (read) does not include. "
            f"If the organization enforces SAML single sign-on, the token must "
            f"also be authorized for it. Rotate it with {TOKEN_FIX}."
        ), True, everything)

    not_found = sorted(wf for wf, e in api if e.status_code == 404)
    if not_found and len(not_found) == n_targets:
        if n_targets == 1:
            wf = not_found[0]
            detail = (
                f"GitHub answered 404 for {wf} in {repo}. Either the gh_token "
                f"cannot see {repo} (GitHub answers 404, not 403, for a private "
                f"repo the token has no access to), or {repo} has no workflow "
                f"file named {wf}: renamed, deleted, or misspelled in "
                f"{_WATCHES_REF}. It is the only workflow watched there, so the "
                f"watchdog cannot tell which. If the name is right, check the "
                f"token's scopes. {_token_needs(repo)} Rotate it with {TOKEN_FIX}."
            )
        else:
            detail = (
                f"GitHub answered 404 for all {n_targets} watched workflows in "
                f"{repo}, so the gh_token cannot see the repo, check its scopes. "
                f"GitHub answers 404, not 403, for a private repo the token has "
                f"no access to, and a wrong workflow name would not take every "
                f"workflow down at once. Unless {repo} itself is misspelled in "
                f"{_WATCHES_REF}, the token is the problem. {_token_needs(repo)} "
                f"Rotate it with {TOKEN_FIX}."
            )
        return _Failure(f"{_ENDPOINT}:404", detail, True, everything)
    if not_found and n_answered:
        one = len(not_found) == 1
        detail = (
            f"GitHub answered 404 for {', '.join(not_found)} in {repo}, while "
            f"{_plural(n_answered, 'other watched workflow')} there answered. So "
            f"the gh_token can see {repo}, and there is no workflow file by "
            f"{'that name' if one else 'those names'}: renamed, deleted, or "
            f"misspelled in {_WATCHES_REF}. Fix or remove the "
            f"{'entry' if one else 'entries'}; `workflow` is the file name under "
            f".github/workflows/."
        )
        return _Failure(
            f"{_ENDPOINT}:404:{','.join(not_found)}", detail, True, tuple(not_found),
        )

    hit = _first(lambda e: 300 <= e.status_code < 400)
    if hit:
        return _Failure(f"{_ENDPOINT}:3xx", (
            f"GitHub redirected /{_ENDPOINT} for {repo} (HTTP {hit.status_code}), "
            f"so the repo was renamed or transferred. Set its new owner/name in "
            f"{_WATCHES_REF}."
        ), True, everything)

    hit = _first(lambda e: not e.transient and e.status_code not in (401, 403, 404))
    if hit:
        same = tuple(sorted(wf for wf, e in api if e.status_code == hit.status_code))
        return _Failure(f"{_ENDPOINT}:{hit.status_code}", (
            f"GitHub /{_ENDPOINT} for {repo} returned HTTP {hit.status_code} for "
            f"{', '.join(same)}: {github_message(hit.body)}. A retry will not "
            f"change that answer. Check {_WATCHES_REF} and the gh_token."
        ), True, same)

    # Transient from here on. Say how much of the repo went unchecked, and
    # hold any 404 back until GitHub answers the rest.
    tail = (
        f" {len(failed)} of {_plural(n_targets, 'watched workflow')} in {repo} "
        f"could not be checked this pass."
    )
    if not_found:
        tail += (
            f" {len(not_found)} of them answered 404, which is diagnosed once "
            f"GitHub answers the rest."
        )

    hit = _first(lambda e: e.rate_limited)
    if hit:
        return _Failure(f"{_ENDPOINT}:rate-limited", (
            f"GitHub rate-limited the scheduled-CI watchdog on /{_ENDPOINT} for "
            f"{repo} (HTTP {hit.status_code}: {github_message(hit.body)}). "
            f"{retry} If this persists, another gh_token consumer is spending "
            f"the budget." + tail
        ), False, everything)

    hit = _first(lambda e: e.status_code >= 500)
    if hit:
        return _Failure(f"{_ENDPOINT}:5xx", (
            f"GitHub /{_ENDPOINT} for {repo} returned {hit.status_code}, a "
            f"GitHub-side error. {retry}" + tail
        ), False, everything)

    # Left: failures that are not an HTTP answer at all (a timeout, DNS, a
    # reset connection, a malformed payload). Only-404 cannot reach here: with
    # no other failure and no answer, every workflow 404'd (handled above).
    other = next(
        (e for _, e in failed if not isinstance(e, GitHubAPIError)), failed[0][1],
    )
    name = type(other).__name__
    return _Failure(name, (
        f"{_describe_error(other)}. The watchdog's GitHub round-trip for {repo} "
        f"did not complete, usually a network or GitHub blip. {retry}" + tail
    ), False, everything)


def _quiet_page_after(config: dict[str, Any]) -> timedelta | None:
    hours = int(config["transient_failure_page_hours"])
    return timedelta(hours=hours) if hours > 0 else None


def _retry_minutes(config: dict[str, Any]) -> int:
    return max(1, int(config["interval_minutes"]))


def _build_failure_page(
    *,
    repo: str,
    reason: str,
    failure: _Failure,
    n_targets: int,
    episode: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str, str]:
    """Render ``(title, body)`` for a failure page."""
    n = len(failure.affected)
    if n >= n_targets:
        subject = repo
        consequence = (
            f"Until it can, nothing notices a scheduled workflow in {repo} that "
            f"stops firing or never passes."
        )
    else:
        subject = f"{failure.affected[0]} in {repo}" if n == 1 else f"{n} workflows in {repo}"
        consequence = (
            f"Until then, {', '.join(failure.affected)} "
            f"{'is' if n == 1 else 'are'} unwatched."
        )
    if reason == failure_episode.PAGE_REMINDER:
        title = f"Scheduled-CI watchdog still cannot check {subject}"
    else:
        title = f"Scheduled-CI watchdog cannot check {subject}"
    lines = [failure.detail, "", consequence]
    lines += failure_episode.episode_lines(
        episode,
        reason=reason,
        retry_minutes=_retry_minutes(config),
        repage_hours=int(config["failure_repage_hours"]),
        repage_setting_key=FAILURE_REPAGE_HOURS_KEY,
        credential_key=TOKEN_SETTING_KEY,
        quiet_page_after=_quiet_page_after(config),
    )
    return title, "\n".join(lines)


async def _emit_audit_event(
    pool: Any,
    event: str,
    detail: str,
    *,
    extra: dict[str, Any] | None = None,
    severity: str = "info",
) -> None:
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ($1, $2, $3::jsonb, $4)",
            event, _SOURCE, json.dumps(payload), severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sched_wf] audit_log insert for %s failed: %s", event, exc)


async def _handle_repo_failure(
    pool: Any,
    *,
    repo: str,
    failure: _Failure,
    n_targets: int,
    now_utc: datetime,
    config: dict[str, Any],
    notify_fn: Callable[..., Any],
) -> dict[str, Any]:
    """Record a failed pass for ``repo``; page only when the episode says it is news."""
    logger.warning(
        "[sched_wf] cannot check %s (%s, %s): %s",
        repo, failure.signature, "loud" if failure.loud else "transient",
        failure.detail,
    )
    outcome = await failure_episode.record_failure(
        pool,
        _failure_key(repo),
        signature=failure.signature,
        detail=failure.detail,
        now_utc=now_utc,
        notify_fn=notify_fn,
        render=lambda episode, reason: _build_failure_page(
            repo=repo, reason=reason, failure=failure, n_targets=n_targets,
            episode=episode, config=config,
        ),
        source=_SOURCE,
        dedup_prefix=f"scheduled_workflow_watch_failed:{repo}",
        loud=failure.loud,
        quiet_page_after=_quiet_page_after(config),
        repage_hours=int(config["failure_repage_hours"]),
        credential_key=TOKEN_SETTING_KEY,
    )
    episode = outcome.episode
    summary = {
        "signature": failure.signature,
        "transient": not failure.loud,
        "workflows": list(failure.affected),
        "failed_attempts": episode.get("attempts"),
        "failing_since": episode.get("since"),
        "page_reason": outcome.reason,
        "paged": outcome.paged,
    }
    await _emit_audit_event(
        pool,
        "probe.scheduled_workflow_watch_failed",
        failure.detail,
        extra={"repo": repo, **summary},
        severity="warning",
    )
    return {**summary, "detail": failure.detail}


async def _close_repo_episode(
    pool: Any,
    *,
    repo: str,
    config: dict[str, Any],
    notify_fn: Callable[..., Any],
) -> None:
    """End an open failure episode after a pass that checked all of ``repo``.

    Sends one recovery note, and only when the episode reached the operator.
    An episode nobody was told about has nothing to take back.
    """
    episode = await failure_episode.close_episode(pool, _failure_key(repo))
    if not episode:
        return
    note = (
        f"The scheduled-CI watchdog is checking {repo} again "
        f"{failure_episode.recovery_summary(episode)}. A workflow that went "
        f"stale while it could not check is reported on this pass; checks "
        f"resume every {_retry_minutes(config)} min."
    )
    logger.info("[sched_wf] recovered: %s", note)
    await _emit_audit_event(
        pool,
        "probe.scheduled_workflow_watch_recovered",
        note,
        extra={
            "repo": repo,
            "signature": episode.get("signature") or "unknown",
            "attempts": episode.get("attempts"),
            "failing_since": episode.get("since"),
            "was_paged": bool(episode.get("paged_at")),
        },
    )
    if episode.get("paged_at"):
        failure_episode.send_page(
            notify_fn,
            label="sched_wf",
            title=f"Scheduled-CI watchdog checking {repo} again",
            detail=note,
            source=_SOURCE,
            severity="info",
            dedup_key=f"scheduled_workflow_watch_recovered:{repo}",
            if_undelivered="the operator still believes the watchdog is blind",
        )


async def _open_episode_repos(pool: Any) -> list[str]:
    """The repos that have an open failure episode in brain_knowledge."""
    prefix = "failure_episode:"
    try:
        rows = await pool.fetch(
            "SELECT attribute FROM brain_knowledge "
            "WHERE entity = $1 AND attribute LIKE 'failure_episode:%'",
            FAILURE_STATE_ENTITY,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sched_wf] could not list open failure episodes: %s. An episode "
            "for a repo that is no longer watched stays open this pass", exc,
        )
        return []
    repos = [str(r["attribute"])[len(prefix):] for r in rows]
    # Only repo-shaped keys. Anything else in this entity is not ours to close:
    # the watch list's own episode (failure_episode:_config) closes when the
    # list is usable again, with its own note, never because no repo names it.
    return [repo for repo in repos if _REPO_RE.match(repo)]


async def _close_unwatched_episodes(
    pool: Any,
    *,
    watched: set[str],
    notify_fn: Callable[..., Any],
) -> None:
    """Close the failure episode of every repo that is no longer watched.

    Only a clean pass over a repo closes its episode, and a repo removed
    from ``scheduled_workflows`` is never checked again. Without this its
    episode stayed open for good. No closing message reached the operator
    who was paged about it, and a re-added repo that still failed was not
    news until the next reminder. A closing note goes out only when the
    episode paged, the same rule as a recovery note.
    """
    for repo in await _open_episode_repos(pool):
        if repo in watched:
            continue
        episode = await failure_episode.close_episode(pool, _failure_key(repo))
        if not episode:
            continue
        note = (
            f"{repo} is no longer watched: app_settings.{WATCHES_SETTING_KEY} "
            f"has no valid entry for it. Its failure episode is closed "
            f"{failure_episode.recovery_summary(episode)}, and no more pages "
            f"about it will follow."
        )
        logger.info("[sched_wf] %s", note)
        await _emit_audit_event(
            pool,
            "probe.scheduled_workflow_watch_unwatched",
            note,
            extra={
                "repo": repo,
                "signature": episode.get("signature") or "unknown",
                "attempts": episode.get("attempts"),
                "failing_since": episode.get("since"),
                "was_paged": bool(episode.get("paged_at")),
            },
        )
        if episode.get("paged_at"):
            failure_episode.send_page(
                notify_fn,
                label="sched_wf",
                title=f"Scheduled-CI watchdog no longer watching {repo}",
                detail=note,
                source=_SOURCE,
                severity="info",
                dedup_key=f"scheduled_workflow_watch_unwatched:{repo}",
                if_undelivered="the operator still expects pages about that repo",
            )


# ---------------------------------------------------------------------------
# When the watch list itself cannot be used: page once per episode, like a
# repo, under failure_episode:_config.
# ---------------------------------------------------------------------------

# Discord cuts a message at 1,900 characters (operator_notifier._try_discord)
# and the cut falls on the end of the page: the episode lines. So a page names
# at most this many ignored entries, each line clipped; the log has them all.
# 180 keeps the offending value on the line for a repo name of ordinary length
# (the value comes last, so a tighter clip cuts exactly the part that matters).
_MAX_ENTRIES_SHOWN = 5
_ENTRY_LINE_LIMIT = 180


def _entries(n: int) -> str:
    return "1 entry" if n == 1 else f"{n} entries"


def _build_config_page(
    watch_list: _WatchList,
    *,
    reason: str,
    episode: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str, str]:
    """Render ``(title, body)`` for a watch list the watchdog cannot use."""
    still = " still" if reason == failure_episode.PAGE_REMINDER else ""
    ignored = watch_list.ignored
    if watch_list.watches:
        title = f"Scheduled-CI watchdog is{still} ignoring {_entries(len(ignored))} in {_WATCHES_REF}"
        head = (
            f"The scheduled-CI watchdog is ignoring {len(ignored)} of the "
            f"{watch_list.total} entries in {_WATCHES_REF} and watches the other "
            f"{len(watch_list.watches)}:"
        )
        consequence = "Until then, nothing checks what the ignored entries ask for."
    else:
        title = f"Scheduled-CI watchdog{still} cannot use {_WATCHES_REF}"
        if ignored:
            whole = (
                "the only entry" if watch_list.total == 1
                else f"any of the {watch_list.total} entries"
            )
            head = (
                f"The scheduled-CI watchdog cannot use {whole} in {_WATCHES_REF}, "
                f"so it checks no scheduled workflow:"
            )
        else:
            head = (
                f"{watch_list.problem} So the scheduled-CI watchdog has no watch "
                f"list and checks no scheduled workflow."
            )
        consequence = (
            "Until then, nothing notices a scheduled workflow that stops firing "
            "or never passes."
        )
    lines = [head]
    shown = ignored[:_MAX_ENTRIES_SHOWN]
    lines += [f"- {clip(ig.reason, _ENTRY_LINE_LIMIT)}" for ig in shown]
    if len(ignored) > len(shown):
        lines.append(f"- and {len(ignored) - len(shown)} more, listed in the brain log")
    if watch_list.hint:
        lines.append(watch_list.hint)
    if ignored:
        lines.append(
            f"Fix or remove {'it' if len(ignored) == 1 else 'them'} with "
            f"{_CONFIG_FIX}, which replaces the whole list."
        )
    else:
        lines.append(f"Fix it with {_CONFIG_FIX}: a JSON list of {_ENTRY_SHAPE} objects.")
    lines += ["", consequence]
    lines += failure_episode.episode_lines(
        episode,
        reason=reason,
        retry_minutes=_retry_minutes(config),
        repage_hours=int(config["failure_repage_hours"]),
        repage_setting_key=FAILURE_REPAGE_HOURS_KEY,
    )
    return title, "\n".join(lines)


async def _check_watch_list(
    pool: Any,
    watch_list: _WatchList,
    *,
    now_utc: datetime,
    config: dict[str, Any],
    notify_fn: Callable[..., Any],
) -> dict[str, Any] | None:
    """Record a watch list the watchdog cannot use; page when the episode says so.

    Runs on real passes only, so its WARNING is logged once a pass rather than
    on every 5-minute brain cycle. A usable list closes the episode instead.
    Returns the failure's summary, or None when the list is usable.

    Every such failure is LOUD: only the operator can fix the list. There is
    no credential behind it, so ``credential_key`` is None and a rewrite of
    the list is news only when it changes the signature, which covers each
    ignored entry as written.
    """
    if watch_list.signature is None:
        await _close_config_episode(pool, watch_list, notify_fn=notify_fn)
        return None
    detail = watch_list.detail
    logger.warning("[sched_wf] cannot use the watch list as written: %s", detail)
    outcome = await failure_episode.record_failure(
        pool,
        _CONFIG_KEY,
        signature=watch_list.signature,
        detail=detail,
        now_utc=now_utc,
        notify_fn=notify_fn,
        render=lambda episode, reason: _build_config_page(
            watch_list, reason=reason, episode=episode, config=config,
        ),
        source=_SOURCE,
        dedup_prefix="scheduled_workflow_watch_config",
        loud=True,
        repage_hours=int(config["failure_repage_hours"]),
        credential_key=None,
    )
    episode = outcome.episode
    summary = {
        "signature": watch_list.signature,
        "entries": watch_list.total,
        "watched": len(watch_list.watches),
        "ignored": [ig.reason for ig in watch_list.ignored],
        "failed_attempts": episode.get("attempts"),
        "failing_since": episode.get("since"),
        "page_reason": outcome.reason,
        "paged": outcome.paged,
    }
    await _emit_audit_event(
        pool,
        "probe.scheduled_workflow_watch_config_failed",
        detail,
        extra=summary,
        severity="warning",
    )
    return {**summary, "detail": watch_list.summary}


async def _close_config_episode(
    pool: Any, watch_list: _WatchList, *, notify_fn: Callable[..., Any],
) -> None:
    """End the config episode once the list is usable again, or empty.

    One closing note, and only when the episode paged, the rule a repo's
    recovery follows.
    """
    episode = await failure_episode.close_episode(pool, _CONFIG_KEY)
    if not episode:
        return
    since = failure_episode.recovery_summary(episode)
    if watch_list.watches:
        title = f"Scheduled-CI watchdog can use {_WATCHES_REF} again"
        note = (
            f"The scheduled-CI watchdog can use {_WATCHES_REF} again {since}. It "
            f"watches {_plural(len(watch_list.watches), 'workflow')} from this "
            f"pass on."
        )
    else:
        title = "Scheduled-CI watchdog watch list is empty"
        note = (
            f"{_WATCHES_REF} is empty now, so the scheduled-CI watchdog watches "
            f"nothing. Its config failure is closed {since}."
        )
    logger.info("[sched_wf] %s", note)
    await _emit_audit_event(
        pool,
        "probe.scheduled_workflow_watch_config_recovered",
        note,
        extra={
            "signature": episode.get("signature") or "unknown",
            "attempts": episode.get("attempts"),
            "failing_since": episode.get("since"),
            "was_paged": bool(episode.get("paged_at")),
            "watched": len(watch_list.watches),
        },
    )
    if episode.get("paged_at"):
        failure_episode.send_page(
            notify_fn,
            label="sched_wf",
            title=title,
            detail=note,
            source=_SOURCE,
            severity="info",
            dedup_key="scheduled_workflow_watch_config_recovered",
            if_undelivered="the operator still believes the watch list is broken",
        )


async def _read_watch_list_setting(pool: Any) -> str | None:
    """The raw ``scheduled_workflows`` value ('' when unset), or None when the read failed.

    A failed read is not an empty list. Read as one, it reported "no
    workflows configured", ok, and could sweep every open repo episode
    closed (if the sweep's own query got through).
    """
    try:
        row = await pool.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1", WATCHES_SETTING_KEY
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sched_wf] could not read %s: %s", _WATCHES_REF, exc)
        return None
    if not row or row["value"] is None:
        return ""
    return str(row["value"])


# ---------------------------------------------------------------------------
# Top-level entry point.
# ---------------------------------------------------------------------------


def _default_client_factory(token: str) -> Callable[[], Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    def _make() -> Any:
        return httpx.AsyncClient(headers=headers, timeout=_HTTP_TIMEOUT)

    return _make


def _group_by_repo(watches: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_repo: dict[str, list[dict[str, Any]]] = {}
    for watch in watches:
        by_repo.setdefault(watch["repo"], []).append(watch)
    return by_repo


async def _finish_pass(
    pool: Any,
    results: dict[str, dict[str, Any]],
    failures: dict[str, dict[str, Any]],
    now_utc: datetime,
    *,
    config_failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await _stamp_run(pool, now_utc)

    # `not_assessed` is neither health nor alarm — a target the probe could
    # not reach must never be counted as fine. Saying "all N healthy" when
    # zero were actually checked is the precise lie this probe exists to
    # catch, so the summary counts ASSESSED targets, not configured ones.
    n_unassessed = sum(
        1 for r in results.values() if r.get("state") == "not_assessed"
    )
    n_assessed = len(results) - n_unassessed
    bad = [
        name for name, r in results.items()
        if r.get("state") in ("stale", "never_green")
    ]
    if bad:
        detail = (
            f"{len(bad)} of {n_assessed} assessed scheduled workflow(s) "
            f"unhealthy: " + ", ".join(bad)
        )
    elif n_assessed == 0:
        detail = "no scheduled workflow(s) assessed"
    else:
        detail = f"all {n_assessed} assessed scheduled workflow(s) healthy"
    if n_unassessed and n_assessed:
        detail += f" ({n_unassessed} not assessed)"
    if failures:
        detail += "; watchdog failing for " + ", ".join(
            f"{repo} ({failure['signature']})" for repo, failure in failures.items()
        )
    if config_failure:
        detail += f"; {config_failure['detail']}"

    # A pass that checked nothing, could not check every workflow, or had to
    # ignore part of the watch list is not ok: that is how this watchdog read
    # as healthy while blind.
    ok = not bad and n_assessed > 0 and not failures and not config_failure
    await _record_last_pass(pool, ok=ok, detail=detail, now_utc=now_utc)

    # Log EVERY completed pass, healthy or not. A probe that only speaks when
    # something is wrong is indistinguishable from a probe that never ran —
    # the exact failure class this file exists to catch. It bit during this
    # probe's own first-pass verification: the brain log said nothing, and
    # whether it had run had to be dug out of brain_knowledge.
    logger.info(
        "[sched_wf] pass complete — %s (%d assessed, %d not assessed)",
        detail, n_assessed, n_unassessed,
    )
    summary: dict[str, Any] = {
        "ok": ok, "detail": detail, "workflows": results, "failures": failures,
    }
    if config_failure:
        summary["config"] = config_failure
    return summary


async def _fail_every_repo(
    pool: Any,
    by_repo: dict[str, list[dict[str, Any]]],
    *,
    signature: str,
    detail_for: Callable[[str, int], str],
    now_utc: datetime,
    config: dict[str, Any],
    notify_fn: Callable[..., Any],
    config_failure: dict[str, Any] | None,
) -> dict[str, Any]:
    """A pass that could not start (no token, no httpx): every repo fails loud."""
    results: dict[str, dict[str, Any]] = {}
    failures: dict[str, dict[str, Any]] = {}
    for repo, repo_watches in by_repo.items():
        for watch in repo_watches:
            results[_target_name(watch)] = {"state": "not_assessed", "reason": signature}
        failure = _Failure(
            signature,
            detail_for(repo, len(repo_watches)),
            True,
            tuple(w["workflow"] for w in repo_watches),
        )
        failures[repo] = await _handle_repo_failure(
            pool, repo=repo, failure=failure, n_targets=len(repo_watches),
            now_utc=now_utc, config=config, notify_fn=notify_fn,
        )
    return await _finish_pass(
        pool, results, failures, now_utc, config_failure=config_failure,
    )


async def run_scheduled_workflow_watch(
    pool: Any,
    *,
    now_fn: Callable[[], datetime] | None = None,
    notify_fn: Callable[..., Any] | None = None,
    http_client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """One pass over every configured workflow. Never raises.

    ``now_fn``, ``notify_fn`` and ``http_client_factory`` are test seams; the
    daemon passes none of them.
    """
    now_utc = now_fn() if now_fn else datetime.now(UTC)
    notify_fn = notify_fn or notify_operator

    enabled = (await _read_setting(pool, ENABLED_SETTING_KEY, "true")).lower()
    if enabled in ("false", "0", "no", "off"):
        return {"ok": True, "detail": "disabled", "workflows": {}}

    raw_watches = await _read_watch_list_setting(pool)
    if raw_watches is None:
        # Not "no workflows configured": nobody knows what is configured.
        return {"ok": False, "detail": f"could not read {_WATCHES_REF}", "workflows": {}}
    watch_list = _parse_watch_list(raw_watches)
    if watch_list.empty:
        # '' or [], the OSS default. The operator watches nothing, on purpose:
        # close whatever was still open, the list's own episode included.
        await _close_config_episode(pool, watch_list, notify_fn=notify_fn)
        await _close_unwatched_episodes(pool, watched=set(), notify_fn=notify_fn)
        return {"ok": True, "detail": "no workflows configured", "workflows": {}}

    try:
        interval = float(await _read_setting(pool, INTERVAL_SETTING_KEY, "60"))
    except ValueError:
        interval = DEFAULT_INTERVAL_MINUTES
    if not await _should_run(pool, interval, now_utc):
        last = await _read_last_pass(pool)
        if last is not None:
            return _throttled_summary(last, interval)
        # No verdict to report: the first cycle after this code deployed, or
        # the row was lost. Measure now rather than report health nobody
        # measured. On 2026-09-25 that window read "ok" from 23:24 to 23:56
        # UTC while playwright-e2e was known to be stale.

    config = {
        "interval_minutes": interval,
        "failure_repage_hours": await _read_hours(
            pool, FAILURE_REPAGE_HOURS_KEY, DEFAULT_FAILURE_REPAGE_HOURS,
        ),
        "transient_failure_page_hours": await _read_hours(
            pool, TRANSIENT_FAILURE_PAGE_HOURS_KEY, DEFAULT_TRANSIENT_FAILURE_PAGE_HOURS,
        ),
    }
    # Set but not usable as written: say so once per episode, then check
    # whatever part of the list is valid.
    config_failure = await _check_watch_list(
        pool, watch_list, now_utc=now_utc, config=config, notify_fn=notify_fn,
    )
    watches = watch_list.watches
    if not watches:
        # Invalid JSON, not a list, or no entry it can use: nothing to check.
        if watch_list.total:
            # A list, so every repo it no longer validly names is unwatched.
            # A value that is not a list at all closes nothing, so a JSON typo
            # cannot end every open episode at once.
            await _close_unwatched_episodes(pool, watched=set(), notify_fn=notify_fn)
        return await _finish_pass(pool, {}, {}, now_utc, config_failure=config_failure)
    by_repo = _group_by_repo(watches)
    await _close_unwatched_episodes(pool, watched=set(by_repo), notify_fn=notify_fn)

    token = await _read_token(pool)
    if not token:
        # The operator configured watches, so a missing token leaves this
        # dead-man's switch blind: page, once per episode. Without a token
        # every call to a private repo would 404, so none is made.
        return await _fail_every_repo(
            pool, by_repo, signature="no-token", detail_for=_no_token_detail,
            now_utc=now_utc, config=config, notify_fn=notify_fn,
            config_failure=config_failure,
        )
    if http_client_factory is None and httpx is None:
        return await _fail_every_repo(
            pool, by_repo, signature="no-httpx", detail_for=_no_httpx_detail,
            now_utc=now_utc, config=config, notify_fn=notify_fn,
            config_failure=config_failure,
        )

    factory = http_client_factory or _default_client_factory(token)
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, BaseException] = {}
    async with factory() as client:
        for watch in watches:
            name = _target_name(watch)
            results[name], exc = await _assess(pool, client, watch, now_utc)
            if exc is not None:
                errors[name] = exc

    failures: dict[str, dict[str, Any]] = {}
    for repo, repo_watches in by_repo.items():
        failed = [
            (w["workflow"], errors[_target_name(w)])
            for w in repo_watches if _target_name(w) in errors
        ]
        if not failed:
            await _close_repo_episode(pool, repo=repo, config=config, notify_fn=notify_fn)
            continue
        failure = _classify_repo_failure(
            repo, failed, n_targets=len(repo_watches),
            retry_minutes=_retry_minutes(config),
        )
        failures[repo] = await _handle_repo_failure(
            pool, repo=repo, failure=failure, n_targets=len(repo_watches),
            now_utc=now_utc, config=config, notify_fn=notify_fn,
        )
    return await _finish_pass(
        pool, results, failures, now_utc, config_failure=config_failure,
    )
