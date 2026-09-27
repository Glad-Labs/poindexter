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

    [{"repo": "Glad-Labs/poindexter", "workflow": "benchmarks.yml",
      "max_age_hours": 30}]

``max_age_hours`` should be roughly 1.5x the cron period: GitHub's scheduler
is best-effort and routinely runs late under load, so a window equal to the
period produces false alarms.

A workflow with no scheduled runs at all is not assessed and raises nothing.
Mirrors ``data_freshness_probe``'s zero-rows rule: an operator who never
enabled a cron gets no alarms about it.

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

One recovery note follows on the first clean pass after a page.

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
eleven cycles in twelve that skip GitHub.

Standalone — stdlib + asyncpg + httpx (asyncpg pool injected by the daemon).
"""

from __future__ import annotations

import json
import logging
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


def _parse_watches(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[sched_wf] %s is not valid JSON: %s", WATCHES_SETTING_KEY, exc)
        return []
    if not isinstance(parsed, list):
        logger.warning("[sched_wf] %s must be a JSON list", WATCHES_SETTING_KEY)
        return []
    return _validate(parsed)


def _validate(parsed: list[Any]) -> list[dict[str, Any]]:
    """Drop malformed entries loudly rather than letting them 404 silently."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            logger.warning("[sched_wf] skipping non-object entry: %r", entry)
            continue
        repo = str(entry.get("repo", "")).strip()
        workflow = str(entry.get("workflow", "")).strip()
        if not _REPO_RE.match(repo):
            logger.warning("[sched_wf] skipping entry with bad repo: %r", repo)
            continue
        if not _WORKFLOW_RE.match(workflow):
            logger.warning(
                "[sched_wf] skipping %s: workflow must be a bare .yml filename, "
                "got %r", repo, workflow,
            )
            continue
        try:
            max_age = float(entry.get("max_age_hours", 30))
        except (TypeError, ValueError):
            logger.warning("[sched_wf] skipping %s/%s: bad max_age_hours", repo, workflow)
            continue
        if max_age <= 0:
            logger.warning(
                "[sched_wf] skipping %s/%s: max_age_hours must be > 0", repo, workflow
            )
            continue
        if (repo, workflow) in seen:
            # A duplicate would be checked twice and counted twice.
            logger.warning(
                "[sched_wf] skipping duplicate entry for %s/%s", repo, workflow
            )
            continue
        seen.add((repo, workflow))
        out.append({"repo": repo, "workflow": workflow, "max_age_hours": max_age})
    return out


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


async def _throttled_summary(pool: Any, interval_minutes: float) -> dict[str, Any]:
    """A cycle that skips GitHub reports the last real pass's verdict.

    Before 2026-09-25 it always reported ok. The brain heartbeat showed the
    result: while ``playwright-e2e`` was stale, one cycle an hour read
    "issue" and the other eleven "ok", and a blind watchdog read "ok" on all
    twelve.
    """
    detail = f"throttled ({interval_minutes:.0f}m)"
    last = await _read_last_pass(pool)
    if last is None or last["ok"]:
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
_WATCHES_REF = f"app_settings.{WATCHES_SETTING_KEY}"


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

    # A pass that checked nothing, or could not check every workflow, is not
    # ok: that is how this watchdog read as healthy while blind.
    ok = not bad and n_assessed > 0 and not failures
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
    return {"ok": ok, "detail": detail, "workflows": results, "failures": failures}


async def _fail_every_repo(
    pool: Any,
    by_repo: dict[str, list[dict[str, Any]]],
    *,
    signature: str,
    detail_for: Callable[[str, int], str],
    now_utc: datetime,
    config: dict[str, Any],
    notify_fn: Callable[..., Any],
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
    return await _finish_pass(pool, results, failures, now_utc)


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

    watches = _parse_watches(await _read_setting(pool, WATCHES_SETTING_KEY, ""))
    if not watches:
        return {"ok": True, "detail": "no workflows configured", "workflows": {}}

    try:
        interval = float(await _read_setting(pool, INTERVAL_SETTING_KEY, "60"))
    except ValueError:
        interval = DEFAULT_INTERVAL_MINUTES
    if not await _should_run(pool, interval, now_utc):
        return await _throttled_summary(pool, interval)

    config = {
        "interval_minutes": interval,
        "failure_repage_hours": await _read_hours(
            pool, FAILURE_REPAGE_HOURS_KEY, DEFAULT_FAILURE_REPAGE_HOURS,
        ),
        "transient_failure_page_hours": await _read_hours(
            pool, TRANSIENT_FAILURE_PAGE_HOURS_KEY, DEFAULT_TRANSIENT_FAILURE_PAGE_HOURS,
        ),
    }
    by_repo = _group_by_repo(watches)

    token = await _read_token(pool)
    if not token:
        # The operator configured watches, so a missing token leaves this
        # dead-man's switch blind: page, once per episode. Without a token
        # every call to a private repo would 404, so none is made.
        return await _fail_every_repo(
            pool, by_repo, signature="no-token", detail_for=_no_token_detail,
            now_utc=now_utc, config=config, notify_fn=notify_fn,
        )
    if http_client_factory is None and httpx is None:
        return await _fail_every_repo(
            pool, by_repo, signature="no-httpx", detail_for=_no_httpx_detail,
            now_utc=now_utc, config=config, notify_fn=notify_fn,
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
    return await _finish_pass(pool, results, failures, now_utc)
