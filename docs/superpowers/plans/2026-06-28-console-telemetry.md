# Console Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring Loki logs, Langfuse traces, and embedded Grafana history/DB panels into the operator console so Grafana never has to be opened directly.

**Architecture:** Two thin worker proxy routes (`/api/logs` over Loki, `/api/traces` over Langfuse) each backed by a single-collaborator read-service (mirroring `findings_read.py`), plus a new console "Telemetry" rail surface with native Logs + Traces panels and iframe-embedded Grafana panels. Logs/traces render natively; the Langfuse waterfall and Grafana history charts are reached by reference (deeplink / iframe).

**Tech Stack:** FastAPI + httpx (worker), React-via-in-browser-Babel (console, no build step), Node `node:test` for pure console modules, pytest for the worker.

## Global Constraints

- **Async everywhere** — worker routes and read-services are `async def`; never block the loop.
- **No inline SQL in `routes/`** — these are HTTP proxies (no SQL at all), satisfying the adapter-purity ratchet by construction.
- **Auth on every `/api` route** — `dependencies=[Depends(verify_api_token)]` on the router; an unauthenticated request must 401.
- **Fail loud, no silent defaults** (`feedback_no_silent_defaults`) — missing Langfuse config returns a 503 with a remediation message, never an empty 200.
- **No dummy data** (`feedback_no_dummy_data`) — live panels render honest-empty (`—` / empty list) when a read is missing; mock data lives only on the mock branch.
- **Secrets stay server-side** — `langfuse_public_key` / `langfuse_secret_key` are read via `await site_config.get_secret(...)`; never returned to the browser. They are NOT added to `settings_defaults.py` (keys matching `*_key`/`*_secret` are auto-excluded + auto-classified secret).
- **Console has no build step** — pure logic goes in dual-mode `.js` modules (browser global + `module.exports`, like `js/kpis.js`); React components are `.jsx` compiled in-browser.
- **Docs + tests ship in the same change** (`feedback_docs_and_tests_default`).
- **Loki facts (verified live):** datasource uid `local-loki`; labels include `service`, `level`, `container`, `service_name`, `compose_project`, `stream`. Default selector `{service=~".+"}`; filter by `level` / `service` label matchers.
- **All work on branch `claude/happy-chaplygin-fddc7b`; PR to `Glad-Labs/glad-labs-stack`, never main direct.**

---

### Task 1: Loki logs read-service

**Files:**

- Create: `src/cofounder_agent/services/logs_read.py`
- Test: `src/cofounder_agent/tests/unit/services/test_logs_read.py`

**Interfaces:**

- Produces: `async read_logs(client: httpx.AsyncClient, *, loki_url: str, query: str = "", service: str = "", level: str = "", since: str = "1h", limit: int = 500) -> dict` returning `{"lines": [{"ts","service","level","line"}], "stats": {"count","query"}}`. Also `build_logql(query, service, level) -> str` and `flatten_streams(payload: dict, limit: int) -> list[dict]` (pure).

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/services/test_logs_read.py
"""Unit tests for the Loki log proxy read-service (services/logs_read.py)."""
from __future__ import annotations

import httpx
import pytest

from services.logs_read import build_logql, flatten_streams, read_logs

_LOKI_PAYLOAD = {
    "status": "success",
    "data": {
        "resultType": "streams",
        "result": [
            {
                "stream": {"service": "poindexter-worker", "level": "error"},
                "values": [
                    ["1719500000000000000", "boom one"],
                    ["1719500001000000000", "boom two"],
                ],
            },
            {
                "stream": {"service": "poindexter-brain", "level": "info"},
                "values": [["1719500002000000000", "tick"]],
            },
        ],
    },
}


@pytest.mark.unit
def test_build_logql_defaults_to_service_selector():
    assert build_logql("", "", "") == '{service=~".+"}'


@pytest.mark.unit
def test_build_logql_appends_service_and_level_matchers():
    out = build_logql("", "poindexter-worker", "error")
    assert out == '{service=~".+",service="poindexter-worker",level="error"}'


@pytest.mark.unit
def test_build_logql_passthrough_when_full_query_given():
    assert build_logql('{container="x"}', "ignored", "ignored") == '{container="x"}'


@pytest.mark.unit
def test_flatten_streams_maps_sorts_desc_and_caps():
    rows = flatten_streams(_LOKI_PAYLOAD, limit=2)
    assert len(rows) == 2
    # newest first
    assert rows[0]["line"] == "tick"
    assert rows[0]["service"] == "poindexter-brain"
    assert rows[0]["level"] == "info"
    assert rows[0]["ts"].startswith("2026-")  # ns → ISO


@pytest.mark.unit
async def test_read_logs_proxies_and_clamps_limit():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_LOKI_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await read_logs(
            client, loki_url="http://loki:3100", level="error", since="2h", limit=99999
        )
    assert out["stats"]["count"] == len(out["lines"])
    assert "limit=1000" in seen["url"]  # hard cap
    assert "since=2h" in seen["url"]
    assert "/loki/api/v1/query_range" in seen["url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_logs_read.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.logs_read'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cofounder_agent/services/logs_read.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_logs_read.py -q`
Expected: PASS (5 passed). If the async test errors with "async def not natively supported", confirm `asyncio_mode = auto` is set in the project's pytest config (it is for this repo); otherwise add `@pytest.mark.asyncio`.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/logs_read.py src/cofounder_agent/tests/unit/services/test_logs_read.py
git commit -m "feat(console): Loki log proxy read-service (read_logs)"
```

---

### Task 2: `/api/logs` route + registration

**Files:**

- Create: `src/cofounder_agent/routes/logs_routes.py`
- Modify: `src/cofounder_agent/utils/route_registration.py` (add to `_WORKER_ROUTES`)
- Test: `src/cofounder_agent/tests/unit/routes/test_logs_routes.py`

**Interfaces:**

- Consumes: `services.logs_read.read_logs` (Task 1).
- Produces: `GET /api/logs?query=&service=&level=&since=&limit=` → the `read_logs` dict. Router object `routes.logs_routes.router`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/routes/test_logs_routes.py
"""Unit tests for the /api/logs HTTP route (Loki proxy)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.logs_routes import router
from utils.route_utils import get_site_config_dependency

SAMPLE = {"lines": [{"ts": "2026-06-27T00:00:00+00:00", "service": "poindexter-worker", "level": "error", "line": "boom"}], "stats": {"count": 1, "query": '{service=~".+"}'}}


def _build_app(*, authed=True):
    app = FastAPI()
    app.include_router(router)
    sc = MagicMock()
    sc.get.return_value = "http://loki:3100"
    app.dependency_overrides[get_site_config_dependency] = lambda: sc
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


@pytest.mark.unit
def test_returns_logs_payload():
    app = _build_app()
    with patch("routes.logs_routes.read_logs", new=AsyncMock(return_value=SAMPLE)) as m:
        res = TestClient(app).get("/api/logs?service=poindexter-worker&level=error")
    assert res.status_code == 200
    assert res.json() == SAMPLE
    # query params forwarded to the read-service
    assert m.await_args.kwargs["service"] == "poindexter-worker"
    assert m.await_args.kwargs["level"] == "error"


@pytest.mark.unit
def test_requires_auth():
    app = _build_app(authed=False)
    res = TestClient(app).get("/api/logs")
    assert res.status_code == 401


@pytest.mark.unit
def test_clamps_limit_via_query_validation():
    app = _build_app()
    res = TestClient(app).get("/api/logs?limit=99999")
    assert res.status_code == 422  # Query(le=1000) rejects out-of-range
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/routes/test_logs_routes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'routes.logs_routes'`

- [ ] **Step 3: Write the route**

```python
# src/cofounder_agent/routes/logs_routes.py
"""Loki log proxy — ``GET /api/logs`` for the operator console.

Thin serializer over :func:`services.logs_read.read_logs` (the HTTP-proxy logic
lives there). Mirrors ``routes/findings_routes.py``: the route does auth + param
validation, the read-service does the work. No SQL — it's an HTTP proxy.
"""
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from services.logs_read import read_logs
from utils.route_utils import get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/logs",
    tags=["logs"],
    dependencies=[Depends(verify_api_token)],
)


@router.get("", response_model=dict[str, Any], summary="Loki log tail/search for the console")
async def list_logs(
    request: Request,
    token: str = Depends(verify_api_token),
    site_config: Any = Depends(get_site_config_dependency),
    query: str = Query("", description="Full LogQL selector; overrides service/level"),
    service: str = Query("", description="Filter by the `service` Loki label"),
    level: str = Query("", description="Filter by the `level` Loki label"),
    since: str = Query("1h", description="Look-back window (Loki duration, e.g. 1h, 30m)"),
    limit: int = Query(500, ge=1, le=1000, description="Max log lines"),
) -> dict[str, Any]:
    loki_url = site_config.get("data_fabric_loki_url", "http://loki:3100")
    client = getattr(request.app.state, "http_client", None)
    if client is not None:
        return await read_logs(
            client, loki_url=loki_url, query=query, service=service, level=level, since=since, limit=limit
        )
    async with httpx.AsyncClient() as c:
        return await read_logs(
            c, loki_url=loki_url, query=query, service=service, level=level, since=since, limit=limit
        )
```

- [ ] **Step 4: Register the router**

In `src/cofounder_agent/utils/route_registration.py`, add this line to the `_WORKER_ROUTES` list, immediately after the `findings_routes` entry (around line 77):

```python
    ("routes.logs_routes", "router", "logs_router", "Loki log proxy for the operator console (/api/logs)"),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/routes/test_logs_routes.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/routes/logs_routes.py src/cofounder_agent/utils/route_registration.py src/cofounder_agent/tests/unit/routes/test_logs_routes.py
git commit -m "feat(console): /api/logs Loki proxy route + registration"
```

---

### Task 3: Langfuse traces read-service

**Files:**

- Create: `src/cofounder_agent/services/traces_read.py`
- Test: `src/cofounder_agent/tests/unit/services/test_traces_read.py`

**Interfaces:**

- Produces: `async read_traces(client, *, host, public_key, secret_key, hours=24, limit=50, task_id="") -> dict` returning `{"traces": [{"id","name","model","latency_ms","cost_usd","qa_score","task_id","timestamp","web_url"}], "stats": {"count"}}`. Exception class `LangfuseNotConfigured(RuntimeError)`. Pure helper `map_trace(t: dict, host: str) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/services/test_traces_read.py
"""Unit tests for the Langfuse trace proxy read-service (services/traces_read.py)."""
from __future__ import annotations

import base64

import httpx
import pytest

from services.traces_read import LangfuseNotConfigured, map_trace, read_traces

_TRACE = {
    "id": "abc123",
    "name": "qa_pass",
    "timestamp": "2026-06-27T10:00:00Z",
    "latency": 2.5,
    "totalCost": 0.0123,
    "metadata": {"model": "gemma-4-31b", "task_id": "task-9"},
    "scores": [{"name": "g_eval", "value": 87}],
}


@pytest.mark.unit
def test_map_trace_builds_row_and_deeplink():
    row = map_trace(_TRACE, "http://localhost:3010")
    assert row["id"] == "abc123"
    assert row["model"] == "gemma-4-31b"
    assert row["latency_ms"] == 2500
    assert row["cost_usd"] == 0.0123
    assert row["qa_score"] == 87
    assert row["task_id"] == "task-9"
    assert row["web_url"] == "http://localhost:3010/trace/abc123"


@pytest.mark.unit
async def test_read_traces_raises_when_unconfigured():
    async with httpx.AsyncClient() as client:
        with pytest.raises(LangfuseNotConfigured):
            await read_traces(client, host="", public_key="", secret_key="")


@pytest.mark.unit
async def test_read_traces_sends_basic_auth_and_maps():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [_TRACE]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await read_traces(
            client, host="http://localhost:3010", public_key="pk", secret_key="sk", limit=10
        )
    expect = "Basic " + base64.b64encode(b"pk:sk").decode()
    assert seen["auth"] == expect
    assert "/api/public/traces" in seen["url"]
    assert out["stats"]["count"] == 1
    assert out["traces"][0]["web_url"].endswith("/trace/abc123")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_traces_read.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.traces_read'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cofounder_agent/services/traces_read.py
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


def map_trace(t: dict[str, Any], host: str) -> dict[str, Any]:
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
        "web_url": f"{host.rstrip('/')}/trace/{tid}" if host and tid else "",
    }


async def read_traces(
    client: httpx.AsyncClient,
    *,
    host: str,
    public_key: str,
    secret_key: str,
    hours: int = 24,
    limit: int = 50,
    task_id: str = "",
) -> dict[str, Any]:
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
    rows = [map_trace(t, host) for t in (resp.json().get("data") or [])]
    return {"traces": rows, "stats": {"count": len(rows)}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_traces_read.py -q`
Expected: PASS (3 passed)

> **NOTE for the implementer:** the Langfuse field names (`latency`, `totalCost`, `scores[].value`, `metadata.model`) are mapped defensively (`_num` + fallbacks) so a version mismatch degrades to `None`, never a crash. Before wiring the console live, confirm the real field names with one authenticated call: `curl -u "$PUBLIC:$SECRET" "$LANGFUSE_HOST/api/public/traces?limit=1"` and adjust `map_trace` if a field differs. This is a verification, not a code change unless the shape differs.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/traces_read.py src/cofounder_agent/tests/unit/services/test_traces_read.py
git commit -m "feat(console): Langfuse trace proxy read-service (read_traces)"
```

---

### Task 4: `/api/traces` route + registration

**Files:**

- Create: `src/cofounder_agent/routes/traces_routes.py`
- Modify: `src/cofounder_agent/utils/route_registration.py` (add to `_WORKER_ROUTES`)
- Test: `src/cofounder_agent/tests/unit/routes/test_traces_routes.py`

**Interfaces:**

- Consumes: `services.traces_read.read_traces`, `LangfuseNotConfigured` (Task 3).
- Produces: `GET /api/traces?hours=&limit=&task_id=` → the `read_traces` dict, or 503 when Langfuse is unconfigured. Router `routes.traces_routes.router`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/routes/test_traces_routes.py
"""Unit tests for the /api/traces HTTP route (Langfuse proxy)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.traces_routes import router
from services.traces_read import LangfuseNotConfigured
from utils.route_utils import get_site_config_dependency

SAMPLE = {"traces": [{"id": "abc", "name": "qa_pass", "model": "gemma-4-31b", "latency_ms": 2500, "cost_usd": 0.01, "qa_score": 87, "task_id": "t9", "timestamp": "2026-06-27T10:00:00Z", "web_url": "http://localhost:3010/trace/abc"}], "stats": {"count": 1}}


def _build_app(*, authed=True):
    app = FastAPI()
    app.include_router(router)
    sc = MagicMock()
    sc.get.return_value = "http://localhost:3010"
    sc.get_secret = AsyncMock(return_value="key")
    app.dependency_overrides[get_site_config_dependency] = lambda: sc
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app, sc


@pytest.mark.unit
def test_returns_traces_payload():
    app, _ = _build_app()
    with patch("routes.traces_routes.read_traces", new=AsyncMock(return_value=SAMPLE)):
        res = TestClient(app).get("/api/traces?hours=12&limit=10")
    assert res.status_code == 200
    assert res.json() == SAMPLE


@pytest.mark.unit
def test_unconfigured_returns_503():
    app, _ = _build_app()
    with patch(
        "routes.traces_routes.read_traces",
        new=AsyncMock(side_effect=LangfuseNotConfigured("set langfuse keys")),
    ):
        res = TestClient(app).get("/api/traces")
    assert res.status_code == 503
    assert "langfuse" in res.json()["detail"].lower()


@pytest.mark.unit
def test_requires_auth():
    app, _ = _build_app(authed=False)
    res = TestClient(app).get("/api/traces")
    assert res.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/routes/test_traces_routes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'routes.traces_routes'`

- [ ] **Step 3: Write the route**

```python
# src/cofounder_agent/routes/traces_routes.py
"""Langfuse trace proxy — ``GET /api/traces`` for the operator console.

Thin serializer over :func:`services.traces_read.read_traces`. Reads the Langfuse
keys as secrets (server-side only) and fails loud with a 503 when they're unset.
No SQL — it's an HTTP proxy.
"""
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from services.traces_read import LangfuseNotConfigured, read_traces
from utils.route_utils import get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/traces",
    tags=["traces"],
    dependencies=[Depends(verify_api_token)],
)


@router.get("", response_model=dict[str, Any], summary="Recent Langfuse LLM traces for the console")
async def list_traces(
    request: Request,
    token: str = Depends(verify_api_token),
    site_config: Any = Depends(get_site_config_dependency),
    hours: int = Query(24, ge=1, le=720, description="Look-back window in hours"),
    limit: int = Query(50, ge=1, le=200, description="Max traces"),
    task_id: str = Query("", description="Scope to one pipeline task (Langfuse sessionId)"),
) -> dict[str, Any]:
    host = site_config.get("langfuse_host", "")
    public_key = await site_config.get_secret("langfuse_public_key", "")
    secret_key = await site_config.get_secret("langfuse_secret_key", "")
    client = getattr(request.app.state, "http_client", None)
    try:
        if client is not None:
            return await read_traces(
                client, host=host, public_key=public_key, secret_key=secret_key,
                hours=hours, limit=limit, task_id=task_id,
            )
        async with httpx.AsyncClient() as c:
            return await read_traces(
                c, host=host, public_key=public_key, secret_key=secret_key,
                hours=hours, limit=limit, task_id=task_id,
            )
    except LangfuseNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
```

- [ ] **Step 4: Register the router**

In `src/cofounder_agent/utils/route_registration.py`, add to `_WORKER_ROUTES` immediately after the `logs_routes` entry from Task 2:

```python
    ("routes.traces_routes", "router", "traces_router", "Langfuse trace proxy for the operator console (/api/traces)"),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/routes/test_traces_routes.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/routes/traces_routes.py src/cofounder_agent/utils/route_registration.py src/cofounder_agent/tests/unit/routes/test_traces_routes.py
git commit -m "feat(console): /api/traces Langfuse proxy route + registration"
```

---

### Task 5: Console pure module — Grafana panel URL builder

**Files:**

- Create: `src/cofounder_agent/console/js/telemetry.js`
- Modify: `src/cofounder_agent/console/index.html` (add `<script>` after `js/kpis.js`)
- Test: `src/cofounder_agent/console/js/__tests__/telemetry.test.js`

**Interfaces:**

- Produces (browser global `PX.telemetry` + `module.exports`): `grafanaPanelUrl(base, uid, panelId, opts) -> string`. Dual-mode like `js/kpis.js`.

- [ ] **Step 1: Write the failing test**

```javascript
// src/cofounder_agent/console/js/__tests__/telemetry.test.js
'use strict';
// Contract tests for the console's pure Grafana-embed URL builder
// (js/telemetry.js -> PX.telemetry.grafanaPanelUrl). Runs on node:test with no
// build step (run `npm run test:console`).
const test = require('node:test');
const assert = require('node:assert/strict');
const { grafanaPanelUrl } = require('../telemetry.js');

test('builds a d-solo URL with theme + kiosk defaults', () => {
  const u = grafanaPanelUrl('http://localhost:3000', 'database', 7);
  assert.equal(
    u,
    'http://localhost:3000/d-solo/database?panelId=7&theme=dark&kiosk'
  );
});

test('strips a trailing slash on the base', () => {
  const u = grafanaPanelUrl('http://localhost:3000/', 'hardware-power', 2);
  assert.equal(u.startsWith('http://localhost:3000/d-solo/'), true);
});

test('honest-empty when base or uid missing', () => {
  assert.equal(grafanaPanelUrl('', 'x', 1), '');
  assert.equal(grafanaPanelUrl('http://x', '', 1), '');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:console`
Expected: FAIL — cannot find module `../telemetry.js`

- [ ] **Step 3: Write minimal implementation**

```javascript
// src/cofounder_agent/console/js/telemetry.js
/* Pure helpers for the console Telemetry surface. Dual-mode (browser global +
   module.exports) so it unit-tests on node:test with no build step, exactly
   like js/kpis.js. Loaded by index.html before the .jsx panels. */
(function () {
  // Compose a Grafana single-panel embed URL. Returns '' (honest-empty) when
  // base or uid is missing, so the iframe renders nothing rather than a broken src.
  function grafanaPanelUrl(base, uid, panelId, opts) {
    if (!base || !uid) return '';
    const o = opts || {};
    const theme = o.theme || 'dark';
    const root = String(base).replace(/\/+$/, '');
    let u = `${root}/d-solo/${encodeURIComponent(uid)}?panelId=${encodeURIComponent(panelId)}&theme=${theme}&kiosk`;
    if (o.from) u += `&from=${encodeURIComponent(o.from)}`;
    if (o.to) u += `&to=${encodeURIComponent(o.to)}`;
    return u;
  }

  const api = { grafanaPanelUrl };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined')
    (window.PX || (window.PX = {})).telemetry = api;
})();
```

- [ ] **Step 4: Add the script tag**

In `src/cofounder_agent/console/index.html`, after line 39 (`<script src="js/kpis.js"></script>`), add:

```html
<!-- Pure Grafana-embed URL builder (PX.telemetry) — must load before panels2.jsx. -->
<script src="js/telemetry.js"></script>
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test:console`
Expected: PASS (the 3 new telemetry tests pass alongside the existing console-unit tests)

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/console/js/telemetry.js src/cofounder_agent/console/js/__tests__/telemetry.test.js src/cofounder_agent/console/index.html
git commit -m "feat(console): pure Grafana-embed URL builder (PX.telemetry)"
```

---

### Task 6: Console adapter methods — logs(), traces(), grafana base

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js`
- Modify: `src/cofounder_agent/console/js/data.js` (mock logs + traces)

**Interfaces:**

- Consumes: `http()`, `pick()`, `pair()`, `cfg`, `mock()` (existing in api.js).
- Produces on `PX.api`: `logs(params='')`, `traces(params='')`, `grafanaBase()`, `setGrafanaEmbed(url)`. On `PX` (mock): `PX.logs`, `PX.traces`.

- [ ] **Step 1: Add mock data to `js/data.js`**

Add these to the `PX` mock object in `src/cofounder_agent/console/js/data.js` (next to `PX.findings` / other mock surfaces — match the file's existing assignment style):

```javascript
// Mock telemetry — only used in mock mode (live polls /api/logs + /api/traces).
PX.logs = {
  lines: [
    {
      ts: '2026-06-27T14:31:55Z',
      service: 'poindexter-worker',
      level: 'info',
      line: 'claimed pipeline_task #1842',
    },
    {
      ts: '2026-06-27T14:31:50Z',
      service: 'poindexter-worker',
      level: 'error',
      line: 'qa.aggregate rejected — fabrication veto',
    },
    {
      ts: '2026-06-27T14:31:40Z',
      service: 'poindexter-brain',
      level: 'info',
      line: 'health cycle ok (5m)',
    },
  ],
  stats: { count: 3, query: '{service=~".+"}' },
};
PX.traces = {
  traces: [
    {
      id: 'demo-1',
      name: 'qa_pass',
      model: 'gemma-4-31b',
      latency_ms: 2480,
      cost_usd: 0,
      qa_score: 87,
      task_id: '1842',
      timestamp: '2026-06-27T14:31:00Z',
      web_url: '',
    },
    {
      id: 'demo-2',
      name: 'writer_draft',
      model: 'gemma-4-31b',
      latency_ms: 18120,
      cost_usd: 0,
      qa_score: null,
      task_id: '1842',
      timestamp: '2026-06-27T14:29:00Z',
      web_url: '',
    },
  ],
  stats: { count: 2 },
};
```

- [ ] **Step 2: Add adapter methods to `js/api.js`**

In `src/cofounder_agent/console/js/api.js`, add `grafana` to the `cfg` object (next to the `prometheus` line, ~line 63):

```javascript
    // Grafana embed base — browser hits /d-solo iframes directly, like prometheus.
    grafana: LS.getItem('px_grafana') ?? 'http://localhost:3000',
```

Then add these methods to the returned `PX.api` object (next to `findings()` ~line 595, matching the surrounding style):

```javascript
    // Loki log proxy (worker GET /api/logs). Mock → PX.logs; empty → no lines.
    logs(params = '') {
      return pick(
        () => http('GET', '/api/logs' + params),
        () => pair(mock().logs, { lines: [], stats: { count: 0, query: '' } })
      );
    },
    // Langfuse trace proxy (worker GET /api/traces). Mock → PX.traces.
    traces(params = '') {
      return pick(
        () => http('GET', '/api/traces' + params),
        () => pair(mock().traces, { traces: [], stats: { count: 0 } })
      );
    },
    // Grafana embed base (client-side, like prometheus). Read + set + persist.
    grafanaBase() {
      return cfg.grafana;
    },
    setGrafanaEmbed(u) {
      cfg.grafana = u || '';
      LS.setItem('px_grafana', cfg.grafana);
    },
```

- [ ] **Step 3: Verify the adapter loads without error**

Run: `npm run test:console`
Expected: PASS — existing console-unit tests (incl. `api.token.test.js`, which vm-evaluates `api.js`) still pass, proving the new methods don't break the IIFE.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/data.js
git commit -m "feat(console): logs()/traces()/grafana adapter methods + mock data"
```

---

### Task 7: Console panels + Telemetry section wiring

**Files:**

- Modify: `src/cofounder_agent/console/js/panels2.jsx` (add `LogsPanel`, `TracesPanel`, `GrafanaEmbed`)
- Modify: `src/cofounder_agent/console/js/app.jsx` (rail item, section, state, polling effects)

**Interfaces:**

- Consumes: `PX.api.logs/traces/grafanaBase`, `PX.telemetry.grafanaPanelUrl`, existing `Icon`, `panel` CSS classes, `relAge`/`PX.ago`.
- Produces: rendered Telemetry section under a new `telemetry` rail id.

- [ ] **Step 1: Add the three panel components to `js/panels2.jsx`**

Append to `src/cofounder_agent/console/js/panels2.jsx` (these are presentational; they read props only). Match the file's existing component style (the `panel` / `panel__head` / `panel__title` classes used by other panels):

```jsx
// ── Telemetry: Loki logs (native) ─────────────────────────────
function LogsPanel({ logs, onFilter, service, level }) {
  const lines = (logs && logs.lines) || [];
  const tone = (lv) =>
    lv === 'error'
      ? 'c-red'
      : lv === 'warn' || lv === 'warning'
        ? 'c-amber'
        : 'c-dim';
  return (
    <div className="panel" id="sec-logs">
      <div className="panel__head">
        <span className="panel__title">
          <span className="idx">▤</span>LOGS
        </span>
        <span className="panel__spacer" style={{ flex: 1 }} />
        <span className="panel__meta">{lines.length} lines · Loki</span>
      </div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        <input
          className="tinput"
          placeholder="service (e.g. poindexter-worker)"
          defaultValue={service || ''}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onFilter({ service: e.target.value });
          }}
        />
        {['', 'info', 'warn', 'error'].map((lv) => (
          <button
            key={lv || 'all'}
            className={`chip ${level === lv ? 'is-active' : ''}`}
            onClick={() => onFilter({ level: lv })}
          >
            {lv || 'all'}
          </button>
        ))}
      </div>
      {lines.length === 0 ? (
        <div className="empty">no log lines — adjust filters or check Loki</div>
      ) : (
        <div className="logfeed">
          {lines.map((l, i) => (
            <div className="logline" key={i}>
              <span className="c-dim tnum">{(l.ts || '').slice(11, 19)}</span>{' '}
              <span className="c-cyan">{l.service}</span>{' '}
              <span className={tone(l.level)}>
                {(l.level || '').toUpperCase()}
              </span>{' '}
              <span>{l.line}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Telemetry: Langfuse traces (native list, deeplink waterfall) ──
function TracesPanel({ traces }) {
  const rows = (traces && traces.traces) || [];
  return (
    <div className="panel" id="sec-traces">
      <div className="panel__head">
        <span className="panel__title">
          <span className="idx">⌁</span>LLM TRACES
        </span>
        <span className="panel__spacer" style={{ flex: 1 }} />
        <span className="panel__meta">{rows.length} · Langfuse</span>
      </div>
      {rows.length === 0 ? (
        <div className="empty">
          no traces — set langfuse keys or widen the window
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {rows.map((t) => (
            <div
              className="traceRow"
              key={t.id}
              style={{ display: 'flex', gap: 10, alignItems: 'center' }}
            >
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {t.name} <span className="c-dim">· {t.model || '—'}</span>
              </span>
              <span className="c-dim tnum">
                {t.latency_ms != null ? Math.round(t.latency_ms) + 'ms' : '—'}
              </span>
              <span className="c-dim tnum">
                {t.qa_score != null ? 'Q' + Math.round(t.qa_score) : '—'}
              </span>
              <button
                className="mbtn"
                disabled={!t.web_url}
                onClick={() =>
                  t.web_url &&
                  window.open(t.web_url, '_blank', 'noopener,noreferrer')
                }
                title={
                  t.web_url ? 'Open waterfall in Langfuse' : 'No Langfuse URL'
                }
              >
                waterfall ↗
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Telemetry: embedded Grafana panels (history + DB) ─────────
// UIDs/panelIds are the local Grafana boards. Confirm each uid via
// search_dashboards / the board URL before relying on it (Step 4 below).
const GRAFANA_EMBEDS = [
  { uid: 'cost-analytics', panelId: 2, label: 'Spend over time' },
  { uid: 'hardware-power', panelId: 4, label: 'GPU history' },
  { uid: 'database', panelId: 2, label: 'DB connections' },
];
function GrafanaEmbed() {
  const base = window.PX.api.grafanaBase();
  return (
    <div className="panel" id="sec-grafana">
      <div className="panel__head">
        <span className="panel__title">
          <span className="idx">▦</span>GRAFANA
        </span>
        <span className="panel__spacer" style={{ flex: 1 }} />
        <span className="panel__meta">history · database</span>
      </div>
      <div style={{ display: 'grid', gap: 10 }}>
        {GRAFANA_EMBEDS.map((g) => {
          const src = window.PX.telemetry.grafanaPanelUrl(
            base,
            g.uid,
            g.panelId
          );
          return src ? (
            <iframe
              key={g.uid + g.panelId}
              title={g.label}
              src={src}
              style={{
                width: '100%',
                height: 220,
                border: '1px solid var(--gl-line, rgba(255,255,255,.1))',
                borderRadius: 2,
              }}
            />
          ) : (
            <div className="empty" key={g.label}>
              set Grafana base in App Settings → Connection
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire state, polling, rail item, and section into `js/app.jsx`**

(a) Add to the `RAIL` array (app.jsx:12-24), after the `revenue` entry:

```javascript
  { id: 'telemetry', icon: 'audit', label: 'Telemetry' },
```

(b) Add state near the other `useS` calls (after the `newsletter` state ~app.jsx:41):

```javascript
const [logs, setLogs] = useS(PX.logs);
const [traces, setTraces] = useS(PX.traces);
const [logFilter, setLogFilter] = useS({ service: '', level: '' });
```

(c) Add two polling effects (next to the other `// ── Live:` effects, e.g. after the newsletter effect ~app.jsx:536). Logs re-fetch when the filter changes; traces on a fixed cadence:

```javascript
// ── Live: Loki logs (GET /api/logs) ───────────────────────
useE(() => {
  if (!PX.api.isLive()) return;
  let alive = true;
  const load = async () => {
    try {
      const qs =
        `?since=1h&limit=300` +
        (logFilter.service
          ? `&service=${encodeURIComponent(logFilter.service)}`
          : '') +
        (logFilter.level
          ? `&level=${encodeURIComponent(logFilter.level)}`
          : '');
      const res = await PX.api.logs(qs);
      if (alive && res) setLogs(res);
    } catch (_e) {
      /* honest-empty: leave panel as-is on a transient error */
    }
  };
  load();
  const id = setInterval(load, 10 * 1000);
  return () => {
    alive = false;
    clearInterval(id);
  };
}, [logFilter]);

// ── Live: Langfuse traces (GET /api/traces) ───────────────
useE(() => {
  if (!PX.api.isLive()) return;
  let alive = true;
  const load = async () => {
    try {
      const res = await PX.api.traces('?hours=24&limit=50');
      if (alive && res) setTraces(res);
    } catch (_e) {
      /* honest-empty (incl. 503 when langfuse keys unset) */
    }
  };
  load();
  const id = setInterval(load, 60 * 1000);
  return () => {
    alive = false;
    clearInterval(id);
  };
}, []);
```

(d) Render the section. Add to the masonry, after the `sec-findings` block (~app.jsx:1472-1477):

```jsx
<div id="sec-telemetry">
  <LogsPanel
    logs={logs}
    service={logFilter.service}
    level={logFilter.level}
    onFilter={(patch) => setLogFilter((f) => ({ ...f, ...patch }))}
  />
  <TracesPanel traces={traces} />
  <GrafanaEmbed />
</div>
```

- [ ] **Step 3: Verify console tests still green**

Run: `npm run test:console`
Expected: PASS (no regressions; the new code is JSX/runtime-only, covered by visual verification next).

- [ ] **Step 4: Visual verification (`feedback_visual_verification`)**

1. Start the worker (`npm run dev:cofounder`) and open `http://localhost:8002/console/`.
2. Mock mode: the new **Telemetry** rail item appears; Logs shows the 3 mock lines, Traces shows 2 rows, Grafana shows three iframes (or the "set Grafana base" empty state).
3. Confirm each Grafana embed `uid`/`panelId` in `GRAFANA_EMBEDS`: open `http://localhost:3000`, find the Cost/Hardware/Database boards, read the real dashboard uid from the URL and a panel's "Share → Embed" panelId; correct any that differ.
4. Live mode (after Task 8's Grafana config + Langfuse keys provisioned): flip live in App Settings → Connection, confirm logs tail, traces list, and iframes render. If Langfuse keys are unset, Traces stays honest-empty (the 503 is swallowed) — expected.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/panels2.jsx src/cofounder_agent/console/js/app.jsx
git commit -m "feat(console): Telemetry surface — Logs, Traces, Grafana panels"
```

---

### Task 8: Grafana embedding config + docs

**Files:**

- Modify: the Grafana service in the compose file(s) — `docker-compose.local.yml` and `docker-compose.consumer.yml` (whichever define the `grafana` service)
- Modify: `src/cofounder_agent/console/README.md`
- Modify: `CLAUDE.md` (Monitoring section — one line)

**Interfaces:** none (config + docs).

- [ ] **Step 1: Enable embedding on the Grafana service**

In each compose file's `grafana` service `environment:` block, add:

```yaml
- GF_SECURITY_ALLOW_EMBEDDING=true
- GF_AUTH_ANONYMOUS_ENABLED=true
- GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
```

Locate the service first:

Run: `grep -rl "image:.*grafana/grafana" docker-compose*.yml`
Then add the three env vars to that service's `environment:` list.

- [ ] **Step 2: Apply + verify the iframe loads**

```bash
docker compose -f docker-compose.local.yml up -d grafana
```

Then in a browser open (substitute a real board uid from your Grafana):
`http://localhost:3000/d-solo/database?panelId=2&theme=dark&kiosk`
Expected: the panel renders WITHOUT a login redirect (anonymous Viewer). If it redirects to login, re-check the three env vars applied (`docker exec poindexter-grafana env | grep GF_AUTH_ANON`).

- [ ] **Step 3: Document the surface in `console/README.md`**

Add a section after the existing API-adapter table (mirror its tone). Include:

- The new `telemetry` rail surface: Logs (native, `/api/logs` → Loki), Traces (native list, `/api/traces` → Langfuse, waterfall deeplinks out), Grafana (embedded `/d-solo` history + DB).
- The `px_grafana` Connection setting (default `http://localhost:3000`).
- That Langfuse traces require `langfuse_host` + `langfuse_public_key` + `langfuse_secret_key` (the last two are secrets — set via `poindexter setup` / `set_secret`), and that the panel is honest-empty until they're set.
- The Grafana embed prerequisites (`GF_SECURITY_ALLOW_EMBEDDING` + anonymous Viewer).

- [ ] **Step 4: Add the CLAUDE.md monitoring line**

In `CLAUDE.md`, in the **Monitoring** section's console/Grafana description, add one line noting the console now embeds logs (Loki via `/api/logs`), LLM traces (Langfuse via `/api/traces`, waterfall deeplinks out), and Grafana history/DB panels under a Telemetry tab — so day-to-day operation no longer requires opening Grafana directly.

- [ ] **Step 5: Commit**

```bash
git add docker-compose*.yml src/cofounder_agent/console/README.md CLAUDE.md
git commit -m "feat(console): enable Grafana embedding + document Telemetry surface"
```

---

## Final verification

- [ ] `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_logs_read.py tests/unit/services/test_traces_read.py tests/unit/routes/test_logs_routes.py tests/unit/routes/test_traces_routes.py -q` → all pass
- [ ] `npm run test:console` → all pass (incl. `telemetry.test.js`)
- [ ] `python scripts/ci/adapter_purity_lint.py` (or the repo's lint target) → no new inline-SQL findings (the proxies have none)
- [ ] Visual: `/console/` Telemetry tab renders Logs + Traces + Grafana in both mock and live modes
- [ ] Open a PR to `Glad-Labs/glad-labs-stack` (never main direct); let CI gate the merge

## Self-review notes (spec coverage)

- Spec §1 Logs → Tasks 1–2. Spec §2 Traces → Tasks 3–4. Spec §3 Embedded Grafana → Tasks 5, 7, 8. Settings (Langfuse secrets, `px_grafana`) → Tasks 4, 6, 8. Risks: limit/since caps (Tasks 1, 3), 503-on-missing-keys (Tasks 3–4), anonymous-Viewer embed (Task 8), brand seam (contained to the Telemetry tab, Task 7). Tests + docs ship within the tasks (`feedback_docs_and_tests_default`).
- Type consistency: `read_logs`/`read_traces` signatures and return shapes are referenced identically in their routes and tests; `grafanaPanelUrl(base, uid, panelId, opts)` is consistent across telemetry.js, its test, and `GrafanaEmbed`.
