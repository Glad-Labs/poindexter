"""Read + summarize quality-probe findings for the operator console.

Findings are ``audit_log`` rows with ``event_type='finding'``, emitted by
:func:`utils.findings.emit_finding` (``details = {kind, title, body, ...}``).
This wraps the same query the ``findings_list`` MCP tool uses, but returns a
STRUCTURED summary — per-finding routed/pending status + delivery policy, plus
emitted/pending counts and by-kind / by-severity rollups — for
``GET /api/findings``. It is the Findings-dashboard data (#461), console-shaped.

Routing status mirrors the ``findings_alert_router`` job:

- ``routed``   — already forwarded (``id <= findings_alert_route_watermark``)
- ``PENDING``  — routable severity, above the watermark, awaiting delivery
- ``log-only`` — info severity (never paged) or kind policy ``log_only``

Delivery policy comes from ``app_settings`` keys ``findings.<kind>.delivery``;
an unconfigured kind shows as ``route`` (the router forwards it loud). A
``log_only`` kind at ``critical`` severity is surfaced as
``route (critical override)`` because the router refuses to suppress it.
"""

from __future__ import annotations

import json
from typing import Any

# Severities the alert router will forward (everything else is log-only).
ROUTABLE_SEVERITIES = ("warn", "warning", "critical")


def _delivery_for(kind: str, severity: str, delivery_by_kind: dict[str, str]) -> str:
    delivery = delivery_by_kind.get(kind, "route")
    if delivery == "log_only" and severity == "critical":
        return "route (critical override)"
    return delivery


def _status_for(severity: str, row_id: int, watermark: int) -> str:
    if severity not in ROUTABLE_SEVERITIES:
        return "log-only"
    if row_id <= watermark:
        return "routed"
    return "PENDING"


async def read_findings(
    pool: Any,
    *,
    kind: str = "",
    severity: str = "",
    hours: int = 168,
    pending_only: bool = False,
    limit: int = 30,
) -> dict[str, Any]:
    """Return a structured findings summary for the operator console.

    Args mirror the ``findings_list`` MCP tool. ``hours`` is clamped to
    1..720, ``limit`` to 1..100. ``pending_only`` narrows the detail rows to
    routable findings above the route watermark (still awaiting delivery); the
    ``counts`` / ``by_kind`` / ``by_severity`` rollups always cover the full
    window so the operator sees the whole picture.
    """
    hours = max(1, min(int(hours), 720))
    limit = max(1, min(int(limit), 100))

    # Route watermark: highest audit_log.id the router has forwarded.
    wm_row = await pool.fetchrow(
        "SELECT NULLIF(value, '')::bigint AS wm FROM app_settings "
        "WHERE key = 'findings_alert_route_watermark'"
    )
    watermark = int(wm_row["wm"]) if wm_row and wm_row["wm"] is not None else 0

    # Per-kind delivery policies (findings.<kind>.delivery). findings.default is
    # ignored — the router routes unconfigured kinds loud, mirrored here.
    pol_rows = await pool.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE 'findings.%.delivery'"
    )
    delivery_by_kind: dict[str, str] = {}
    for r in pol_rows:
        parts = r["key"].split(".")
        if len(parts) == 3 and parts[1] not in ("", "default"):
            delivery_by_kind[parts[1]] = r["value"]

    # Shared window/kind/severity filter — built once, reused for the detail
    # SELECT and the aggregate COUNT / GROUP BY queries.
    conditions = [
        "event_type = 'finding'",
        "timestamp > NOW() - $1 * INTERVAL '1 hour'",
    ]
    params: list[Any] = [hours]
    idx = 2
    if kind:
        conditions.append(f"details->>'kind' = ${idx}")
        params.append(kind)
        idx += 1
    if severity:
        sev = severity.strip().lower()
        if sev in ("warn", "warning"):
            conditions.append(f"LOWER(severity) = ANY(${idx}::text[])")
            params.append(["warn", "warning"])
        else:
            conditions.append(f"LOWER(severity) = ${idx}")
            params.append(sev)
        idx += 1
    base_where = " AND ".join(conditions)

    # Detail rows (latest `limit`, optionally pending-only).
    detail_conditions = list(conditions)
    detail_params = list(params)
    didx = idx
    if pending_only:
        detail_conditions.append(f"id > ${didx}")
        detail_params.append(watermark)
        didx += 1
        detail_conditions.append(f"LOWER(severity) = ANY(${didx}::text[])")
        detail_params.append(list(ROUTABLE_SEVERITIES))
        didx += 1
    detail_where = " AND ".join(detail_conditions)
    detail_params.append(limit)
    rows = await pool.fetch(
        f"SELECT id, timestamp, source, severity, details FROM audit_log "
        f"WHERE {detail_where} ORDER BY id DESC LIMIT ${didx}",
        *detail_params,
    )

    findings: list[dict[str, Any]] = []
    for row in rows:
        details = (
            json.loads(row["details"])
            if isinstance(row["details"], str)
            else (row["details"] or {})
        )
        fkind = details.get("kind") or "?"
        sev = (row["severity"] or "info").lower()
        findings.append(
            {
                "id": row["id"],
                "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                "source": row["source"],
                "severity": sev,
                "kind": fkind,
                "title": (details.get("title") or "").strip(),
                "status": _status_for(sev, row["id"], watermark),
                "delivery": _delivery_for(fkind, sev, delivery_by_kind),
            }
        )

    # Aggregate rollups over the full window (filtered, no row limit).
    emitted = await pool.fetchval(f"SELECT COUNT(*) FROM audit_log WHERE {base_where}", *params)
    pending = await pool.fetchval(
        f"SELECT COUNT(*) FROM audit_log WHERE {base_where} AND id > ${idx} "
        f"AND LOWER(severity) = ANY(${idx + 1}::text[])",
        *params,
        watermark,
        list(ROUTABLE_SEVERITIES),
    )
    by_kind_rows = await pool.fetch(
        f"SELECT COALESCE(details->>'kind', '?') AS kind, COUNT(*) AS c "
        f"FROM audit_log WHERE {base_where} GROUP BY 1 ORDER BY c DESC LIMIT 20",
        *params,
    )
    by_sev_rows = await pool.fetch(
        f"SELECT LOWER(severity) AS severity, COUNT(*) AS c "
        f"FROM audit_log WHERE {base_where} GROUP BY 1 ORDER BY c DESC",
        *params,
    )

    return {
        "findings": findings,
        "counts": {"emitted": int(emitted or 0), "pending": int(pending or 0)},
        "by_kind": [{"kind": r["kind"], "count": int(r["c"])} for r in by_kind_rows],
        "by_severity": [{"severity": r["severity"], "count": int(r["c"])} for r in by_sev_rows],
        "delivery_by_kind": delivery_by_kind,
        "watermark": watermark,
        "hours": hours,
    }
