"""FindingsDailyDigestJob — once-a-day Discord summary of findings activity.

The last unbuilt Phase 4 triage surface from poindexter#461. The MCP
``findings_list`` tool and the Grafana "Findings — Probe Routing" dashboard
shipped (glad-labs-stack#927); the daily digest did not. This job posts a
once-a-day rollup to the Discord ops channel so the operator sees findings
volume + routing at a glance without opening Grafana, e.g.:

    📋 Findings — last 24h: 47 across 6 kinds. Top: media_drift ×30 (log_only),
    missing_seo ×9 (auto_fix), quality_regression ×8 (github_issue). 0 pending
    delivery.

Routine, so Discord not Telegram (``feedback_telegram_vs_discord``).

Data sources (the same shapes the dashboard panels + ``findings_alert_router``
use):

* By-kind volume — ``audit_log WHERE event_type='finding'`` in the trailing
  window, grouped by ``details->>'kind'``.
* Delivery policy per kind — ``findings.<kind>.delivery`` app_settings; a kind
  with no policy stays loud (``route``), matching the router's default.
* Pending delivery — routable findings (``severity in warn/warning/critical``)
  with ``audit_log.id`` above ``findings_alert_route_watermark`` (the backlog
  ``FindingsAlertRouterJob`` hasn't forwarded yet). NOT window-scoped — it's the
  live routing health signal; ``0`` means the router is keeping up.

Config (all DB-first):

* ``findings_daily_digest_enabled`` (default ``true``) — master switch.
* ``findings_daily_digest_lookback_hours`` (default ``24``) — the rollup window.
* ``findings_daily_digest_top_n`` (default ``5``) — how many kinds to name.

Schedule defaults to ``0 9 * * *`` (09:00 local container time) and is tunable
like every other job via the ``plugin.job.findings_daily_digest`` config's
``config.schedule`` override.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.integrations.operator_notify import notify_operator

logger = logging.getLogger(__name__)

_DEFAULT_LOOKBACK_HOURS = 24
_DEFAULT_TOP_N = 5
_DISCORD_MAX_CHARS = 1800
_WATERMARK_KEY = "findings_alert_route_watermark"
# audit_log.severity values the router forwards (mirrors findings_alert_router).
_ROUTABLE_SEVERITIES = ("warn", "warning", "critical")


class FindingsDailyDigestJob:
    name = "findings_daily_digest"
    description = (
        "Daily findings digest — once-a-day Discord rollup of audit_log "
        "findings by kind + delivery policy + pending-delivery count"
    )
    # 09:00 local. Tunable via plugin.job.findings_daily_digest config.schedule.
    schedule = "0 9 * * *"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # ---- Master switch ----
        enabled = await _read_bool_setting(pool, "findings_daily_digest_enabled", True)
        if not enabled:
            return JobResult(ok=True, detail="disabled", changes_made=0)

        # ---- Webhook required up front (feedback_no_silent_defaults) ----
        discord_configured = bool(
            await _read_setting(pool, "discord_ops_webhook_url", "")
        )
        if not discord_configured:
            logger.warning(
                "[findings_daily_digest] discord_ops_webhook_url is empty; "
                "digest cannot be delivered"
            )
            return JobResult(
                ok=False,
                detail="discord_ops_webhook_url not configured",
                changes_made=0,
            )

        lookback_hours = int(
            config.get("lookback_hours")
            or await _read_int_setting(
                pool, "findings_daily_digest_lookback_hours", _DEFAULT_LOOKBACK_HOURS
            )
            or _DEFAULT_LOOKBACK_HOURS
        )
        top_n = int(
            config.get("top_n")
            or await _read_int_setting(
                pool, "findings_daily_digest_top_n", _DEFAULT_TOP_N
            )
            or _DEFAULT_TOP_N
        )

        # ---- Gather ----
        try:
            data = await _gather_findings_data(pool, lookback_hours)
        except Exception as exc:  # noqa: BLE001 — gather is best-effort
            logger.exception("[findings_daily_digest] data gather failed: %s", exc)
            return JobResult(
                ok=False, detail=f"data gather failed: {exc}", changes_made=0,
            )

        # ---- Format + send (Discord only) ----
        message = _format_digest(data, lookback_hours, top_n)
        try:
            await notify_operator(message)
        except Exception as exc:  # noqa: BLE001 — surface but don't crash
            logger.exception("[findings_daily_digest] Discord send failed: %s", exc)
            return JobResult(
                ok=False, detail=f"Discord send failed: {exc}", changes_made=0,
            )

        return JobResult(
            ok=True,
            detail=(
                f"sent digest — {data['total']} findings across "
                f"{len(data['by_kind'])} kinds, {data['pending']} pending delivery"
            ),
            changes_made=1,
            metrics={
                "total_findings": data["total"],
                "kind_count": len(data["by_kind"]),
                "pending_delivery": data["pending"],
            },
        )


# ---------------------------------------------------------------------------
# app_settings helpers — read directly so the job stays usable with a bare
# pool (no SiteConfig DI seam), mirroring services/jobs/morning_brief.py.
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: str) -> str:
    """Read ``app_settings[key]``, decrypting when ``is_secret=true`` (routes
    through ``plugins.secrets.get_secret`` so encrypted rows like
    ``discord_ops_webhook_url`` return plaintext, not ``enc:v1:`` ciphertext)."""
    from plugins.secrets import get_secret
    try:
        val = await get_secret(pool, key)
    except Exception as exc:  # noqa: BLE001 — best-effort; degrade to default
        logger.debug("[findings_daily_digest] setting %s fetch failed: %s", key, exc)
        return default
    return str(val) if val not in (None, "") else default


async def _read_bool_setting(pool: Any, key: str, default: bool) -> bool:
    raw = await _read_setting(pool, key, "")
    if raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


async def _read_int_setting(pool: Any, key: str, default: int) -> int:
    raw = await _read_setting(pool, key, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


async def _gather_findings_data(pool: Any, lookback_hours: int) -> dict[str, Any]:
    """Roll up findings by kind (windowed) + per-kind delivery policy + the
    pending-delivery backlog (above the router watermark)."""
    interval = f"{lookback_hours} hours"

    async with pool.acquire() as conn:
        # By-kind volume in the trailing window (pre-sorted, biggest first).
        kind_rows = await conn.fetch(
            """
            SELECT COALESCE(details->>'kind', 'unknown') AS kind,
                   COUNT(*)                               AS cnt
            FROM audit_log
            WHERE event_type = 'finding'
              AND timestamp >= NOW() - ($1::text)::interval
            GROUP BY COALESCE(details->>'kind', 'unknown')
            ORDER BY cnt DESC
            """,
            interval,
        )

        # Per-kind delivery policy (findings.<kind>.delivery).
        policy_rows = await conn.fetch(
            "SELECT key, value FROM app_settings WHERE key LIKE 'findings.%.delivery'"
        )

        # Pending delivery = routable findings the router hasn't forwarded yet
        # (id above the persisted watermark). NOT window-scoped — it's the live
        # routing-health signal, so a chronic backlog shows even if it predates
        # the window.
        wm_row = await conn.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1", _WATERMARK_KEY
        )
        watermark = 0
        if wm_row and wm_row["value"]:
            try:
                watermark = int(wm_row["value"])
            except (TypeError, ValueError):
                watermark = 0
        pending = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM audit_log
            WHERE event_type = 'finding'
              AND severity = ANY($1::text[])
              AND id > $2
            """,
            list(_ROUTABLE_SEVERITIES),
            watermark,
        )

    deliveries: dict[str, str] = {}
    for r in policy_rows:
        parts = r["key"].split(".")
        # findings.<kind>.delivery
        if len(parts) == 3:
            deliveries[parts[1]] = r["value"]

    # A kind with no policy stays loud ('route') — same default as the router.
    by_kind = [
        (r["kind"], int(r["cnt"]), deliveries.get(r["kind"], "route"))
        for r in kind_rows
    ]
    total = sum(c for _, c, _ in by_kind)

    return {"total": total, "by_kind": by_kind, "pending": int(pending or 0)}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_digest(
    data: dict[str, Any], lookback_hours: int, top_n: int,
) -> str:
    total = data["total"]
    by_kind: list[tuple[str, int, str]] = data["by_kind"]
    pending = data["pending"]
    kind_count = len(by_kind)

    header = f"\U0001F4CB **Findings — last {lookback_hours}h:**"
    pending_str = f"{pending} pending delivery."

    if total == 0:
        return f"{header} none. {pending_str}"

    top = by_kind[:top_n]
    top_str = ", ".join(f"{kind} ×{cnt} ({delivery})" for kind, cnt, delivery in top)
    suffix = f" (+{kind_count - top_n} more kinds)" if kind_count > top_n else ""
    out = (
        f"{header} {total} across {kind_count} kinds. "
        f"Top: {top_str}{suffix}. {pending_str}"
    )
    if len(out) > _DISCORD_MAX_CHARS:
        out = out[: _DISCORD_MAX_CHARS - 12] + "…(truncated)"
    return out
