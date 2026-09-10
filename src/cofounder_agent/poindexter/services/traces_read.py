"""Langfuse trace proxy read-service for the operator console.

``GET /api/traces`` (``routes/traces_routes.py``) serializes this. The Langfuse
public + secret keys are read server-side (``get_secret``) and used as HTTP Basic
auth; they never reach the browser. Raises :class:`LangfuseNotConfigured` when
unset so the route can fail loud (``feedback_no_silent_defaults``) with a 503.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

_MAX_LIMIT = 200


class LangfuseNotConfigured(RuntimeError):
    """Raised when langfuse_host / keys are unset — surfaced by the route as 503."""


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def map_trace(t: dict[str, Any], public_url: str) -> dict[str, Any]:
    # ``public_url`` is the BROWSER-facing Langfuse base (langfuse_public_url,
    # e.g. http://localhost:3010) — NOT the server-side ``langfuse_host``
    # (http://langfuse-web:3000), which is a Docker-internal name the operator's
    # browser can't resolve. Building the deeplink from host sent the "waterfall"
    # button to an unreachable address.
    tid = t.get("id") or ""
    meta = t.get("metadata") or {}
    qa: float | None = None
    for s in t.get("scores") or []:
        qa = _num((s or {}).get("value"))
        if qa is not None:
            break
    latency = _num(t.get("latency"))
    cost = _num(t.get("totalCost") if "totalCost" in t else t.get("cost"))
    return {
        "id": tid,
        "name": t.get("name") or "",
        "model": meta.get("model") or t.get("model") or "",
        "latency_ms": round(latency * 1000) if latency is not None else None,
        "cost_usd": cost,
        "qa_score": qa,
        "task_id": meta.get("task_id") or t.get("sessionId") or "",
        "timestamp": t.get("timestamp") or "",
        "web_url": f"{public_url.rstrip('/')}/trace/{tid}" if public_url and tid else "",
    }


async def read_traces(
    client: httpx.AsyncClient,
    *,
    host: str,
    public_key: str,
    secret_key: str,
    public_url: str = "",
    hours: int = 24,
    limit: int = 50,
    task_id: str = "",
) -> dict[str, Any]:
    # ``host`` is the server-side base for the API call below; ``public_url`` is
    # the browser-facing base for trace deeplinks. When unset we fall back to
    # ``host`` (preserves prior behaviour rather than emptying the link).
    if not host or not public_key or not secret_key:
        raise LangfuseNotConfigured(
            "Langfuse not configured — set langfuse_host + langfuse_public_key + "
            "langfuse_secret_key (poindexter setup / set_secret) to view traces."
        )
    limit = max(1, min(int(limit), _MAX_LIMIT))
    from_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    params: dict[str, str] = {"limit": str(limit), "fromTimestamp": from_ts}
    if task_id:
        params["sessionId"] = task_id
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    resp = await client.get(
        host.rstrip("/") + "/api/public/traces",
        params=params,
        headers={"Authorization": "Basic " + auth},
        timeout=15.0,
    )
    resp.raise_for_status()
    link_base = public_url or host
    rows = [map_trace(t, link_base) for t in (resp.json().get("data") or [])]
    return {"traces": rows, "stats": {"count": len(rows)}}
