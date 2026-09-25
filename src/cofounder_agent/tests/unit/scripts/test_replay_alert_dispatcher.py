"""scripts/replay_alert_dispatcher.py — the harness that measured #4022 and #4025.

It drives the real dispatcher over ``FirefighterWorld``, which answers the
dispatcher's SQL by substring. When that SQL changes and the fake does not,
the replay quietly measures a different system. These tests replay a short
history with a known outcome, so that drift fails here instead.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "scripts").is_dir() and (p / "src" / "cofounder_agent").is_dir()
)
_SCRIPT = _REPO_ROOT / "scripts" / "replay_alert_dispatcher.py"


def _load():
    spec = importlib.util.spec_from_file_location("replay_alert_dispatcher", _SCRIPT)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _event(event_id, minutes, *, alertname, fingerprint, status="firing", prod=None,
           starts_at=None):
    return {
        "id": event_id, "alertname": alertname, "status": status, "severity": "warning",
        "category": "infrastructure", "labels": {"alertname": alertname, "severity": "warning"},
        "annotations": {"summary": f"{alertname} {status}"}, "fingerprint": fingerprint,
        "starts_at": starts_at,
        "received_at": (_T0 + timedelta(minutes=minutes)).isoformat(), "dispatch_result": prod,
    }


def _history():
    """A job finding that fails every 20 minutes with no rule, and an
    Alertmanager alert whose rule restarts the profiler, which Alertmanager
    then reports resolved (the evidence a notifier's fix needs)."""
    taps = {"alertname": "scheduler.run_taps:job_failure", "fingerprint": "job_failure:run_taps"}
    pyro = {"alertname": "PyroscopeDown", "fingerprint": "a9b4c69fd247b1e8",
            "starts_at": (_T0 - timedelta(minutes=4)).isoformat()}
    return [
        _event(1, 0, **taps, prod="sent"),
        _event(2, 1, **pyro, prod="remediating: restart_container (run 1)"),
        _event(3, 6, **pyro, status="resolved", prod="suppressed: repeat 2"),
        _event(4, 20, **taps, prod="suppressed: repeat 2"),
        _event(5, 40, **taps, prod="sent: summary (repeat 3)"),
        _event(6, 60, **taps, prod="suppressed: repeat 4"),
    ]


_PYRO_RULE = {
    "id": 1, "alertname": "PyroscopeDown", "match_regex": None,
    "action_name": "restart_container", "params": {"container": "poindexter-pyroscope"},
    "enabled": True, "max_attempts_per_window": None, "window_minutes": None,
    "verify_after_seconds": None, "created_at": "2026-07-04T20:43:52+00:00",
}


@pytest.mark.asyncio
async def test_a_replay_reproduces_what_the_dispatcher_does():
    mod = _load()
    report = await mod.replay(_history(), [_PYRO_RULE], select="abstain")
    summary = mod.summarize(report)
    assert summary["pages"] == 2                      # the taps first fire + its 30-min summary
    assert summary["actions_by_source"] == {"rule": 1}
    assert summary["verifies"] == {"rule:resolved": 1}
    assert summary["matches_prod"] == "6/6"
    (verify,) = report["remediation_verify"]
    assert verify["evidence"] == "resolved_notification"
    # the long-tail's one look: the finding's second row, not its first or later ones
    assert [(s["row"], s["alertname"]) for s in report["selects"]] == [(4, "scheduler.run_taps:job_failure")]
    replayed = {r["id"]: r["replay"] for r in report["rows"]}
    assert replayed[4].endswith("persistent at repeat 2 (20 min), not auto-remediated (no rule; llm abstained)")


@pytest.mark.asyncio
async def test_the_worst_case_selector_is_recorded_as_a_dry_run_by_default():
    mod = _load()
    report = await mod.replay(_history(), [_PYRO_RULE], select="act")
    assert [d["params"] for d in report["remediation_dry_run"]] == [{"container": "poindexter-replay-target"}]
    assert mod.summarize(report)["actions_by_source"] == {"rule": 1}


@pytest.mark.asyncio
async def test_a_replay_leaves_the_dispatcher_as_it_found_it():
    import poindexter.brain.alert_dispatcher as ad
    import poindexter.brain.remediation.engine as engine

    before = (ad._default_now, ad._make_select_fn, ad.evaluate_for_dispatch_hook,
              engine.datetime, engine.execute)
    await _load().replay(_history(), [_PYRO_RULE])
    assert (ad._default_now, ad._make_select_fn, ad.evaluate_for_dispatch_hook,
            engine.datetime, engine.execute) == before


@pytest.mark.asyncio
async def test_compare_reports_the_decisions_a_change_moves(tmp_path, capsys):
    mod = _load()
    before = await mod.replay(_history(), [_PYRO_RULE])
    after = await mod.replay(_history(), [_PYRO_RULE], select="act",
                             settings={"ops_firefighter_llm_dry_run": "false"})
    paths = []
    for name, report in (("before", before), ("after", after)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(report, default=str))
        paths.append(str(path))
    assert mod.main(["--compare", *paths]) == 0
    change = json.loads(capsys.readouterr().out)["change"]
    assert change["decisions_moved"] == {"suppressed -> remediating": 1}
