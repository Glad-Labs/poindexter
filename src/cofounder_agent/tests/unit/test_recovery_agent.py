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
