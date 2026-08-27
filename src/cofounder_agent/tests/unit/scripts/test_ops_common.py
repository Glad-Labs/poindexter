"""Contract tests for scripts/ops_sessions/_common.py pure helpers."""
from __future__ import annotations

import logging
import os
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


# --- sampling determinism ----------------------------------------------------
# A model's baked-in sampling params vary wildly (llama3.2:3b ships none, so
# Ollama's 0.8 applied; granite4.2:3b bakes in temperature 1 / top_p 0.95).
# Leaving temperature unset makes every session's determinism a property of
# whichever pin happens to be set. The bake-off preceding the 2026-08-27
# triage-pin swap caught exactly that: sampling at Granite's baked-in 1.0, the
# same alert returned different verdicts across runs (self-consistent on only
# 4 of 8 real alerts) — in a session whose output is FILING GITHUB ISSUES.


def _captured_body(monkeypatch) -> dict:
    import httpx
    seen: dict = {}

    def _post(url, **kwargs):
        seen.update(kwargs.get("json") or {})
        return _Resp(200)

    monkeypatch.setattr(httpx, "post", _post)
    return seen


def test_ollama_chat_pins_temperature_zero_by_default(monkeypatch):
    body = _captured_body(monkeypatch)
    _common.ollama_chat("hi", model="m:1b")
    assert body["options"]["temperature"] == 0.0


def test_ollama_chat_temperature_is_overridable(monkeypatch):
    body = _captured_body(monkeypatch)
    _common.ollama_chat("hi", model="m:1b", temperature=0.7)
    assert body["options"]["temperature"] == 0.7


def test_ollama_chat_disables_thinking(monkeypatch):
    """Same reason as temperature: don't let the pin decide behaviour.

    granite4.2:3b is a thinking model — left alone it puts its reasoning in
    ``message.thinking`` and takes 41s for a one-key JSON reply vs 8s with
    thinking off. The real hazard is the failure mode already documented for
    ``ops_triage_writer_model``: a thinking model that burns its budget
    mid-trace returns an EMPTY ``message.content``, which surfaces here as an
    unparseable answer. These sessions want the answer, never the trace.
    """
    body = _captured_body(monkeypatch)
    _common.ollama_chat("hi", model="m:1b")
    assert body["think"] is False


# --- default-pin licensing (2026-08-27 audit) --------------------------------


def _default_pin(name: str) -> str:
    """The literal default in the source, independent of the caller's env.

    Read via ``ast`` rather than the imported module attribute on purpose:
    ``MODEL_*`` resolves ``OPS_OLLAMA_MODEL_*`` at import, so on a box that
    exports an override the attribute is the operator's choice, not the value
    that ships. What needs guarding is what a fresh OSS install inherits.
    """
    import ast
    tree = ast.parse((_ops_dir() / "_common.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name):
            continue
        # Expect exactly `os.environ.get("OPS_OLLAMA_MODEL_X", "<default>")`.
        # Fail loudly rather than by AttributeError if that shape ever changes,
        # so the next reader is told what to update instead of debugging a
        # traceback in a licensing guard.
        value = node.value
        if not (isinstance(value, ast.Call) and len(value.args) == 2
                and isinstance(value.args[1], ast.Constant)):
            raise AssertionError(
                f"{name} is no longer `os.environ.get(<key>, <literal default>)`; "
                "update _default_pin() to read the new shape"
            )
        return value.args[1].value
    raise AssertionError(f"{name} not found as a module-level assignment")


@pytest.mark.parametrize("pin", ["MODEL_TRIAGE", "MODEL_TESTFIX"])
def test_default_model_pins_are_permissively_licensed(pin):
    """`scripts/ops_sessions/` ships publicly — these defaults are the OSS
    product's, not just Glad Labs'.

    ``scripts/sync-to-github.sh`` does not strip this directory, so whatever is
    pinned here is what Glad-Labs/poindexter hands a fresh install. The
    2026-08-27 audit found ``llama3.2:3b`` was the last non-permissive default
    in the tree: the Llama 3.2 Community License is not OSI-approved, carries
    an acceptable-use policy and a 700M-MAU ceiling, and its upstream HF repo
    is ``gated: manual`` — a downstream user cannot fetch the weights the
    default names. Widen this allowlist only with an Apache-2.0/MIT model
    (check the registry's license layer), never to re-admit a community
    license.
    """
    permissive = {
        "granite4.2:3b",       # IBM Granite 4.2 — Apache-2.0
        "granite4.2:8b",       # Apache-2.0
        "qwen2.5-coder:7b",    # Qwen2.5-Coder — Apache-2.0
        "qwen3-coder:30b",     # Apache-2.0
    }
    assert _default_pin(pin) in permissive


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
        httpx, "get", lambda *a, **k: _TagsResp(["granite4.2:3b", "qwen2.5-coder:7b"]),
    )
    _common.preflight_model_pins("granite4.2:3b", "qwen2.5-coder:7b")


def test_preflight_missing_pin_names_model_and_remedy(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _TagsResp(["granite4.2:3b"]))
    with pytest.raises(_common.OllamaUnavailable) as exc:
        _common.preflight_model_pins("granite4.2:3b", "qwen2.5-coder:7b")
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
        _common.preflight_model_pins("granite4.2:3b")


def test_model_pins_registry_covers_every_pin():
    """MODEL_PINS is the single registry pin-check consumes; a module-level
    pin missing from it is invisible to the daily probe."""
    assert _common.MODEL_PINS == {
        "OPS_OLLAMA_MODEL_TRIAGE": _common.MODEL_TRIAGE,
        "OPS_OLLAMA_MODEL_TESTFIX": _common.MODEL_TESTFIX,
    }


# --- get_logger log-dir isolation (OPS_LOG_DIR) ------------------------------
# get_logger writes <name>-<stamp>.log where "a file here means a session ran"
# (run-session.sh writes the same shape to the same dir). On 2026-08-15 a
# pytest run driving session main()s deposited synthetic test-health/pin-check
# logs into the operator's real ~/.poindexter/logs/claude-sessions/, and one
# was read as a real 23:35 session fire during an incident investigation.
# OPS_LOG_DIR — set for every test in this directory by conftest.py's autouse
# fixture — is the seam that keeps pytest out of that directory.


def _close_session_handlers(name: str) -> None:
    logger = logging.getLogger(name)
    for handler in _common._SESSION_HANDLERS.pop(name, []):
        logger.removeHandler(handler)
        handler.close()


def test_get_logger_honors_ops_log_dir(monkeypatch, tmp_path):
    target = tmp_path / "redirected-logs"
    monkeypatch.setenv("OPS_LOG_DIR", str(target))
    try:
        log = _common.get_logger("isolation-canary")
        log.info("stays out of the operator log dir")
        assert len(list(target.glob("isolation-canary-*.log"))) == 1
        # The canary name is unique to this test, so the real operator dir
        # gaining one would prove the env seam broke — check it directly.
        if _common._LOG_DIR.exists():
            assert list(_common._LOG_DIR.glob("isolation-canary-*.log")) == []
    finally:
        _close_session_handlers("isolation-canary")


def test_default_log_dir_is_the_operator_location(monkeypatch):
    """Pins the unset-env default WITHOUT calling get_logger — calling it here
    would itself write the operator dir, the exact pollution this section is
    about."""
    monkeypatch.delenv("OPS_LOG_DIR", raising=False)
    assert _common._resolve_log_dir() == _common._LOG_DIR


def test_conftest_isolation_is_active_for_this_directory():
    """Regression guard on conftest.py's autouse fixture: if it disappears,
    every test in this dir that drives a session main() goes back to writing
    realistic-looking logs into the operator's real log dir. Fail loud here
    instead."""
    configured = os.environ.get("OPS_LOG_DIR", "")
    assert configured, "autouse OPS_LOG_DIR fixture from conftest.py is not active"
    assert Path(configured) != _common._LOG_DIR
    assert not Path(configured).is_relative_to(Path.home() / ".poindexter")


def test_get_logger_does_not_stack_handlers_across_calls(monkeypatch, tmp_path):
    """logging.getLogger(name) is process-global; a second get_logger(name)
    must replace its own handlers, not stack a second file+stream pair that
    duplicates every record into the earlier call's file."""
    monkeypatch.setenv("OPS_LOG_DIR", str(tmp_path))
    try:
        first = _common.get_logger("handler-stack-canary")
        count_after_first = len(first.handlers)
        second = _common.get_logger("handler-stack-canary")
        assert second is first
        assert len(second.handlers) == count_after_first
    finally:
        _close_session_handlers("handler-stack-canary")
