"""FindingsAlertRouterJob — bridge ``audit_log`` findings to ``alert_events``.

Closes the long-standing silent-route gap described in
``utils/findings.py``'s docstring:

    "no automatic delivery. The audit_log row IS the finding. Triage
    manually via SQL until the dispatcher lands."

Captured 2026-05-15: ``emit_finding(severity='critical')`` calls have
been writing to ``audit_log`` for months — 108 critical findings in the
last 7 days alone — but the brain's ``alert_dispatcher`` polls
``alert_events``, not ``audit_log``, so none of those criticals ever
reached an operator. The intended ``critical -> Telegram`` /
``warn -> Discord`` routing matrix (per
``feedback_telegram_vs_discord``) was a no-op.

This job is the missing bridge. Every minute it:

1. Reads the persisted watermark
   (``app_settings.findings_alert_route_watermark`` — the highest
   ``audit_log.id`` we've already routed).
2. Selects up to 200 new ``severity in ('warn','warning','critical')``
   findings with ``id > watermark``.
3. Inserts one ``alert_events`` row per finding using a stable
   fingerprint derived from ``details->>'dedup_key'`` (falling back to
   ``source:kind``).
4. Advances the watermark.

The brain's existing ``alert_dispatcher`` then handles delivery AND
dedup — its ``alert_dedup_state`` table will collapse repeated fires
of the same fingerprint into one operator page + suppressed counter,
so a chronic finding (e.g. ``media_drift`` every 15 min) doesn't spam
the operator. See ``brain/alert_dispatcher.py``.

Severity normalization: ``warn`` -> ``warning`` so the dispatcher's
severity matrix matches its existing routing tables (the codebase has
both shapes — Prometheus convention is ``warning``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)

# How many findings to forward per cycle. With the bridge running every
# 60s a backlog of thousands clears in minutes, so cap each cycle to
# bound the transaction time. The watermark guarantees no row is
# processed twice.
_BATCH_LIMIT = 200

# Watermark key. Stored in app_settings as a stringified bigint so the
# operator can inspect / reset it from psql without touching code.
_WATERMARK_KEY = "findings_alert_route_watermark"

# audit_log.severity values that should be routed to operators. ``info``
# stays out of alert_events — it's queryable via SQL but doesn't page.
_ROUTABLE_SEVERITIES = ("warn", "warning", "critical")


def _normalize_severity(raw: str) -> str:
    """Map emit_finding severities to the Prometheus convention used by
    alert_events. ``warn`` -> ``warning`` so the dispatcher's severity
    matrix matches; ``critical`` stays as-is. Anything else passes through
    so we don't lose information (the dispatcher logs unknown severities)."""
    s = (raw or "").strip().lower()
    if s == "warn":
        return "warning"
    return s


def _build_fingerprint(source: str, details: dict[str, Any]) -> str:
    """Stable identifier for the alert_dispatcher's dedup engine.

    Prefer the caller-provided ``dedup_key`` (per ``emit_finding``'s
    contract — already designed for cross-fire stability). Fall back to
    ``source:kind`` which is stable per kind of finding but coarser.
    """
    dk = details.get("dedup_key")
    if dk:
        return f"finding:{source}:{dk}"
    kind = details.get("kind") or "unknown"
    return f"finding:{source}:{kind}"


def _build_alertname(source: str, details: dict[str, Any]) -> str:
    """Human-readable alertname. The dispatcher uses this in Discord /
    Telegram embeds. Keeping ``source:kind`` keeps the operator's mental
    model 1-to-1 with the audit_log row."""
    kind = details.get("kind") or "finding"
    return f"{source}:{kind}"


async def _read_watermark(pool: Any) -> int:
    """Fetch the last-routed audit_log.id. Returns 0 if missing or
    unparseable (fresh install / corrupted row — the bridge will replay
    everything from the start, which is what we want)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1",
            _WATERMARK_KEY,
        )
    if row is None or not row["value"]:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        logger.warning(
            "[findings_alert_router] watermark %r unparseable; resetting to 0",
            row["value"],
        )
        return 0


async def _write_watermark(pool: Any, new_id: int) -> None:
    """Advance the watermark via UPSERT. Idempotent on the same id."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, updated_at)
            VALUES ($1, $2, 'plugin_telemetry', $3, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = NOW()
            """,
            _WATERMARK_KEY,
            str(new_id),
            (
                "Highest audit_log.id already forwarded to alert_events by "
                "FindingsAlertRouterJob. Operators can reset to 0 to replay."
            ),
        )


async def _fetch_unrouted_findings(pool: Any, watermark: int) -> list[dict[str, Any]]:
    """Pull up to ``_BATCH_LIMIT`` un-routed findings above the watermark."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, source, severity, details
            FROM audit_log
            WHERE event_type = 'finding'
              AND severity = ANY($1::text[])
              AND id > $2
            ORDER BY id ASC
            LIMIT $3
            """,
            list(_ROUTABLE_SEVERITIES),
            watermark,
            _BATCH_LIMIT,
        )
    return [dict(r) for r in rows]


async def _insert_alert_event(pool: Any, finding: dict[str, Any]) -> None:
    """Insert one ``alert_events`` row mirroring the existing probe
    patterns in ``brain/mcp_http_probe.py`` and friends. Dispatcher takes
    over from here — picks channel by severity, dedup by fingerprint."""
    details_raw = finding.get("details") or {}
    if isinstance(details_raw, str):
        try:
            details_raw = json.loads(details_raw)
        except json.JSONDecodeError:
            details_raw = {}
    if not isinstance(details_raw, dict):
        details_raw = {}

    source = finding["source"] or "unknown"
    alertname = _build_alertname(source, details_raw)
    severity = _normalize_severity(finding.get("severity") or "info")
    fingerprint = _build_fingerprint(source, details_raw)

    labels = json.dumps({
        "source": source,
        "kind": details_raw.get("kind") or "finding",
        "audit_log_id": finding["id"],
    })
    annotations = json.dumps({
        "summary": details_raw.get("title") or alertname,
        "description": (details_raw.get("body") or "")[:4000],
    })

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO alert_events (
                alertname, status, severity, category,
                labels, annotations, fingerprint
            ) VALUES (
                $1, 'firing', $2, 'finding',
                $3::jsonb, $4::jsonb, $5
            )
            """,
            alertname,
            severity,
            labels,
            annotations,
            fingerprint,
        )


class FindingsAlertRouterJob:
    """Periodic bridge: ``audit_log`` findings -> ``alert_events``."""

    name = "findings_alert_router"
    schedule = "every 60 seconds"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        del config  # uniform job signature; this job has no tunables yet
        watermark = await _read_watermark(pool)
        rows = await _fetch_unrouted_findings(pool, watermark)
        if not rows:
            return JobResult(
                ok=True,
                detail=f"no new findings above watermark {watermark}",
                changes_made=0,
            )

        routed = 0
        errors = 0
        max_id = watermark
        for r in rows:
            try:
                await _insert_alert_event(pool, r)
                routed += 1
            except Exception as exc:
                # Don't advance the watermark past a failed row — next
                # cycle will retry. Log but continue so a single bad row
                # doesn't block the rest of the batch.
                errors += 1
                logger.warning(
                    "[findings_alert_router] alert_events insert failed for "
                    "audit_log.id=%s source=%s: %s",
                    r["id"], r.get("source"), exc,
                )
                continue
            max_id = max(max_id, int(r["id"]))

        if max_id > watermark:
            await _write_watermark(pool, max_id)

        return JobResult(
            ok=errors == 0,
            detail=(
                f"routed {routed} finding(s), {errors} error(s); "
                f"watermark {watermark} -> {max_id}"
            ),
            changes_made=routed,
        )
