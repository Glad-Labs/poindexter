from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import alert_triage as at  # noqa: E402


class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess (returncode + stdout only)."""

    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


def test_prompt_includes_alert_and_probe():
    p = at.build_classification_prompt("BrainDaemonStale", "dispatched ok", "def probe(): ...")
    assert "BrainDaemonStale" in p
    assert "def probe()" in p


def test_parse_classification_valid_json():
    raw = '{"classification": "probe_bug", "reason": "dedup broken", "suspect_file": "brain/x_probe.py"}'
    out = at.parse_classification(raw)
    assert out["classification"] == "probe_bug"
    assert out["suspect_file"] == "brain/x_probe.py"


def test_parse_classification_normalizes_and_defaults():
    out = at.parse_classification('{"classification": "REAL_FAILURE"}')
    assert out["classification"] == "real_failure"
    assert out["reason"] == ""


def test_parse_classification_bad_json_raises():
    with pytest.raises(ValueError):
        at.parse_classification("not json")


def test_has_open_probe_issue_true_when_gh_finds_a_match(monkeypatch):
    calls = []

    def fake_gh(*args):
        calls.append(args)
        return _FakeProc(0, '[{"number": 2350}]')

    monkeypatch.setattr(at.c, "gh", fake_gh)
    assert at._has_open_probe_issue("FinanceMercuryPollStale") is True
    # queried the right alert, scoped to open issues in this repo
    (call,) = calls
    assert "FinanceMercuryPollStale" in " ".join(call)
    assert "open" in call
    assert at.REPO in call


def test_has_open_probe_issue_false_when_gh_finds_nothing(monkeypatch):
    monkeypatch.setattr(at.c, "gh", lambda *_a: _FakeProc(0, "[]"))
    assert at._has_open_probe_issue("BrandNewAlert") is False


def test_has_open_probe_issue_failsafe_on_gh_error(monkeypatch):
    # gh itself failed (auth hiccup, rate limit, ...) — fail toward "don't file"
    monkeypatch.setattr(at.c, "gh", lambda *_a: _FakeProc(1, ""))
    assert at._has_open_probe_issue("X") is True


def test_has_open_probe_issue_failsafe_on_bad_json(monkeypatch):
    monkeypatch.setattr(at.c, "gh", lambda *_a: _FakeProc(0, "not json"))
    assert at._has_open_probe_issue("X") is True


def test_main_skips_already_tracked_alert_without_classifying_or_filing(monkeypatch):
    def fake_asyncio_run(coro):
        coro.close()  # never actually run — avoid a "coroutine never awaited" warning
        return [{"alertname": "FinanceMercuryPollStale", "dispatch_result": "sent", "n": 19}]

    def boom_ollama(*a, **k):
        raise AssertionError("an already-tracked alert must not be re-classified")

    def boom_gh(*a, **k):
        raise AssertionError("an already-tracked alert must not file a duplicate issue")

    monkeypatch.setattr(at.c, "get_logger", lambda _name: logging.getLogger("test-alert-triage"))
    monkeypatch.setattr(at.c, "asyncio_run", fake_asyncio_run)
    monkeypatch.setattr(at, "_has_open_probe_issue", lambda _alertname: True)
    monkeypatch.setattr(at.c, "ollama_chat", boom_ollama)
    monkeypatch.setattr(at.c, "gh", boom_gh)

    assert at.main() == 0


def test_main_classifies_and_files_when_nothing_open_yet(monkeypatch):
    filed = []

    def fake_asyncio_run(coro):
        coro.close()
        return [{"alertname": "NewNoisyAlert", "dispatch_result": "sent", "n": 8}]

    def fake_gh(*args):
        filed.append(args)
        return _FakeProc(0, "")

    monkeypatch.setattr(at.c, "get_logger", lambda _name: logging.getLogger("test-alert-triage"))
    monkeypatch.setattr(at.c, "asyncio_run", fake_asyncio_run)
    monkeypatch.setattr(at, "_has_open_probe_issue", lambda _alertname: False)
    monkeypatch.setattr(
        at.c, "ollama_chat",
        lambda *_a, **_k: '{"classification": "probe_bug", "reason": "r", "suspect_file": "f.py"}',
    )
    monkeypatch.setattr(at.c, "gh", fake_gh)

    assert at.main() == 0
    (call,) = filed
    assert "NewNoisyAlert" in " ".join(call)
