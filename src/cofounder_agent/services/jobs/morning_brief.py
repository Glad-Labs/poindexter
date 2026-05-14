"""MorningBriefJob — daily 7am consolidated digest of the prior 24h.

Posts a structured "what happened while I slept" summary to the
Discord ops channel via ``discord_ops_webhook_url``. Pings Telegram
only when criticals appeared overnight (failed tasks or critical-
severity alerts) so routine mornings stay quiet.

Sections in the brief:
1. Posts published in the last N hours
2. Posts entering ``awaiting_approval`` in the last N hours
3. Failed tasks with sample error messages
4. Alert counts per severity
5. Cost spent (cloud vs local-zero)
6. Open PRs >24h with green CI (best-effort via ``gh`` if on PATH)
7. Brain probe failures from ``audit_log``

Config (``plugin.job.morning_brief``)
- ``enabled`` (default true)
- ``config.lookback_hours`` (default 24)
- ``config.telegram_critical_only`` (default true)
- ``config.max_message_chars`` (default 1800 — Discord 2000-char limit headroom)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


_DEFAULT_LOOKBACK_HOURS = 24
_DISCORD_MAX_CHARS = 1800


class MorningBriefJob:
    name = "morning_brief"
    description = "Daily morning brief — Discord digest + Telegram ping on overnight criticals"
    # 0 7 * * * = 07:00 local container time. Hour is overridable via
    # the ``morning_brief_hour_local`` app_settings key surfaced in
    # PluginConfig.config['cron_expression'] for full schedule overrides.
    schedule = "0 7 * * *"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # ---- Master switch ----
        # Read morning_brief_enabled directly from app_settings so the
        # operator can toggle the job from the dashboard without editing
        # the plugin config row. Defaults to true.
        enabled = await _read_bool_setting(pool, "morning_brief_enabled", True)
        if not enabled:
            return JobResult(ok=True, detail="disabled", changes_made=0)

        # ---- Webhook required up front ----
        # Per feedback_no_silent_defaults: don't silently swallow a missing
        # Discord webhook. Surface it as an explicit failure detail.
        webhook_url = await _read_setting(pool, "discord_ops_webhook_url", "")
        if not webhook_url:
            logger.warning(
                "[morning_brief] discord_ops_webhook_url is empty; brief "
                "cannot be delivered"
            )
            return JobResult(
                ok=False,
                detail="discord_ops_webhook_url not configured",
                changes_made=0,
            )

        lookback_hours = int(
            config.get("lookback_hours")
            or await _read_int_setting(pool, "morning_brief_lookback_hours", _DEFAULT_LOOKBACK_HOURS)
            or _DEFAULT_LOOKBACK_HOURS
        )

        critical_only_default = await _read_bool_setting(
            pool, "morning_brief_telegram_critical_only", True,
        )
        telegram_critical_only = bool(
            config.get("telegram_critical_only", critical_only_default)
        )

        max_chars = int(config.get("max_message_chars", _DISCORD_MAX_CHARS))
        site_url = await _read_setting(pool, "site_url", "")

        # ---- Gather data in a small number of round-trips ----
        try:
            data = await _gather_brief_data(pool, lookback_hours)
        except Exception as exc:  # noqa: BLE001 — gather is best-effort
            logger.exception("[morning_brief] data gather failed: %s", exc)
            return JobResult(
                ok=False, detail=f"data gather failed: {exc}", changes_made=0,
            )

        # Open PRs come from a subprocess and are entirely optional.
        data["open_prs"] = await _gather_open_prs()

        # ---- Format ----
        message = _format_brief(data, lookback_hours, site_url, max_chars)

        # ---- Send Discord ----
        try:
            await _send_discord(webhook_url, message)
        except Exception as exc:  # noqa: BLE001 — surface but don't crash
            logger.exception("[morning_brief] Discord send failed: %s", exc)
            return JobResult(
                ok=False, detail=f"Discord send failed: {exc}", changes_made=0,
            )

        # ---- Telegram tag rule ----
        criticals_present = (
            data["alerts_by_severity"].get("critical", 0) > 0
            or data["failed_tasks_count"] > 0
        )
        telegram_pinged = False
        if criticals_present and telegram_critical_only:
            try:
                from services.integrations.operator_notify import notify_operator
                await notify_operator(
                    _format_telegram_summary(data, lookback_hours),
                    critical=True,
                )
                telegram_pinged = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("[morning_brief] Telegram ping failed: %s", exc)

        return JobResult(
            ok=True,
            detail=(
                f"sent brief — published={data['published_count']} "
                f"awaiting={data['awaiting_count']} failed={data['failed_tasks_count']} "
                f"telegram={'yes' if telegram_pinged else 'no'}"
            ),
            changes_made=1,
            metrics={
                "published_count": data["published_count"],
                "awaiting_count": data["awaiting_count"],
                "failed_tasks_count": data["failed_tasks_count"],
                "alerts_critical": data["alerts_by_severity"].get("critical", 0),
                "alerts_warning": data["alerts_by_severity"].get("warning", 0),
                "cost_cloud_usd": data["cost_cloud_usd"],
                "brain_probe_failures": data["brain_probe_failures"],
                "telegram_pinged": telegram_pinged,
            },
        )


# ---------------------------------------------------------------------------
# app_settings helpers — reading directly so the job stays usable even when
# SiteConfig is not in the DI seam (jobs run with a bare pool).
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: str) -> str:
    """Read ``app_settings[key]``, decrypting when ``is_secret=true``.

    Routes through ``plugins.secrets.get_secret`` so callers reading
    encrypted rows (e.g. ``discord_ops_webhook_url``) get plaintext
    instead of ``enc:v1:`` ciphertext. Mirrors the brain-side fix in
    ``brain/alert_dispatcher.py`` for the same bug class.
    """
    from plugins.secrets import get_secret
    try:
        val = await get_secret(pool, key)
    except Exception as exc:  # noqa: BLE001 — best-effort; degrade to default
        logger.debug("[morning_brief] setting %s fetch failed: %s", key, exc)
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


async def _gather_brief_data(pool: Any, lookback_hours: int) -> dict[str, Any]:
    """Pull all 24h activity rollups in a small number of queries."""
    interval = f"{lookback_hours} hours"

    async with pool.acquire() as conn:
        # Posts published in the window.
        published_rows = await conn.fetch(
            """
            SELECT id, title, slug
            FROM posts
            WHERE status = 'published'
              AND published_at >= NOW() - ($1::text)::interval
            ORDER BY published_at DESC
            """,
            interval,
        )

        # Posts (content_tasks rows) entering awaiting_approval in the window.
        # Excludes auto-approved tasks per the spec — auto-approval is
        # captured by ``approval_status='auto_approved'`` on the row.
        awaiting_rows = await conn.fetch(
            """
            SELECT task_id, COALESCE(title, topic) AS title
            FROM content_tasks
            WHERE status = 'awaiting_approval'
              AND updated_at >= NOW() - ($1::text)::interval
              AND COALESCE(approval_status, '') <> 'auto_approved'
            ORDER BY updated_at DESC
            LIMIT 50
            """,
            interval,
        )

        # Failed tasks in the window with a sample of error messages.
        failed_rows = await conn.fetch(
            """
            SELECT task_id,
                   COALESCE(title, topic) AS title,
                   error_message
            FROM content_tasks
            WHERE status = 'failed'
              AND updated_at >= NOW() - ($1::text)::interval
            ORDER BY updated_at DESC
            LIMIT 25
            """,
            interval,
        )

        # Alerts grouped by severity.
        alert_rows = await conn.fetch(
            """
            SELECT COALESCE(severity, 'unknown') AS severity,
                   COUNT(*)                       AS count,
                   MODE() WITHIN GROUP (ORDER BY alertname) AS top_alertname
            FROM alert_events
            WHERE received_at >= NOW() - ($1::text)::interval
              AND status = 'firing'
            GROUP BY severity
            """,
            interval,
        )

        # Cost split — cloud (cost_usd > 0) vs local-zero. Local LLMs log
        # cost_usd=0 so the sum is naturally 0.0 for an all-local day.
        cost_row = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(cost_usd) FILTER (WHERE cost_usd > 0), 0)::float AS cloud_usd,
                COUNT(*) FILTER (WHERE cost_usd > 0)                          AS cloud_calls,
                COUNT(*) FILTER (WHERE COALESCE(cost_usd, 0) = 0)             AS local_calls
            FROM cost_logs
            WHERE created_at >= NOW() - ($1::text)::interval
            """,
            interval,
        )

        # Brain probe failures from audit_log.
        brain_probe_failures = (
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM audit_log
                WHERE timestamp >= NOW() - ($1::text)::interval
                  AND event_type LIKE 'brain.probe.%'
                  AND COALESCE(severity, '') IN ('error', 'critical', 'warning')
                """,
                interval,
            )
            or 0
        )

        # Total brain probe cycles for the "X failed across N cycles" line.
        brain_probe_cycles = (
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM audit_log
                WHERE timestamp >= NOW() - ($1::text)::interval
                  AND event_type LIKE 'brain.probe.%'
                """,
                interval,
            )
            or 0
        )

    alerts_by_severity: dict[str, int] = {}
    top_alertname_by_severity: dict[str, str] = {}
    for row in alert_rows:
        sev = (row["severity"] or "unknown").lower()
        alerts_by_severity[sev] = int(row["count"] or 0)
        top = row.get("top_alertname") if hasattr(row, "get") else row["top_alertname"]
        if top:
            top_alertname_by_severity[sev] = top

    cost_cloud_usd = 0.0
    cost_cloud_calls = 0
    cost_local_calls = 0
    if cost_row is not None:
        cost_cloud_usd = float(cost_row["cloud_usd"] or 0.0)
        cost_cloud_calls = int(cost_row["cloud_calls"] or 0)
        cost_local_calls = int(cost_row["local_calls"] or 0)

    return {
        "published_count": len(published_rows),
        "published_rows": [dict(r) for r in published_rows],
        "awaiting_count": len(awaiting_rows),
        "awaiting_rows": [dict(r) for r in awaiting_rows],
        "failed_tasks_count": len(failed_rows),
        "failed_rows": [dict(r) for r in failed_rows],
        "alerts_by_severity": alerts_by_severity,
        "top_alertname_by_severity": top_alertname_by_severity,
        "cost_cloud_usd": cost_cloud_usd,
        "cost_cloud_calls": cost_cloud_calls,
        "cost_local_calls": cost_local_calls,
        "brain_probe_failures": int(brain_probe_failures),
        "brain_probe_cycles": int(brain_probe_cycles),
    }


async def _gather_open_prs() -> list[dict[str, Any]]:
    """List open PRs >24h with green CI via ``gh`` (best-effort).

    Lazy-imported subprocess so the module's import time stays fast and
    workers without ``gh`` on PATH still load the job.
    """
    import asyncio
    import json
    import shutil

    if not shutil.which("gh"):
        logger.warning(
            "[morning_brief] gh CLI not on PATH — skipping open-PRs section"
        )
        return []

    cmd = [
        "gh", "pr", "list",
        "--search", "is:pr is:open",
        "--json", "number,title,url,createdAt,statusCheckRollup",
        "--limit", "30",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[morning_brief] gh subprocess failed: %s", exc)
        return []

    if proc.returncode != 0:
        logger.warning(
            "[morning_brief] gh returned %s: %s",
            proc.returncode, (stderr or b"").decode("utf-8", errors="replace")[:200],
        )
        return []

    try:
        prs = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        logger.warning("[morning_brief] gh JSON parse failed: %s", exc)
        return []

    cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600
    matched: list[dict[str, Any]] = []
    for pr in prs:
        created_iso = pr.get("createdAt", "")
        try:
            created_ts = datetime.fromisoformat(
                created_iso.replace("Z", "+00:00"),
            ).timestamp()
        except (TypeError, ValueError):
            continue
        if created_ts > cutoff:
            continue  # too new
        rollup = pr.get("statusCheckRollup") or []
        # Green = every check completed with conclusion in {SUCCESS, NEUTRAL, SKIPPED}
        # or empty (no checks configured). Mirrors gh's "All checks passing".
        all_green = all(
            (c.get("conclusion") or "SUCCESS")
            in ("SUCCESS", "NEUTRAL", "SKIPPED")
            for c in rollup
        )
        if all_green:
            matched.append(
                {"number": pr.get("number"), "title": pr.get("title"), "url": pr.get("url")}
            )
    return matched


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_brief(
    data: dict[str, Any], lookback_hours: int, site_url: str, max_chars: int,
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: list[str] = [f"\U0001F305 **Morning brief — {today}**", ""]

    # Published
    pub_count = data["published_count"]
    if pub_count:
        links = [
            _post_link(row["title"], row["slug"], site_url)
            for row in data["published_rows"][:5]
        ]
        suffix = f" (+{pub_count - 5} more)" if pub_count > 5 else ""
        lines.append(
            f"\U0001F4E4 **Published ({lookback_hours}h):** {pub_count} — "
            f"{', '.join(links)}{suffix}"
        )
    else:
        lines.append(f"\U0001F4E4 **Published ({lookback_hours}h):** 0")

    # Awaiting approval
    awaiting_count = data["awaiting_count"]
    if awaiting_count:
        top_title = (data["awaiting_rows"][0].get("title") or "<untitled>")[:80]
        lines.append(
            f"\U0001F4CB **Awaiting approval ({lookback_hours}h):** {awaiting_count} new — "
            f"top: \"{top_title}\""
        )
    else:
        lines.append(f"\U0001F4CB **Awaiting approval ({lookback_hours}h):** 0 new")

    # Alerts
    sev_counts = data["alerts_by_severity"]
    crit = sev_counts.get("critical", 0)
    warn = sev_counts.get("warning", 0)
    other = sum(v for k, v in sev_counts.items() if k not in ("critical", "warning"))
    if crit or warn or other:
        top_sev = "critical" if crit else ("warning" if warn else next(iter(sev_counts), ""))
        top_alert = data["top_alertname_by_severity"].get(top_sev, "")
        top_alert_count = sev_counts.get(top_sev, 0)
        top_str = (
            f" — top: {top_alert} × {top_alert_count}" if top_alert else ""
        )
        lines.append(
            f"⚠️ **Alerts ({lookback_hours}h):** {crit} critical, "
            f"{warn} warnings{top_str}"
        )
    else:
        lines.append(f"⚠️ **Alerts ({lookback_hours}h):** none")

    # Cost
    cloud_usd = data["cost_cloud_usd"]
    if cloud_usd > 0:
        lines.append(
            f"\U0001F4B5 **Cost ({lookback_hours}h):** ${cloud_usd:.2f} cloud "
            f"({data['cost_cloud_calls']} calls, {data['cost_local_calls']} local)"
        )
    else:
        lines.append(
            f"\U0001F4B5 **Cost ({lookback_hours}h):** $0.00 cloud (local-only)"
        )

    # Failed tasks
    failed_count = data["failed_tasks_count"]
    if failed_count:
        first = data["failed_rows"][0]
        sample = (first.get("error_message") or first.get("title") or "")[:80]
        lines.append(
            f"\U0001F41B **Failed tasks ({lookback_hours}h):** {failed_count} — "
            f"\"{sample}\""
        )
    else:
        lines.append(f"\U0001F41B **Failed tasks ({lookback_hours}h):** 0")

    # Open PRs
    open_prs = data.get("open_prs") or []
    lines.append(
        f"\U0001F6E0 **Open PRs >24h with green CI:** {len(open_prs)}"
    )

    # Brain probes
    failures = data["brain_probe_failures"]
    cycles = data["brain_probe_cycles"]
    if cycles:
        marker = "✅" if not failures else "⚠️"
        lines.append(
            f"{marker} **Brain probes:** {failures} failed across {cycles:,} cycles"
        )
    else:
        lines.append("✅ **Brain probes:** no probe activity recorded")

    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[: max_chars - 12] + "\n…(truncated)"
    return out


def _post_link(title: str, slug: str, site_url: str) -> str:
    """Render a Discord-flavored markdown link for a published post."""
    title_clean = (title or "<untitled>")[:60]
    if site_url and slug:
        return f"[{title_clean}]({site_url.rstrip('/')}/posts/{slug})"
    return f"\"{title_clean}\""


def _format_telegram_summary(data: dict[str, Any], lookback_hours: int) -> str:
    """Compact Telegram body — no Markdown links to keep clients happy."""
    crit = data["alerts_by_severity"].get("critical", 0)
    failed = data["failed_tasks_count"]
    parts = [f"Morning brief ({lookback_hours}h) — overnight criticals:"]
    if crit:
        top = data["top_alertname_by_severity"].get("critical", "")
        suffix = f" (top: {top})" if top else ""
        parts.append(f"- {crit} critical alerts{suffix}")
    if failed:
        sample = (data["failed_rows"][0].get("error_message") or "")[:80]
        parts.append(f"- {failed} failed tasks (e.g. \"{sample}\")")
    parts.append("Full brief in Discord #ops.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Discord delivery
# ---------------------------------------------------------------------------


async def _send_discord(webhook_url: str, message: str) -> None:
    """POST the brief to the operator's Discord ops webhook."""
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(webhook_url, json={"content": message})
        # Treat 2xx as success; raise on anything else so the caller logs it.
        if resp.status_code >= 300:
            raise RuntimeError(
                f"Discord webhook returned {resp.status_code}: {resp.text[:200]}"
            )
