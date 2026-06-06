"""Unit tests for the host-side voice brain daemon (scripts/voice_brain_host.py, #1006).

This daemon is a voice->host-RCE endpoint, so the tests pin the two things
that keep it safe: strict argv validation (UUID session ids, all-listed
permission modes, list-only extra args) and bearer-token auth (no token /
wrong token -> 401). The claude subprocess is never actually invoked — the
turn runner is stubbed for the auth roundtrip.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import threading
import urllib.error
import urllib.request

import pytest


# ---------------------------------------------------------------------------
# Load the daemon by path — it lives at repo-root/scripts/, outside the
# cofounder_agent package. Walk up so this works at host *and* container depth.
# ---------------------------------------------------------------------------
def _load_module():
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "scripts" / "voice_brain_host.py"
        if cand.exists():
            spec = importlib.util.spec_from_file_location("voice_brain_host", cand)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    pytest.skip("scripts/voice_brain_host.py not found", allow_module_level=True)


vbh = _load_module()

_UUID = "3699ec87-cb49-47b1-af0b-f6f6d83efa55"


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    """A validated _Config wired to harmless test values (claude = the python
    interpreter, which exists; cwd = a tmp dir)."""
    monkeypatch.setenv("VOICE_BRAIN_TOKEN", "t" * 32)
    monkeypatch.setenv("VOICE_BRAIN_CWD", str(tmp_path))
    monkeypatch.setenv("VOICE_BRAIN_CLAUDE", sys.executable)
    monkeypatch.setattr(vbh, "CFG", vbh._Config())
    return vbh.CFG


# ---------------------------------------------------------------------------
# argv validation — the injection-resistance surface
# ---------------------------------------------------------------------------


def test_build_argv_first_turn_creates(cfg):
    argv = vbh._build_argv({"session_id": _UUID, "first_turn": True, "text": "hi"})
    assert "--session-id" in argv and argv[argv.index("--session-id") + 1] == _UUID
    assert "--resume" not in argv
    assert "--permission-mode" in argv and "dontAsk" in argv


def test_build_argv_resume_when_not_first_turn(cfg):
    argv = vbh._build_argv({"session_id": _UUID, "first_turn": False})
    assert "--resume" in argv and argv[argv.index("--resume") + 1] == _UUID
    assert "--session-id" not in argv


def test_build_argv_rejects_non_uuid_session(cfg):
    with pytest.raises(ValueError, match="session_id"):
        vbh._build_argv({"session_id": "; rm -rf /", "first_turn": True})


def test_build_argv_rejects_unknown_permission_mode(cfg):
    with pytest.raises(ValueError, match="permission_mode"):
        vbh._build_argv({"session_id": _UUID, "permission_mode": "evil"})


def test_build_argv_rejects_non_list_extra_args(cfg):
    with pytest.raises(ValueError, match="extra_args"):
        vbh._build_argv({"session_id": _UUID, "extra_args": "--dangerous"})


def test_build_argv_passes_list_extra_args(cfg):
    argv = vbh._build_argv(
        {"session_id": _UUID, "first_turn": True, "extra_args": ["--allowedTools", "Read"]},
    )
    assert argv[-2:] == ["--allowedTools", "Read"]


def test_run_turn_requires_text(cfg):
    with pytest.raises(ValueError, match="text"):
        vbh._run_turn({"session_id": _UUID, "first_turn": True})


def test_run_turn_spawns_with_no_window_flags(cfg, monkeypatch):
    """The per-turn claude subprocess is spawned with
    creationflags=_NO_WINDOW_FLAGS (CREATE_NO_WINDOW on Windows, 0 elsewhere)
    so a voice turn never pops a console window on the host — feedback_no_popups.
    """
    captured: dict = {}

    class _Proc:
        returncode = 0
        stdout = b"ok"
        stderr = b""

    def _fake_run(argv, **kwargs):
        captured.update(kwargs)
        return _Proc()

    monkeypatch.setattr(vbh.subprocess, "run", _fake_run)
    out = vbh._run_turn({"session_id": _UUID, "first_turn": True, "text": "hi"})

    assert out["returncode"] == 0
    assert captured.get("creationflags") == vbh._NO_WINDOW_FLAGS
    # The injection-resistance guarantee must survive alongside the new kwarg.
    assert captured.get("shell") is False


# ---------------------------------------------------------------------------
# token auth — start a real server, stub the turn runner, hit it over HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def server(cfg, monkeypatch):
    from http.server import ThreadingHTTPServer

    monkeypatch.setattr(vbh, "_run_turn", lambda body: {"returncode": 0, "stdout": "ok", "stderr": ""})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), vbh._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", cfg.token
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post(url, token, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_turn_requires_token(server):
    base, _ = server
    code, _ = _post(f"{base}/turn", None, {"session_id": _UUID, "text": "hi"})
    assert code == 401


def test_turn_rejects_wrong_token(server):
    base, _ = server
    code, _ = _post(f"{base}/turn", "wrong-token-value-1234", {"session_id": _UUID, "text": "hi"})
    assert code == 401


def test_turn_accepts_right_token(server):
    base, token = server
    code, payload = _post(f"{base}/turn", token, {"session_id": _UUID, "first_turn": True, "text": "hi"})
    assert code == 200
    assert payload == {"returncode": 0, "stdout": "ok", "stderr": ""}


def test_healthz_open_no_token(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/healthz", timeout=5) as r:
        assert r.status == 200 and json.loads(r.read())["status"] == "ok"


# ---------------------------------------------------------------------------
# Self-configuring defaults (#1006 persistence) — token-file fallback, repo-root
# cwd default, and windowless file logging. These let the scheduled task run a
# bare `pythonw voice_brain_host.py` with NO secret/path in its definition.
# ---------------------------------------------------------------------------


import logging  # noqa: E402
import os  # noqa: E402


def test_config_reads_token_from_file_when_env_absent(monkeypatch, tmp_path):
    """No VOICE_BRAIN_TOKEN env -> read ~/.poindexter/voice_brain_token."""
    monkeypatch.delenv("VOICE_BRAIN_TOKEN", raising=False)
    monkeypatch.setenv("VOICE_BRAIN_CWD", str(tmp_path))
    monkeypatch.setenv("VOICE_BRAIN_CLAUDE", sys.executable)
    monkeypatch.setattr(vbh, "_default_poindexter_dir", lambda: str(tmp_path))
    (tmp_path / "voice_brain_token").write_text("file-token-" + "x" * 20, encoding="utf-8")

    cfg = vbh._Config()
    assert cfg.token == "file-token-" + "x" * 20


def test_config_env_token_wins_over_file(monkeypatch, tmp_path):
    """Explicit env token takes precedence over the file fallback."""
    monkeypatch.setenv("VOICE_BRAIN_TOKEN", "env-token-" + "y" * 20)
    monkeypatch.setenv("VOICE_BRAIN_CWD", str(tmp_path))
    monkeypatch.setenv("VOICE_BRAIN_CLAUDE", sys.executable)
    monkeypatch.setattr(vbh, "_default_poindexter_dir", lambda: str(tmp_path))
    (tmp_path / "voice_brain_token").write_text("file-token-should-lose", encoding="utf-8")

    cfg = vbh._Config()
    assert cfg.token == "env-token-" + "y" * 20


def test_config_defaults_cwd_to_repo_root(monkeypatch):
    """No VOICE_BRAIN_CWD -> default to the repo root holding scripts/."""
    monkeypatch.setenv("VOICE_BRAIN_TOKEN", "t" * 32)
    monkeypatch.delenv("VOICE_BRAIN_CWD", raising=False)
    monkeypatch.setenv("VOICE_BRAIN_CLAUDE", sys.executable)

    cfg = vbh._Config()
    # The default must be an existing dir that actually contains the daemon.
    assert os.path.isdir(cfg.cwd)
    assert os.path.exists(os.path.join(cfg.cwd, "scripts", "voice_brain_host.py"))


def _strip_added_file_handlers(before):
    """Remove + close any FileHandler _attach_file_log added (Windows holds the
    file open, which would block tmp cleanup and leak into later tests)."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if h not in before and isinstance(h, logging.FileHandler):
            root.removeHandler(h)
            h.close()


def test_attach_file_log_writes_to_explicit_path(monkeypatch, tmp_path):
    """VOICE_BRAIN_LOG -> logs land in that file."""
    log_path = tmp_path / "vbh.log"
    monkeypatch.setenv("VOICE_BRAIN_LOG", str(log_path))
    before = list(logging.getLogger().handlers)
    try:
        vbh._attach_file_log()
        vbh.logger.info("hello-from-test")
        for h in logging.getLogger().handlers:
            h.flush()
        assert log_path.exists()
        assert "hello-from-test" in log_path.read_text(encoding="utf-8")
    finally:
        _strip_added_file_handlers(before)


def test_attach_file_log_defaults_when_no_stderr(monkeypatch, tmp_path):
    """pythonw (sys.stderr is None) + no VOICE_BRAIN_LOG -> default log file."""
    monkeypatch.delenv("VOICE_BRAIN_LOG", raising=False)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(vbh, "_default_poindexter_dir", lambda: str(tmp_path))
    before = list(logging.getLogger().handlers)
    try:
        vbh._attach_file_log()
        assert (tmp_path / "voice_brain_host.log").exists()
    finally:
        _strip_added_file_handlers(before)


def test_attach_file_log_noop_with_stderr_and_no_env(monkeypatch, tmp_path):
    """Interactive python (real stderr) + no env -> no file handler added."""
    monkeypatch.delenv("VOICE_BRAIN_LOG", raising=False)
    monkeypatch.setattr(vbh, "_default_poindexter_dir", lambda: str(tmp_path))
    before = list(logging.getLogger().handlers)
    try:
        # sys.stderr is the real stream here (pytest capture), not None.
        vbh._attach_file_log()
        added = [h for h in logging.getLogger().handlers if h not in before]
        assert added == []
    finally:
        _strip_added_file_handlers(before)
