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

Not assessed (no finding, logged once) when: httpx is missing, no GitHub
token is configured, the workflow 404s, the API errors, or the workflow has
no scheduled runs at all. Mirrors ``data_freshness_probe``'s zero-rows rule —
an operator who never enabled a workflow gets no alarms about it.

Throttled to ``scheduled_workflow_watch_interval_minutes`` (default 60)
rather than running on every 5-minute brain cycle: each target costs two
GitHub API calls and nothing here changes minute to minute.

Standalone — stdlib + asyncpg + httpx (asyncpg pool injected by the daemon).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover — brain ships httpx; degrade loudly
    httpx = None  # type: ignore[assignment]

try:
    from secret_reader import read_app_setting as _shared_read_app_setting
except ImportError:  # pragma: no cover — package-qualified path
    from brain.secret_reader import read_app_setting as _shared_read_app_setting

logger = logging.getLogger(__name__)

ENABLED_SETTING_KEY = "scheduled_workflow_watch_enabled"
WATCHES_SETTING_KEY = "scheduled_workflows"
INTERVAL_SETTING_KEY = "scheduled_workflow_watch_interval_minutes"
TOKEN_SETTING_KEY = "gh_token"

_STATE_ENTITY_PREFIX = "scheduled_workflow_watchdog"
_LAST_RUN_ENTITY = f"{_STATE_ENTITY_PREFIX}:_last_checked"

# owner/name — GitHub's own allowed character set for both halves.
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
# A workflow FILENAME, not a path: the API takes the basename.
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")

_HTTP_TIMEOUT = 20.0


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

    ``event=schedule`` is not optional — see the module docstring. Raises on
    any non-200 so the caller can mark the target not-assessed rather than
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
        raise RuntimeError(
            f"GitHub workflow-runs returned {r.status_code}: {(r.text or '')[:160]}"
        )
    payload = r.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"non-object payload: {type(payload).__name__}")
    runs = payload.get("workflow_runs") or []
    newest = _parse_iso8601_utc(runs[0].get("created_at")) if runs else None
    return int(payload.get("total_count", 0)), newest


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


async def _should_run(pool: Any, interval_minutes: float) -> bool:
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
    age_min = (datetime.now(UTC) - last).total_seconds() / 60.0
    return age_min >= interval_minutes


async def _stamp_run(pool: Any) -> None:
    await _write_state(pool, "_last_checked", datetime.now(UTC).isoformat())


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
    pool: Any, client: Any, watch: dict[str, Any]
) -> dict[str, Any]:
    """Assess one workflow. Returns a result dict; never raises."""
    name = _target_name(watch)
    try:
        total, _ = await _fetch_runs(
            client, watch["repo"], watch["workflow"], only_success=False
        )
        if total == 0:
            # Never scheduled-fired here (fresh install, or the operator
            # removed the cron). Not an alert condition.
            return {"state": "not_assessed", "reason": "no scheduled runs"}
        _, last_success = await _fetch_runs(
            client, watch["repo"], watch["workflow"], only_success=True
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sched_wf] %s not assessed: %s", name, exc)
        return {"state": "not_assessed", "reason": str(exc)[:160]}

    if last_success is None:
        mode, is_bad = "never_green", True
        age_h: float | None = None
        detail = f"{total} scheduled run(s), none successful."
        extra = {"scheduled_runs": total, "successful_runs": 0}
    else:
        age_h = (datetime.now(UTC) - last_success).total_seconds() / 3600.0
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
    return result


async def run_scheduled_workflow_watch(pool: Any) -> dict[str, Any]:
    """One pass over every configured workflow. Never raises."""
    enabled = (await _read_setting(pool, ENABLED_SETTING_KEY, "true")).lower()
    if enabled in ("false", "0", "no", "off"):
        return {"ok": True, "detail": "disabled", "workflows": {}}

    if httpx is None:
        return {"ok": True, "detail": "httpx missing — not assessed",
                "workflows": {}}

    watches = _parse_watches(await _read_setting(pool, WATCHES_SETTING_KEY, ""))
    if not watches:
        return {"ok": True, "detail": "no workflows configured", "workflows": {}}

    try:
        interval = float(await _read_setting(pool, INTERVAL_SETTING_KEY, "60"))
    except ValueError:
        interval = 60.0
    if not await _should_run(pool, interval):
        return {"ok": True, "detail": f"throttled ({interval:.0f}m)",
                "workflows": {}}

    token = await _read_token(pool)
    if not token:
        # No token: the API would 401 on every call. Say it once, don't alarm.
        logger.info(
            "[sched_wf] no %s configured — scheduled-CI watchdog not assessed",
            TOKEN_SETTING_KEY,
        )
        await _stamp_run(pool)
        return {"ok": True, "detail": "no github token — not assessed",
                "workflows": {}}

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    results: dict[str, dict[str, Any]] = {}
    bad: list[str] = []
    async with httpx.AsyncClient(headers=headers, timeout=_HTTP_TIMEOUT) as client:
        for watch in watches:
            res = await _assess(pool, client, watch)
            results[_target_name(watch)] = res
            if res.get("state") in ("stale", "never_green"):
                bad.append(_target_name(watch))

    await _stamp_run(pool)

    # `not_assessed` is neither health nor alarm — a target the probe could
    # not reach must never be counted as fine. Saying "all N healthy" when
    # zero were actually checked is the precise lie this probe exists to
    # catch, so the summary counts ASSESSED targets, not configured ones.
    n_unassessed = sum(
        1 for r in results.values() if r.get("state") == "not_assessed"
    )
    n_assessed = len(results) - n_unassessed
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

    # Log EVERY completed pass, healthy or not. A probe that only speaks when
    # something is wrong is indistinguishable from a probe that never ran —
    # the exact failure class this file exists to catch. It bit during this
    # probe's own first-pass verification: the brain log said nothing, and
    # whether it had run had to be dug out of brain_knowledge.
    logger.info(
        "[sched_wf] pass complete — %s (%d assessed, %d not assessed)",
        detail, n_assessed, n_unassessed,
    )
    return {"ok": not bad, "detail": detail, "workflows": results}
