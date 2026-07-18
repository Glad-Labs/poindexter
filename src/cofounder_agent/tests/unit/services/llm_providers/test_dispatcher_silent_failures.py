"""Unit tests — dispatcher cost_logs auto-write visibility.

Silent-excepts OLD-debt burn-down (batch 16). ``_record_dispatch_cost`` writes a
``cost_logs`` row after every ``dispatch_complete`` call. For all non-writer LLM
dispatches (QA / SEO / title / atom calls) it is the SOLE ``cost_logs`` writer —
``admin_db.log_cost`` is wired to one caller (``writer_core``), and
``cost_guard.record`` only fires for caption/media/youtube ``record_usage``. The
budget cap SUMS ``cost_logs.cost_usd``, so a swallowed write on a PAID call
silently undercounts the cap with no operator signal — the router-learning /
analytics gap class (batches 10-13).

The write-failure handler now emits a non-paging ``info`` finding (kind
``dispatch_cost_log_write_failed``) and still never raises (the auto-log must
never break the dispatch path). Mirrors the sibling ``cost_guard.record``, which
already surfaces its own ``cost_log_write_failed`` audit event.

The other two flagged handlers in the same function stay ``# silent-ok``: the
``get_site_config`` fetch (falls back to ``site_config=None``) and the
``estimate_local_kwh`` guard (electricity_kwh stays ``None``; the authoritative
electricity spend is the brain's measured power rows, not per-call estimates).
"""

from __future__ import annotations

import types

import pytest

import services.llm_providers.dispatcher as dispatcher

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


class _BrokenConn:
    async def execute(self, *_a, **_k):
        raise RuntimeError("cost_logs insert boom")


class _BrokenCM:
    async def __aenter__(self):
        return _BrokenConn()

    async def __aexit__(self, *_a):
        return False


class _BrokenPool:
    def acquire(self):
        return _BrokenCM()


class _OkConn:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    async def execute(self, query, *args):
        self._sink.append((query, args))


class _OkCM:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    async def __aenter__(self):
        return _OkConn(self._sink)

    async def __aexit__(self, *_a):
        return False


class _OkPool:
    def __init__(self) -> None:
        self.executions: list = []

    def acquire(self):
        return _OkCM(self.executions)


# A PAID model + config so cost_usd = response_cost (non-zero) and the electricity
# branch is skipped, landing straight on the cost_logs INSERT.
_PAID_MODEL = "openai/gpt-4o-mini"
_RESULT = types.SimpleNamespace(
    raw={"response_cost": 0.5},
    prompt_tokens=10,
    completion_tokens=5,
    total_tokens=15,
)
_PROVIDER = types.SimpleNamespace(name="openai")


async def test_cost_log_write_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)

    # Must not raise — the auto-log is observability-only.
    await dispatcher._record_dispatch_cost(
        pool=_BrokenPool(),
        provider=_PROVIDER,
        model=_PAID_MODEL,
        result=_RESULT,
        task_id="t1",
        phase="atom.qa",
        duration_ms=100,
        success=True,
        provider_config={},
        error=None,
    )

    findings = [c for c in calls if c["kind"] == "dispatch_cost_log_write_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "dispatch_cost_log_write_failed" in findings[0]["dedup_key"]


async def test_cost_log_write_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _OkPool()

    await dispatcher._record_dispatch_cost(
        pool=pool,
        provider=_PROVIDER,
        model=_PAID_MODEL,
        result=_RESULT,
        task_id="t1",
        phase="atom.qa",
        duration_ms=100,
        success=True,
        provider_config={},
        error=None,
    )

    # The row was written and no failure finding fired.
    assert any("cost_logs" in q for (q, _a) in pool.executions)
    assert calls == []
