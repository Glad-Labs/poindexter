"""Unit tests for scripts/recovery-agent.py (the host Recovery Agent).

The agent runs on the host and turns authenticated ``POST /recover`` requests
from the brain (a Docker container) into host-level recovery actions:
Scheduled-Task restarts and a compose reapply. These tests cover the pure
dispatch + auth seams and the compose-reapply command construction without a
real socket, Scheduled Task, or docker daemon — so they run on Linux CI too.

The script filename is hyphenated (not an importable module name), so we load
it by path via importlib, the same way it is deployed and run.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_agent():
    here = Path(__file__).resolve()
    root = next(
        (p for p in here.parents if (p / "scripts" / "recovery-agent.py").is_file()),
        None,
    )
    assert root is not None, "could not locate scripts/recovery-agent.py from test"
    path = root / "scripts" / "recovery-agent.py"
    spec = importlib.util.spec_from_file_location("recovery_agent_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


# --- dispatch_recovery -----------------------------------------------------


def test_unknown_service_is_400():
    status, body = agent.dispatch_recovery("nope")
    assert status == 400
    assert body["ok"] is False
    assert "unknown service" in body["error"]


def test_task_kind_invokes_task_fn_with_registered_name():
    seen = {}

    def fake_task(name):
        seen["name"] = name
        return True, "ok"

    status, body = agent.dispatch_recovery(
        "mcp-http", task_fn=fake_task, compose_fn=lambda: (True, "x"),
    )
    assert status == 200 and body["ok"] is True
    # The registered Scheduled-Task name is what gets restarted — proves the
    # existing mcp-http behavior survives the action-kinds generalization.
    assert seen["name"] == "Poindexter MCP HTTP"


def test_compose_kind_invokes_compose_fn():
    called = {"n": 0}

    def fake_compose():
        called["n"] += 1
        return True, "reapplied"

    status, body = agent.dispatch_recovery(
        "compose-reapply", task_fn=lambda n: (True, "x"), compose_fn=fake_compose,
    )
    assert status == 200 and body["ok"] is True and body["detail"] == "reapplied"
    assert called["n"] == 1


def test_action_failure_is_500():
    status, body = agent.dispatch_recovery(
        "compose-reapply", compose_fn=lambda: (False, "boom"),
    )
    assert status == 500 and body["ok"] is False and body["detail"] == "boom"


# --- authorized ------------------------------------------------------------


@pytest.mark.parametrize(
    "header,token,expected",
    [
        ("Bearer s3cret", "s3cret", True),
        ("Bearer wrong", "s3cret", False),
        ("", "s3cret", False),
        ("Basic abc", "s3cret", False),
        ("Bearer s3cret", "", False),  # agent not configured (no token)
    ],
)
def test_authorized(header, token, expected):
    ok, _ = agent.authorized(header, token)
    assert ok is expected


# --- _resolve_start_stack --------------------------------------------------


def test_resolve_start_stack_prefers_env(monkeypatch, tmp_path):
    ss = tmp_path / "start-stack.sh"
    ss.write_text("#!/bin/bash\n")
    monkeypatch.setenv("POINDEXTER_START_STACK", str(ss))
    assert agent._resolve_start_stack() == str(ss)


def test_resolve_start_stack_finds_deploy_clone(monkeypatch, tmp_path):
    monkeypatch.delenv("POINDEXTER_START_STACK", raising=False)
    clone_ss = tmp_path / ".poindexter" / "deploy" / "some-clone" / "scripts" / "start-stack.sh"
    clone_ss.parent.mkdir(parents=True)
    clone_ss.write_text("#!/bin/bash\n")
    monkeypatch.setattr(agent.Path, "home", staticmethod(lambda: tmp_path))
    # Globbed without spelling out the clone's directory name (public-safe).
    assert agent._resolve_start_stack() == str(clone_ss)


def test_resolve_start_stack_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("POINDEXTER_START_STACK", raising=False)
    monkeypatch.setattr(agent.glob, "glob", lambda pattern: [])
    monkeypatch.setattr(agent.Path, "home", staticmethod(lambda: tmp_path))
    assert agent._resolve_start_stack() is None


# --- _resolve_git_bash -----------------------------------------------------


def test_resolve_git_bash_derives_from_git_location(monkeypatch, tmp_path):
    gitroot = tmp_path / "Git"
    (gitroot / "cmd").mkdir(parents=True)
    (gitroot / "bin").mkdir(parents=True)
    git_exe = gitroot / "cmd" / "git.exe"
    git_exe.write_text("")
    (gitroot / "bin" / "bash.exe").write_text("")
    monkeypatch.setattr(agent.shutil, "which", lambda name: str(git_exe))
    # Compare structurally to dodge .resolve() canonicalization differences.
    result = Path(agent._resolve_git_bash())
    assert result.name == "bash.exe" and result.parent.name == "bin"


# --- _compose_reapply ------------------------------------------------------


def test_compose_reapply_spawns_start_stack_up_no_build(monkeypatch):
    calls = {}

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(agent, "_resolve_start_stack", lambda: "/clone/scripts/start-stack.sh")
    monkeypatch.setattr(agent, "_resolve_git_bash", lambda: "/git/bin/bash.exe")
    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)
    ok, detail = agent._compose_reapply()
    assert ok is True and "dispatched" in detail
    # Fire-and-forget Popen; plain `up -d` (selective recreate of drifted
    # services), --no-build (no surprise image builds), NOT --force-recreate
    # (would tear down healthy containers incl. postgres/brain).
    assert calls["argv"] == [
        "/git/bin/bash.exe", "/clone/scripts/start-stack.sh", "up", "-d", "--no-build",
    ]


def test_compose_reapply_errors_when_start_stack_missing(monkeypatch):
    monkeypatch.setattr(agent, "_resolve_start_stack", lambda: None)
    ok, detail = agent._compose_reapply()
    assert ok is False and "start-stack.sh not found" in detail


def test_compose_reapply_spawn_failure_is_reported(monkeypatch):
    def boom(argv, **kwargs):
        raise OSError("cannot spawn")

    monkeypatch.setattr(agent, "_resolve_start_stack", lambda: "/clone/scripts/start-stack.sh")
    monkeypatch.setattr(agent, "_resolve_git_bash", lambda: "/git/bin/bash.exe")
    monkeypatch.setattr(agent.subprocess, "Popen", boom)
    ok, detail = agent._compose_reapply()
    assert ok is False and "OSError" in detail


# --- query_task_statuses (status capability — GET /tasks) ------------------
# The brain runs in a Linux container and can't enumerate the host's Windows
# Task Scheduler, so the agent (on the host) reflects task status back over
# GET /tasks. query_task_statuses is the pure dispatch behind that route, with
# the PowerShell runner injected so these assertions run on Linux CI too.


def test_query_task_statuses_empty_names_returns_empty_list():
    # No names requested → nothing to report (the route resolves names from the
    # query string; a bare GET /tasks should not error).
    status, body = agent.query_task_statuses([])
    assert status == 200
    assert body == {"ok": True, "tasks": []}


def test_query_task_statuses_returns_status_fn_payload():
    def fake_status(names):
        assert names == ["Poindexter MCP HTTP"]
        return True, [
            {
                "name": "Poindexter MCP HTTP",
                "exists": True,
                "enabled": True,
                "state": "Ready",
                "last_run_result": 0,
            }
        ]

    status, body = agent.query_task_statuses(
        ["Poindexter MCP HTTP"], status_fn=fake_status
    )
    assert status == 200 and body["ok"] is True
    assert body["tasks"][0]["name"] == "Poindexter MCP HTTP"
    assert body["tasks"][0]["enabled"] is True


def test_query_task_statuses_status_fn_failure_is_500():
    status, body = agent.query_task_statuses(
        ["X"], status_fn=lambda names: (False, "powershell not found")
    )
    assert status == 500
    assert body["ok"] is False
    assert "powershell not found" in body["error"]


# --- _task_names_from_query ------------------------------------------------


def test_task_names_from_query_repeated_name_params():
    # The brain sends one ?name= per watched task (spaces form-encoded).
    names = agent._task_names_from_query("name=Poindexter+MCP+HTTP&name=DeployCheckoutSync")
    assert names == ["Poindexter MCP HTTP", "DeployCheckoutSync"]


def test_task_names_from_query_csv_form_dedupes_preserving_order():
    # ?names=A,B,A convenience form for manual curl debugging.
    assert agent._task_names_from_query("names=A,B,A") == ["A", "B"]


def test_task_names_from_query_empty_is_empty_list():
    assert agent._task_names_from_query("") == []


# --- _scheduled_task_status (PowerShell runner) ----------------------------


def test_scheduled_task_status_parses_json_array(monkeypatch):
    canned = (
        '[{"name":"A","exists":true,"enabled":true,'
        '"state":"Ready","last_run_result":0}]'
    )

    def fake_run(argv, **kwargs):
        # Proves we shell out to PowerShell, embedding the requested task name
        # and the Get-ScheduledTask / Get-ScheduledTaskInfo cmdlets.
        assert argv[0] == "powershell"
        assert "Get-ScheduledTask" in argv[-1]
        assert "Get-ScheduledTaskInfo" in argv[-1]
        assert "'A'" in argv[-1]
        return SimpleNamespace(returncode=0, stdout=canned, stderr="")

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    ok, tasks = agent._scheduled_task_status(["A"])
    assert ok is True
    assert tasks == [
        {
            "name": "A",
            "exists": True,
            "enabled": True,
            "state": "Ready",
            "last_run_result": 0,
        }
    ]


def test_scheduled_task_status_wraps_single_object(monkeypatch):
    # Windows PowerShell 5.1's ConvertTo-Json emits a bare object (not an
    # array) for a single task — the parser must normalize it to a list.
    canned = (
        '{"name":"A","exists":true,"enabled":false,'
        '"state":"Disabled","last_run_result":0}'
    )
    monkeypatch.setattr(
        agent.subprocess,
        "run",
        lambda argv, **k: SimpleNamespace(returncode=0, stdout=canned, stderr=""),
    )
    ok, tasks = agent._scheduled_task_status(["A"])
    assert ok is True and isinstance(tasks, list) and len(tasks) == 1
    assert tasks[0]["enabled"] is False


def test_scheduled_task_status_empty_stdout_is_empty_list(monkeypatch):
    monkeypatch.setattr(
        agent.subprocess,
        "run",
        lambda argv, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    ok, tasks = agent._scheduled_task_status(["A"])
    assert ok is True and tasks == []


def test_scheduled_task_status_nonzero_exit_is_error(monkeypatch):
    monkeypatch.setattr(
        agent.subprocess,
        "run",
        lambda argv, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    ok, detail = agent._scheduled_task_status(["A"])
    assert ok is False and "boom" in detail


def test_scheduled_task_status_escapes_single_quotes(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["script"] = argv[-1]
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    agent._scheduled_task_status(["O'Brien Task"])
    # Single quotes are doubled for PowerShell string-literal safety (mirrors
    # _restart_task), so the name can't break out of the '...' literal.
    assert "O''Brien Task" in seen["script"]


def test_scheduled_task_status_subprocess_error_is_reported(monkeypatch):
    def boom(argv, **kwargs):
        raise FileNotFoundError("powershell missing")

    monkeypatch.setattr(agent.subprocess, "run", boom)
    ok, detail = agent._scheduled_task_status(["A"])
    assert ok is False and "FileNotFoundError" in detail


# --- _restart_process + "process" kind + "ollama" service ------------------


def test_ollama_registered_as_process_kind():
    """SERVICES must declare "ollama" with kind="process" so dispatch_recovery
    routes it to _restart_process instead of the scheduled-task or compose paths."""
    spec = agent.SERVICES.get("ollama")
    assert spec is not None, '"ollama" not found in SERVICES'
    assert spec["kind"] == "process"
    assert "command" in spec, '"ollama" SERVICES entry missing "command" key'


def test_ollama_service_runs_powershell_kill_and_start(monkeypatch):
    """_restart_process must be invoked with a command that stops ollama
    and starts 'ollama serve'. We inject _restart_process to stay portable."""
    seen: dict = {}

    def fake_restart_process(command: str) -> tuple[bool, str]:
        seen["command"] = command
        return True, "process restart command succeeded"

    monkeypatch.setattr(agent, "_restart_process", fake_restart_process)
    status, body = agent.dispatch_recovery("ollama")
    assert status == 200 and body["ok"] is True
    cmd = seen.get("command", "")
    assert "ollama" in cmd.lower()
    assert "serve" in cmd.lower()
    assert "Stop-Process" in cmd or "stop" in cmd.lower()


def test_restart_process_returns_true_on_zero_exit(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        agent.subprocess,
        "run",
        lambda argv, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    ok, detail = agent._restart_process("echo hi")
    assert ok is True and "succeeded" in detail


def test_restart_process_returns_false_on_nonzero_exit(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        agent.subprocess,
        "run",
        lambda argv, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    ok, detail = agent._restart_process("echo hi")
    assert ok is False and "boom" in detail


def test_restart_process_reports_subprocess_exception(monkeypatch):
    def boom(argv, **k):
        raise OSError("powershell not found")

    monkeypatch.setattr(agent.subprocess, "run", boom)
    ok, detail = agent._restart_process("cmd")
    assert ok is False and "OSError" in detail
