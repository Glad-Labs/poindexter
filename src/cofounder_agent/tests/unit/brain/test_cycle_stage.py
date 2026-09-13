"""The watchdog's timeout must name the stage that hung (GlitchTip #925).

``run_cycle`` stamps ``cycle_stage`` before every stage await and
``run_health_probes`` before every probe, so a cancelled cycle can say
"in stage health_probe:ollama" instead of "a stuck await was cancelled".
"""
from __future__ import annotations

import ast
import asyncio
import inspect

import pytest

from poindexter.brain import brain_daemon, cycle_stage, health_probes


@pytest.fixture(autouse=True)
def _reset():
    cycle_stage.reset()
    yield
    cycle_stage.reset()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_leaves_the_hung_stage_readable(monkeypatch):
    monkeypatch.setattr(brain_daemon, "_brain_activity_begin", _noop_async_returning(1))
    monkeypatch.setattr(brain_daemon, "_brain_activity_finish", _noop_async_returning(None))

    async def hangs_in_compose_drift(_pool):
        cycle_stage.set_stage("run_compose_drift_probe")
        await asyncio.sleep(10)

    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await brain_daemon._run_cycle_with_watchdog(None, cycle_timeout=0.05, run_cycle_fn=hangs_in_compose_drift)
    assert cycle_stage.get_stage() == "run_compose_drift_probe"


@pytest.mark.unit
def test_every_stage_await_in_run_cycle_is_stamped():
    src = inspect.getsource(brain_daemon.run_cycle)
    tree = ast.parse(src)
    stamped_before: set[str] = set()
    awaited: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_cycle":
            body = node.body
            break
    else:  # pragma: no cover
        raise AssertionError("run_cycle not found")

    def visit(stmts):
        last_stamp = None
        for st in stmts:
            dumped = ast.dump(st)
            if "attr='set_stage'" in dumped and isinstance(st, ast.Expr):
                last_stamp = st.value.args[0].value  # type: ignore[attr-defined]
                continue
            for call in [n for n in ast.walk(st) if isinstance(n, ast.Await)]:
                fn = call.value.func if isinstance(call.value, ast.Call) else None
                name = getattr(fn, "id", None)
                if name in STAGES:
                    awaited.add(name)
                    if last_stamp == name.lstrip("_"):
                        stamped_before.add(name)
            for child in ast.iter_child_nodes(st):
                if hasattr(child, "body") and isinstance(child.body, list):
                    visit(child.body)
                if hasattr(child, "orelse") and isinstance(child.orelse, list):
                    visit(child.orelse)
                if hasattr(child, "handlers"):
                    for hd in child.handlers:
                        visit(hd.body)
            if hasattr(st, "body") and isinstance(st.body, list):
                visit(st.body)
            if hasattr(st, "handlers"):
                for hd in st.handlers:
                    visit(hd.body)
            last_stamp = None if not isinstance(st, ast.Expr) else last_stamp

    visit(body)
    assert awaited >= {"monitor_services", "run_health_probes", "run_compose_drift_probe"}
    missing = awaited - stamped_before
    assert not missing, f"stage awaits without a set_stage() right before them: {sorted(missing)}"


STAGES = {
    "monitor_services", "monitor_external_services", "auto_remediate", "self_maintain",
    "update_system_metrics", "log_electricity_cost", "generate_daily_digest", "_maybe_sync_grafana_alerts",
    "run_health_probes", "run_business_probes", "probe_post_performance", "maybe_run_operator_url_probe",
    "run_migration_drift_probe", "run_compose_drift_probe",
}


@pytest.mark.unit
def test_health_probe_loop_stamps_each_probe():
    src = inspect.getsource(health_probes.run_health_probes)
    assert 'cycle_stage.set_stage(f"health_probe:{name}")' in src


def _noop_async_returning(value):
    async def _f(*_a, **_k):
        return value
    return _f
