"""deploy_health_gate — watch a rebuilt service come up, roll back when it does not."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
GATE = REPO_ROOT / "scripts" / "linux" / "deploy_health_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("deploy_health_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _inspect_json(*, status="running", restarting=False, restarts=0, health="healthy", has_hc=True, image_id="sha256:new"):
    return json.dumps([{
        "State": {"Status": status, "Restarting": restarting, "ExitCode": 1 if restarting else 0,
                  "StartedAt": "t", **({"Health": {"Status": health}} if has_hc else {})},
        "RestartCount": restarts,
        "Config": {"Image": "glad-labs-website-x", **({"Healthcheck": {"Test": ["CMD", "true"]}} if has_hc else {})},
        "Image": image_id,
    }])


class FakeDocker:
    """argv -> (rc, out, err); `inspect` answers come from a queue so a service can change over polls."""

    def __init__(self, inspects: list[str]):
        self.inspects = list(inspects)
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return 0, "poindexter-x\n", ""
        if argv[:2] == ["docker", "inspect"]:
            body = self.inspects.pop(0) if len(self.inspects) > 1 else self.inspects[0]
            return 0, body, ""
        if argv[:2] == ["docker", "logs"]:
            return 0, "Traceback\nModuleNotFoundError: No module named '_voice_paths'\n", ""
        if argv[:2] == ["docker", "tag"]:
            return 0, "", ""
        if argv[:2] == ["docker", "exec"]:
            return 0, "", ""
        if "up" in argv:
            return 0, "", ""
        return 1, "", f"unexpected {argv}"

    def alerts(self):
        return [a for a in self.calls if a[:2] == ["docker", "exec"] and any("alert_events" in x for x in a)]

    def tags(self):
        return [a for a in self.calls if a[:2] == ["docker", "tag"]]

    def ups(self):
        return [a for a in self.calls if "up" in a]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _verify(mod, fake, services, *, rollback=True, timeout=300, settle=30, snap=None):
    clock = Clock()
    default = {"x": {"container": "poindexter-x", "image_ref": "glad-labs-website-x", "image_id": "sha256:old"}}
    return mod.verify(services, default if snap is None else snap,
                      sha="abc123def", timeout=timeout, settle=settle, do_rollback=rollback,
                      stack_cmd=["bash", "start-stack.sh"], run=fake, clock=clock, sleep=clock.sleep), fake


def test_healthy_after_a_few_polls_is_ok_and_writes_nothing():
    mod = _load()
    fake = FakeDocker([_inspect_json(health="starting"), _inspect_json(health="starting"), _inspect_json(health="healthy")])
    res, fake = _verify(mod, fake, ["x"])
    assert res["x"]["verdict"] == "healthy" and mod.exit_code(res) == 0
    assert fake.alerts() == [] and fake.tags() == []


def test_no_healthcheck_service_needs_the_settle_window():
    mod = _load()
    fake = FakeDocker([_inspect_json(has_hc=False, health=None)])
    clock = Clock()
    v, _, _ = mod.wait_for("x", timeout=300, settle=30, run=fake, clock=clock, sleep=clock.sleep)
    assert v == "healthy" and clock.t >= 30


def test_restart_loop_rolls_back_to_the_previous_image_and_pages_critical():
    mod = _load()
    fake = FakeDocker([_inspect_json(status="restarting", restarting=True, restarts=3, health="unhealthy"),
                       _inspect_json(health="healthy", image_id="sha256:old")])
    res, fake = _verify(mod, fake, ["x"])
    assert res["x"]["verdict"].startswith("failed:restarting") and res["x"]["rolled_back"] is True
    assert res["x"]["after_rollback"] == "healthy" and mod.exit_code(res) == 2
    assert fake.tags() == [["docker", "tag", "sha256:old", "glad-labs-website-x"]]
    assert fake.ups() and fake.ups()[0][-1] == "x" and "--force-recreate" in fake.ups()[0]
    alerts = fake.alerts()
    assert len(alerts) == 1
    cmd = " ".join(alerts[0])
    assert "sev=critical" in cmd and "rolled back" in cmd and "ModuleNotFoundError" in cmd and "fp=deploy_health_gate:x:abc123def" in cmd
    assert ":'sev'" in alerts[0][-1] and "critical" not in alerts[0][-1]  # values never spliced into the SQL text


def test_rollback_disabled_pages_but_leaves_the_image():
    mod = _load()
    fake = FakeDocker([_inspect_json(health="unhealthy")])
    res, fake = _verify(mod, fake, ["x"], rollback=False)
    assert res["x"]["verdict"] == "failed:unhealthy" and res["x"]["rolled_back"] is False
    assert mod.exit_code(res) == 1 and fake.tags() == [] and len(fake.alerts()) == 1
    assert "sev=critical" in " ".join(fake.alerts()[0])


def test_bounced_containers_are_verified_by_name_and_never_rolled_back():
    mod = _load()
    fake = FakeDocker([_inspect_json(status="restarting", restarting=True, restarts=5)])
    res, fake = _verify(mod, fake, ["container:poindexter-worker"], snap={})
    assert res["container:poindexter-worker"]["container"] == "poindexter-worker"
    assert res["container:poindexter-worker"]["rolled_back"] is False and fake.tags() == []
    assert len(fake.alerts()) == 1 and "sev=critical" in " ".join(fake.alerts()[0])


def test_timeout_without_a_verdict_is_a_warning_not_a_rollback():
    mod = _load()
    fake = FakeDocker([_inspect_json(health="starting")])
    res, fake = _verify(mod, fake, ["x"], timeout=60)
    assert res["x"]["verdict"] == "timeout" and res["x"]["rolled_back"] is False and mod.exit_code(res) == 1
    assert len(fake.alerts()) == 1 and "sev=warning" in " ".join(fake.alerts()[0])


def test_missing_snapshot_means_no_rollback_but_still_a_page():
    mod = _load()
    fake = FakeDocker([_inspect_json(status="exited")])
    res, fake = _verify(mod, fake, ["x"], snap={})
    assert res["x"]["rolled_back"] is False and "no previous image" in res["x"]["rollback_note"]
    assert fake.tags() == [] and "rollback FAILED" in " ".join(fake.alerts()[0])


def test_verdict_table():
    mod = _load()
    def info(**kw):
        return mod.inspect("c", lambda argv: (0, _inspect_json(**kw), ""))
    assert mod.verdict(None, running_for=0, settle=30) == "pending"
    assert mod.verdict(info(health="healthy"), running_for=0, settle=30) == "healthy"
    assert mod.verdict(info(health="starting"), running_for=100, settle=30) == "pending"
    assert mod.verdict(info(restarts=2), running_for=0, settle=30).startswith("failed:restarted 2x")
    assert mod.verdict(info(status="exited"), running_for=0, settle=30).startswith("failed:exited")
    assert mod.verdict(info(has_hc=False, health=None), running_for=10, settle=30) == "pending"
    assert mod.verdict(info(has_hc=False, health=None), running_for=31, settle=30) == "healthy"


def test_settings_fall_back_when_psql_is_unavailable():
    mod = _load()
    assert mod.read_int_setting("deploy_health_gate_seconds", 300, run=lambda argv: (1, "", "down")) == 300
    assert mod.read_bool_setting("deploy_rollback_on_unhealthy", True, run=lambda argv: (0, "false\n", "")) is False
    assert mod.read_int_setting("k", 7, run=lambda argv: (0, "not-an-int\n", "")) == 7


def test_snapshot_records_the_running_image_id():
    mod = _load()
    fake = FakeDocker([_inspect_json(image_id="sha256:running")])
    snap = mod.snapshot(["x"], run=fake)
    assert snap == {"x": {"container": "poindexter-x", "image_ref": "glad-labs-website-x", "image_id": "sha256:running"}}


@pytest.mark.parametrize("results,code", [({"a": {"verdict": "healthy", "rolled_back": False}}, 0),
                                          ({"a": {"verdict": "failed:x", "rolled_back": True}}, 2),
                                          ({"a": {"verdict": "timeout", "rolled_back": False}}, 1)])
def test_exit_codes(results, code):
    assert _load().exit_code(results) == code


def test_settings_and_alerts_use_psql_variables_not_spliced_sql():
    mod = _load()
    seen = []

    def run(argv):
        seen.append(argv)
        return 0, "42\n", ""

    assert mod.read_setting("deploy_health_gate_seconds", "300", run=run) == "42"
    argv = seen[0]
    assert argv[-1] == "SELECT value FROM app_settings WHERE key = :'k'"
    assert "-v" in argv and "k=deploy_health_gate_seconds" in argv
    mod.write_alert(service="x", sha="s", severity="critical", title="t'; DROP TABLE posts; --", body="$$ body $$", run=run)
    sql = seen[1][-1]
    assert "DROP TABLE" not in sql and "$$" not in sql
    assert any(a.startswith("ann=") and "DROP TABLE" in a for a in seen[1])
