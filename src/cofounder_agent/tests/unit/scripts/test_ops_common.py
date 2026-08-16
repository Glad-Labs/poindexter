"""Contract tests for scripts/ops_sessions/_common.py pure helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import _common  # noqa: E402


def test_parse_ollama_content_extracts_message():
    payload = {"message": {"role": "assistant", "content": "hello"}, "done": True}
    assert _common.parse_ollama_content(payload) == "hello"


def test_parse_ollama_content_missing_raises():
    with pytest.raises(KeyError):
        _common.parse_ollama_content({"done": True})


def test_ollama_unavailable_is_runtimeerror():
    assert issubclass(_common.OllamaUnavailable, RuntimeError)


# --- ollama_chat error classification --------------------------------------
# Sessions catch OllamaUnavailable and nothing else, and catching it is what
# fires notify_fail. A raw httpx.HTTPStatusError escaping to the top is what
# made test-health die silently on 2026-08-07/08/09: the operator-notify path
# existed and never ran because the exception type nobody catches pages nobody.


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self):
        import httpx
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=self,
            )

    def json(self):
        return {"message": {"content": "ok"}}


def test_missing_model_404_becomes_ollama_unavailable(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(404))
    with pytest.raises(_common.OllamaUnavailable) as exc:
        _common.ollama_chat("hi", model="never-pulled:7b")
    msg = str(exc.value)
    # Must name the model AND the remedy — a bare "HTTP 404" sends the
    # operator looking at the daemon instead of running one pull command.
    assert "never-pulled:7b" in msg
    assert "ollama pull never-pulled:7b" in msg


def test_other_http_errors_also_become_ollama_unavailable(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(500))
    with pytest.raises(_common.OllamaUnavailable) as exc:
        _common.ollama_chat("hi", model="qwen2.5-coder:7b")
    assert "HTTP 500" in str(exc.value)


def test_connect_error_still_maps_to_ollama_unavailable(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(_common.OllamaUnavailable):
        _common.ollama_chat("hi", model="qwen2.5-coder:7b")


def test_successful_call_is_unaffected(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200))
    assert _common.ollama_chat("hi", model="qwen2.5-coder:7b") == "ok"


# --- preflight_model_pins (stack#3163) --------------------------------------
# A pin can be unsatisfiable for weeks with the 03:00 session failure as the
# only detector. The preflight turns that into a seconds-fast, correctly-
# classified (OllamaUnavailable -> notify_fail) startup failure.


class _TagsResp:
    def __init__(self, models: list[str], status_code: int = 200) -> None:
        self.status_code = status_code
        self._models = models

    def raise_for_status(self):
        import httpx
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=self,
            )

    def json(self):
        return {"models": [{"name": m} for m in self._models]}


def test_preflight_passes_when_all_pins_present(monkeypatch):
    import httpx
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _TagsResp(["llama3.2:3b", "qwen2.5-coder:7b"]),
    )
    _common.preflight_model_pins("llama3.2:3b", "qwen2.5-coder:7b")


def test_preflight_missing_pin_names_model_and_remedy(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _TagsResp(["llama3.2:3b"]))
    with pytest.raises(_common.OllamaUnavailable) as exc:
        _common.preflight_model_pins("llama3.2:3b", "qwen2.5-coder:7b")
    msg = str(exc.value)
    assert "qwen2.5-coder:7b" in msg
    assert "ollama pull qwen2.5-coder:7b" in msg
    assert "OPS_OLLAMA_MODEL_" in msg


def test_preflight_normalizes_untagged_pin_to_latest(monkeypatch):
    """Ollama registers an untagged pull as ``<name>:latest`` — a bare pin
    must match it rather than false-alarm."""
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _TagsResp(["phi4:latest"]))
    _common.preflight_model_pins("phi4")


def test_preflight_connect_failure_is_ollama_unavailable(monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", _boom)
    with pytest.raises(_common.OllamaUnavailable):
        _common.preflight_model_pins("llama3.2:3b")


def test_model_pins_registry_covers_every_pin():
    """MODEL_PINS is the single registry pin-check consumes; a module-level
    pin missing from it is invisible to the daily probe."""
    assert _common.MODEL_PINS == {
        "OPS_OLLAMA_MODEL_TRIAGE": _common.MODEL_TRIAGE,
        "OPS_OLLAMA_MODEL_TESTFIX": _common.MODEL_TESTFIX,
    }
