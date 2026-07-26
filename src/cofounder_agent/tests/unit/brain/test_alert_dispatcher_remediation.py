import json
from unittest.mock import AsyncMock

import brain.alert_dispatcher as ad
import pytest
from brain.remediation.engine import RemediationDecision
from brain.remediation.registry import ActionResult

_CATALOG = [{"name": "restart_container", "description": "restart it", "params_schema": {"container": "str"}}]


def _make_row(row_id=1, alertname="WorkerDown", severity="critical"):
    return {
        "id": row_id, "alertname": alertname, "status": "firing",
        "severity": severity, "category": "infrastructure",
        "labels": {"alertname": alertname, "severity": severity},
        "annotations": {}, "fingerprint": "fp-worker",
    }


class _Pool:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    async def fetch(self, sql, *a):
        if "alert_events" in sql and "dispatched_at IS NULL" in sql:
            return self._rows
        return []

    async def execute(self, sql, *a):
        self.executed.append((sql, a))

    async def fetchval(self, sql, *a):
        return None

    async def fetchrow(self, sql, *a):
        return None


def _acoro(value):
    async def _f(*a, **k):
        return value
    return _f


@pytest.mark.asyncio
async def test_firefighter_acts_holds_the_page(monkeypatch):
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro({
        "suppress_window_minutes": 0, "summarize_threshold_minutes": 0,
        "force_telegram_set": frozenset(), "triage_retry_max": 1, "triage_backoff": [0.0],
        "firefighter_config": {"enabled": True},
    }))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    monkeypatch.setattr(ad, "run_verify_scan_hook",
                        _acoro({"verified": 0, "resolved": 0, "still_firing": 0}), raising=False)
    monkeypatch.setattr(
        ad, "evaluate_for_dispatch_hook",
        _acoro(RemediationDecision(acted=True, action_name="restart_container",
                                   run_id="abcd1234ef", result=ActionResult(status="ok"))),
        raising=False,
    )
    summary = await ad.poll_and_dispatch(pool, notify_fn=notify)
    assert summary.get("remediated") == 1
    assert notify.await_count == 0  # page HELD
    assert any("remediating:" in str(a[1]) for a in pool.executed)


@pytest.mark.asyncio
async def test_no_rule_pages_as_usual(monkeypatch):
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro({
        "suppress_window_minutes": 0, "summarize_threshold_minutes": 0,
        "force_telegram_set": frozenset(), "triage_retry_max": 1, "triage_backoff": [0.0],
        "firefighter_config": {"enabled": True},
    }))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    monkeypatch.setattr(
        ad, "evaluate_for_dispatch_hook",
        _acoro(RemediationDecision(acted=False, reason="no rule")), raising=False,
    )
    summary = await ad.poll_and_dispatch(pool, notify_fn=notify)
    assert summary["sent"] == 1
    assert notify.await_count == 1  # paged


# ---------------------------------------------------------------------------
# Plan B — the brain-side select_fn transport (POST /api/remediation/select).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remediation_select_returns_parsed_selection(monkeypatch):
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro("tok"))
    body = json.dumps({
        "action_name": "restart_container", "params": {"container": "poindexter-pyroscope"},
        "confidence": 0.82, "reason": "profiler down", "model": "ollama/llama3.2:3b",
    }).encode("utf-8")
    monkeypatch.setattr(ad, "_post_select_sync", lambda url, payload, token, timeout: (200, body))
    out = await ad._remediation_select(_Pool([]), alert={"labels": {}}, catalog=_CATALOG, timeout=5.0)
    assert out is not None
    assert out["action_name"] == "restart_container"
    assert out["params"] == {"container": "poindexter-pyroscope"}
    assert out["confidence"] == 0.82
    assert out["model"] == "ollama/llama3.2:3b"


@pytest.mark.asyncio
async def test_remediation_select_abstain_returns_none(monkeypatch):
    """action_name='' (the worker's abstain) -> None, so the engine pages."""
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro("tok"))
    body = json.dumps({"action_name": "", "confidence": 0.0}).encode("utf-8")
    monkeypatch.setattr(ad, "_post_select_sync", lambda *a: (200, body))
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_remediation_select_no_base_url_returns_none(monkeypatch):
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro(""))
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_remediation_select_no_token_returns_none(monkeypatch):
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro(None))
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_remediation_select_503_returns_none(monkeypatch):
    """Worker 503 (firefighter disabled / no provider) -> None (Ollama-down
    degradation: page as usual)."""
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro("tok"))
    monkeypatch.setattr(ad, "_post_select_sync", lambda *a: (503, b'{"detail":{"code":"no_provider"}}'))
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_remediation_select_post_raises_returns_none(monkeypatch):
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro("tok"))

    def _boom(*a):
        raise OSError("connection refused")

    monkeypatch.setattr(ad, "_post_select_sync", _boom)
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_poll_wires_select_fn_and_repeat_count(monkeypatch):
    """poll_and_dispatch hands the engine a callable select_fn + an int
    repeat_count so the LLM long-tail path can engage on persistent alerts."""
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro({
        "suppress_window_minutes": 0, "summarize_threshold_minutes": 0,
        "force_telegram_set": frozenset(), "triage_retry_max": 1, "triage_backoff": [0.0],
        "firefighter_config": {"enabled": True, "llm_longtail_enabled": True},
    }))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    captured = {}

    async def _hook(pool, *, alert, fingerprint, config, logger,
                    select_fn=None, repeat_count=0, age_minutes=0):
        captured["select_fn"] = select_fn
        captured["repeat_count"] = repeat_count
        return RemediationDecision(acted=False, reason="no rule")

    monkeypatch.setattr(ad, "evaluate_for_dispatch_hook", _hook, raising=False)
    await ad.poll_and_dispatch(pool, notify_fn=notify)
    assert callable(captured["select_fn"])
    assert isinstance(captured["repeat_count"], int)
