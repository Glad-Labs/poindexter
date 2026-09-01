"""Deploy-sync freshness probe — a dead-man's switch on the deploy path.

poindexter#977. On 2026-08-02 host DNS dropped for ~30 minutes.
``poindexter-deploy-sync.service`` failed repeatedly, which was correct — but
after the 00:01 EDT failure the *timer* stopped scheduling entirely
(``NEXT: -``), and merged ``main`` then sat undeployed for ~45 minutes with
**zero signal**. Recovery was a manual ``systemctl start``.

The shape is the one that keeps recurring here: not a component that broke
loudly, but one that stopped running while everything still looked fine.
Nothing polls "did the deploy path run recently?", so a frozen timer is
indistinguishable from a quiet one — both produce no output at all.

Two conditions, deliberately separated because they need different responses:

* **stale** — the newest ``deploy_sync_run`` heartbeat is older than
  ``deploy_sync_max_age_minutes``. The deploy path is not running. Merged
  commits are silently not shipping. ``critical``.
* **failing** — the last ``deploy_sync_error_streak_threshold`` runs all
  reported ``result='error'``. The path IS running, and consistently cannot
  finish. ``warning``: the timer will retry on its own, and a single bad
  network minute must not page.

A run that is merely *deferred* (``deferred-active-flow`` — the sync waited
out an in-flight Prefect run rather than restarting a busy worker) is a
HEALTHY outcome, not an error. It counts as liveness and never as a failure;
treating deferral as breakage would page on the mechanism working.

Why the heartbeat is in the DB rather than read from the status file: the
status JSON lives at the ``~/.poindexter`` root, and the brain container
mounts only subdirectories of it. Exposing the root to read one file would
also hand ``bootstrap.toml`` — the master key — to a container with no need
for it, and a single-file bind mount goes stale when the writer replaces the
inode. Postgres is already the bus, and ``deploy-checkout-sync.sh`` already
talks to it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# --- Setting keys + in-code fallbacks (drift-guarded by test) --------------
ENABLED_SETTING_KEY = "deploy_sync_probe_enabled"
MAX_AGE_SETTING_KEY = "deploy_sync_max_age_minutes"
STREAK_SETTING_KEY = "deploy_sync_error_streak_threshold"

# The timer fires every 10 minutes; 35 gives three missed fires plus slack for
# a long pass (TimeoutStartSec is 900s, and a rebuild pass can legitimately
# run for several minutes). Tight enough that a frozen timer is caught inside
# one cycle of the 45-minute outage that motivated this.
DEFAULT_MAX_AGE_MINUTES = 35
DEFAULT_ERROR_STREAK = 3

# Dot-free so `findings.<kind>.delivery` attaches as one settings key.
FINDING_KIND_STALE = "deploy_sync_stale"
FINDING_KIND_FAILING = "deploy_sync_failing"

HEARTBEAT_EVENT = "deploy_sync_run"

# Outcomes the sync script writes that mean "ran and did its job". Anything
# else it writes is `error`. Kept as an explicit allowlist rather than
# `!= 'error'` so a NEW result string shows up as unknown-but-alive in the
# summary instead of being silently folded into "healthy".
_OK_RESULTS = frozenset({
    "deployed",
    "synced-norestart",
    "synced-no-change",
    "baseline-recorded",
    "deferred-active-flow",
})


async def _read_setting(pool: Any, key: str, default: str = "") -> str:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[deploy_sync] setting read %s failed: %s", key, exc)
        return default
    # '' is the app_settings "unset" sentinel — treat it as the default.
    return str(val) if val not in (None, "") else default


async def _read_int_setting(pool: Any, key: str, default: int) -> int:
    raw = (await _read_setting(pool, key, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "[deploy_sync] %s not an int (%r); using %d", key, raw, default,
        )
        return default


async def _emit_finding(
    pool: Any, *, kind: str, severity: str, title: str, body: str,
    extra: dict[str, Any],
) -> None:
    details = {
        "kind": kind,
        "title": title,
        "body": body,
        "dedup_key": kind,
        "extra": extra,
    }
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ('finding', 'deploy_sync_probe', $1::jsonb, $2)",
            json.dumps(details), severity,
        )
        logger.warning("[deploy_sync] %s — finding emitted (%s)", title, severity)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[deploy_sync] finding insert failed: %s", exc)


def _details_of(row: Any) -> dict[str, Any]:
    raw = row["details"] if row is not None else None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


async def run_deploy_sync_probe(pool: Any) -> dict[str, Any]:
    """Check that the deploy path is both running and succeeding.

    Returns a summary dict; never raises (the brain cycle must survive any
    probe). ``ok`` is False only when a finding was emitted.
    """
    if pool is None:
        return {"ok": True, "status": "no_pool", "detail": "no DB pool"}

    enabled = (await _read_setting(pool, ENABLED_SETTING_KEY, "true")).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return {"ok": True, "status": "disabled", "detail": "probe disabled"}

    max_age = await _read_int_setting(pool, MAX_AGE_SETTING_KEY, DEFAULT_MAX_AGE_MINUTES)
    streak_n = await _read_int_setting(pool, STREAK_SETTING_KEY, DEFAULT_ERROR_STREAK)

    try:
        rows = await pool.fetch(
            'SELECT "timestamp", details, '
            '       EXTRACT(EPOCH FROM (now() - "timestamp"))/60.0 AS age_minutes '
            "FROM audit_log WHERE event_type = $1 "
            'ORDER BY "timestamp" DESC LIMIT $2',
            HEARTBEAT_EVENT, max(streak_n, 1),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[deploy_sync] heartbeat query failed: %s", exc)
        return {"ok": True, "status": "query_failed", "detail": str(exc)}

    if not rows:
        # Never a page. A host that never installed the deploy-sync timer is
        # indistinguishable here from one whose timer has never fired, and the
        # first is a legitimate configuration. Surfaced in the summary so the
        # brain's own status still shows it as unmonitored rather than green.
        return {
            "ok": True,
            "status": "no_history",
            "detail": (
                f"no {HEARTBEAT_EVENT} rows — deploy-sync has never reported "
                "on this host (timer not installed, or never fired)"
            ),
        }

    newest = rows[0]
    age_minutes = float(newest["age_minutes"] or 0.0)
    latest = _details_of(newest)
    result = str(latest.get("result") or "unknown")
    host = str(latest.get("host") or "unknown")

    summary: dict[str, Any] = {
        "ok": True,
        "status": "fresh",
        "age_minutes": round(age_minutes, 1),
        "max_age_minutes": max_age,
        "last_result": result,
        "host": host,
        "detail": "",
    }

    if age_minutes > max_age:
        summary["ok"] = False
        summary["status"] = "stale"
        summary["detail"] = (
            f"newest deploy-sync heartbeat is {age_minutes:.0f}m old "
            f"(max {max_age}m)"
        )
        await _emit_finding(
            pool,
            kind=FINDING_KIND_STALE,
            severity="critical",
            title=(
                f"Deploy path frozen: no deploy-sync run in {age_minutes:.0f}m "
                f"(expected every ~10m)"
            ),
            body=(
                f"The newest `{HEARTBEAT_EVENT}` heartbeat on {host} is "
                f"{age_minutes:.0f} minutes old, past the {max_age}m threshold. "
                f"Merged commits on origin/main are most likely NOT reaching "
                f"the running stack, and nothing else reports that — the sync "
                f"produces no output when it does not run.\n\n"
                f"Last recorded result was `{result}`.\n\n"
                f"Check `systemctl list-timers poindexter-deploy-sync.timer` — "
                f"a `NEXT: -` there means the timer has stopped scheduling and "
                f"needs `systemctl start poindexter-deploy-sync.service` to "
                f"recover. Also check `journalctl -u poindexter-deploy-sync` "
                f"and `~/.poindexter/deploy-checkout-sync.log`."
            ),
            extra={
                "age_minutes": round(age_minutes, 1),
                "max_age_minutes": max_age,
                "last_result": result,
                "host": host,
            },
        )
        return summary

    # Fresh, but is it actually succeeding? Only an unbroken streak counts:
    # one bad run between good ones is the retry working, not a failure.
    errored = [r for r in rows if str(_details_of(r).get("result")) == "error"]
    if len(rows) >= streak_n and len(errored) == len(rows) == streak_n:
        details_list = [str(_details_of(r).get("detail") or "") for r in rows]
        summary["ok"] = False
        summary["status"] = "failing"
        summary["error_streak"] = streak_n
        summary["detail"] = f"last {streak_n} deploy-sync runs all errored"
        await _emit_finding(
            pool,
            kind=FINDING_KIND_FAILING,
            severity="warning",
            title=f"Deploy-sync has failed {streak_n} runs in a row",
            body=(
                f"The last {streak_n} `{HEARTBEAT_EVENT}` heartbeats on {host} "
                f"all reported `result=error`, so the deploy path is running "
                f"but cannot finish. Merged commits are not reaching the "
                f"running stack.\n\n"
                f"Most recent details, newest first:\n"
                + "\n".join(f"- {d or '(none)'}" for d in details_list)
                + "\n\nCommon causes: DNS/network failure on `git fetch`, a "
                f"dirty deploy clone blocking `git reset --hard`, or a failing "
                f"image build. See `~/.poindexter/deploy-checkout-sync.log`."
            ),
            extra={
                "error_streak": streak_n,
                "recent_details": details_list,
                "host": host,
            },
        )
        return summary

    summary["detail"] = (
        f"deploy-sync ran {age_minutes:.0f}m ago with result={result}"
    )
    return summary
