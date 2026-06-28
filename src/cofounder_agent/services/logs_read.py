"""Loki log proxy read-service for the operator console.

``GET /api/logs`` (``routes/logs_routes.py``) is a thin serializer over this; the
HTTP-proxy logic lives here so the route stays a single-collaborator adapter
(mirrors ``services/findings_read.py`` behind ``routes/findings_routes.py``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

# `service` is a real Loki label on this stack (verified via list_loki_label_names).
_DEFAULT_SELECTOR = '{service=~".+"}'
_MAX_LIMIT = 1000


def _ns_to_iso(ns: str) -> str:
    try:
        secs = int(ns) / 1_000_000_000
        return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return ""


def build_logql(query: str, service: str, level: str) -> str:
    """Full ``query`` wins; otherwise build a selector with optional label matchers."""
    if query.strip():
        return query.strip()
    if not service and not level:
        return _DEFAULT_SELECTOR
    matchers = ['service=~".+"']
    if service:
        matchers.append(f'service="{service}"')
    if level:
        matchers.append(f'level="{level}"')
    return "{" + ",".join(matchers) + "}"


def flatten_streams(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    result = (((payload or {}).get("data") or {}).get("result")) or []
    for stream in result:
        labels = stream.get("stream") or {}
        svc = labels.get("service") or labels.get("service_name") or labels.get("container") or ""
        lvl = labels.get("level") or ""
        for entry in stream.get("values") or []:
            if not entry or len(entry) < 2:
                continue
            out.append({"ts": _ns_to_iso(entry[0]), "service": svc, "level": lvl, "line": entry[1]})
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out[:limit]


async def read_logs(
    client: httpx.AsyncClient,
    *,
    loki_url: str,
    query: str = "",
    service: str = "",
    level: str = "",
    since: str = "1h",
    limit: int = 500,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), _MAX_LIMIT))
    logql = build_logql(query, service, level)
    params = {"query": logql, "since": since, "limit": str(limit), "direction": "backward"}
    resp = await client.get(
        loki_url.rstrip("/") + "/loki/api/v1/query_range", params=params, timeout=15.0
    )
    resp.raise_for_status()
    lines = flatten_streams(resp.json(), limit)
    return {"lines": lines, "stats": {"count": len(lines), "query": logql}}
