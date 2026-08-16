"""Tests for the pin-check ops session (stack#3163 option 2).

The session is the periodic half of the fix: the startup preflight makes a
broken pin fail fast at 03:00, this makes it page the SAME DAY at 12:30
instead of silently waiting for the next LLM session to trip over it.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import _common  # noqa: E402
import pin_check  # noqa: E402


def test_green_when_all_pins_present(monkeypatch):
    monkeypatch.setattr(_common, "preflight_model_pins", lambda *m, **k: None)
    calls: list = []
    monkeypatch.setattr(_common, "notify_fail", lambda *a, **k: calls.append(a))
    assert pin_check.main() == 0
    assert not calls


def test_missing_pin_notifies_and_exits_nonzero(monkeypatch):
    def _boom(*models, **k):
        raise _common.OllamaUnavailable("model pin(s) not present: 'qwen2.5-coder:7b'")

    notified: list = []
    monkeypatch.setattr(_common, "preflight_model_pins", _boom)
    monkeypatch.setattr(
        _common, "notify_fail", lambda title, detail, source: notified.append((title, detail, source)),
    )
    assert pin_check.main() == 1
    assert len(notified) == 1
    title, detail, source = notified[0]
    assert "pin" in title
    assert "qwen2.5-coder:7b" in detail
    # The notification names every env knob so the operator can repoint
    # instead of pulling, if that's the right fix.
    assert "OPS_OLLAMA_MODEL_TRIAGE" in detail
    assert source == "pin_check"


def test_checks_the_shared_registry(monkeypatch):
    """The probe must consume _common.MODEL_PINS, not restate the pin list."""
    seen: list = []
    monkeypatch.setattr(
        _common, "preflight_model_pins", lambda *models, **k: seen.extend(models),
    )
    monkeypatch.setattr(_common, "notify_fail", lambda *a, **k: None)
    pin_check.main()
    assert sorted(seen) == sorted(_common.MODEL_PINS.values())
