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
import urllib.request
import urllib.error

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
