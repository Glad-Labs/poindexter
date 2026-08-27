"""Unit tests for routes/remediation_routes.py (firefighter Plan B).

``POST /api/remediation/select`` is a thin adapter over
``services.firefighter_service.select_remediation_action``. These tests cover
the route's own responsibilities — auth, the kill-switch, the provider check,
and faithful pass-through of the service result — plus one end-to-end proof
that the "off-list pick degrades to abstain" safety contract holds at the HTTP
boundary (not just in the service unit tests).

Simpler than the triage route: no idempotency cache and no cost_guard (the
selector is Ollama-only / $0 by policy). Nothing here hits a real LLM,
Postgres, or Telegram.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.remediation_routes import router, set_model_router_for_tests
from services.site_config import SiteConfig
from utils.route_utils import get_database_dependency, get_site_config_dependency

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_CATALOG = [
    {
        "name": "restart_container",
        "description": "Restart a docker container by name.",
        "params_schema": {"container": "str"},
    },
    {
        "name": "run_auto_remediate",
        "description": "Sweep stuck pipeline tasks.",
        "params_schema": {},
    },
]


class _MockDB:
    """DB service stub — the route only reaches for ``.pool`` (unused by the
    selector itself; the stub router ignores it)."""

    def __init__(self, pool):
        self.pool = pool


def _build_app(*, site_cfg: SiteConfig | None = None) -> FastAPI:
    if site_cfg is None:
        site_cfg = SiteConfig(initial_config={
            "ops_firefighter_enabled": "true",
            "local_llm_api_url": "http://localhost:11434",
            "ops_firefighter_model": "ollama/llama3.2:3b",
        })

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: _MockDB(MagicMock())
    app.dependency_overrides[get_site_config_dependency] = lambda: site_cfg
    return app


def _stub_router_factory(text: str, model: str = "ollama/llama3.2:3b", tokens: int = 12):
    """Router-factory whose ``invoke`` returns ``text`` verbatim — the route
    delegates parsing/validation to ``select_remediation_action``, so ``text``
    is the raw model output under test."""

    def _factory(_site_config):
        router_obj = MagicMock()
        router_obj.invoke = AsyncMock(return_value={
            "text": text, "model": model, "tokens": tokens,
        })
        return router_obj

    return _factory


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Clear the injected router-factory between tests so leakage is impossible."""
    set_model_router_for_tests(None)
    yield
    set_model_router_for_tests(None)


def _payload(*, alertname: str = "PyroscopeDown", catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "alert": {"labels": {"alertname": alertname, "severity": "warning"}},
        "action_catalog": _CATALOG if catalog is None else catalog,
    }


def _valid_selection_text() -> str:
    return json.dumps({
        "action_name": "restart_container",
        "params": {"container": "poindexter-pyroscope"},
        "confidence": 0.82,
        "reason": "profiler scrape down; restart is safe",
    })


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuth:
    def test_missing_authorization_returns_401(self):
        # Build the app WITHOUT overriding verify_api_token so the real
        # dependency runs and rejects the missing header.
        app = _build_app()
        client = TestClient(app)
        resp = client.post("/api/remediation/select", json=_payload())
        assert resp.status_code == 401

    def test_overridden_auth_passes(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory(_valid_selection_text()))
        client = TestClient(app)
        resp = client.post("/api/remediation/select", json=_payload())
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 503 paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKillSwitchAndProvider:
    def test_disabled_returns_503_firefighter_disabled(self):
        site_cfg = SiteConfig(initial_config={
            "ops_firefighter_enabled": "false",
            "local_llm_api_url": "http://localhost:11434",
        })
        app = _build_app(site_cfg=site_cfg)
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory(_valid_selection_text()))
        client = TestClient(app)
        resp = client.post("/api/remediation/select", json=_payload())
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "firefighter_disabled"

    def test_missing_local_llm_url_returns_503_no_provider(self):
        site_cfg = SiteConfig(initial_config={
            "ops_firefighter_enabled": "true",
            "local_llm_api_url": "",
        })
        app = _build_app(site_cfg=site_cfg)
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory(_valid_selection_text()))
        client = TestClient(app)
        resp = client.post("/api/remediation/select", json=_payload())
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "no_provider"


# ---------------------------------------------------------------------------
# Happy path — faithful pass-through of the service result
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHappyPath:
    def test_valid_selection_passes_through(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory(_valid_selection_text()))
        client = TestClient(app)
        resp = client.post("/api/remediation/select", json=_payload())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action_name"] == "restart_container"
        assert body["params"] == {"container": "poindexter-pyroscope"}
        assert body["confidence"] == pytest.approx(0.82)
        assert body["reason"]
        assert body["model"] == "ollama/llama3.2:3b"
        assert body["ms"] >= 0


# ---------------------------------------------------------------------------
# Abstain contract at the HTTP boundary — an off-list / bad / failed pick
# MUST surface as action_name="" (200), never an executable action.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAbstainAtBoundary:
    def test_off_list_action_returns_200_abstain(self):
        # The safety-critical case: model names an action outside the catalog.
        # The route MUST return 200 with an empty action_name (the brain reads
        # that as "page as usual"), NEVER pass the unvalidated action through.
        off_list = json.dumps(
            {"action_name": "rm_minus_rf", "params": {}, "confidence": 0.99, "reason": "trust me"}
        )
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory(off_list))
        client = TestClient(app)
        resp = client.post("/api/remediation/select", json=_payload())
        assert resp.status_code == 200, resp.text
        assert resp.json()["action_name"] == ""

    def test_malformed_json_returns_200_abstain(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory("not json, just prose"))
        client = TestClient(app)
        resp = client.post("/api/remediation/select", json=_payload())
        assert resp.status_code == 200
        assert resp.json()["action_name"] == ""

    def test_router_raises_returns_200_abstain(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"

        def _factory(_cfg):
            router_obj = MagicMock()
            router_obj.invoke = AsyncMock(side_effect=RuntimeError("ollama down"))
            return router_obj

        set_model_router_for_tests(_factory)
        client = TestClient(app)
        resp = client.post("/api/remediation/select", json=_payload())
        assert resp.status_code == 200
        assert resp.json()["action_name"] == ""

    def test_empty_catalog_returns_200_abstain(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        # A stub that would return a valid-looking pick if called — the service
        # short-circuits on the empty catalog before invoking it.
        set_model_router_for_tests(_stub_router_factory(_valid_selection_text()))
        client = TestClient(app)
        resp = client.post("/api/remediation/select", json=_payload(catalog=[]))
        assert resp.status_code == 200
        assert resp.json()["action_name"] == ""


# ---------------------------------------------------------------------------
# _SelectorModelRouter model resolution — the DEFAULT router (not the stub
# used above) resolves its model. Remediation selection is a SATELLITE phase
# (it names a docker/sweep action; it is NOT the blog draft), so a paid
# ``pipeline_writer_model`` pin must never leak into it. Regression guard for
# the 2026-07-07 Sonnet-canary leak (Refs Glad-Labs/poindexter#866): the
# selector used to fall through to ``resolve_local_model`` (returns the writer
# pin verbatim); it now routes through ``resolve_local_writer_model``.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectorModelRouterResolution:
    @pytest.mark.asyncio
    async def test_paid_writer_does_not_leak_uses_local_pin(self):
        """No ``ops_firefighter_model`` + a PAID ``pipeline_writer_model``: the
        selector must resolve the LOCAL writer pin, never the paid model."""
        from routes.remediation_routes import _SelectorModelRouter

        sc = SiteConfig(initial_config={
            "ops_firefighter_model": "",  # no dedicated override
            "pipeline_writer_model": "anthropic/claude-sonnet-5",  # PAID
            "pipeline_local_writer_model": "ollama/gemma3:27b",  # LOCAL pin
            "local_llm_api_url": "http://localhost:11434",
        })
        router_obj = _SelectorModelRouter(sc)

        with patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value="{}"),
        ) as mock_chat:
            result = await router_obj.invoke(
                model_class="ops_firefighter", system="s", user="u",
            )

        # Pre-fix returned "anthropic/claude-sonnet-5" verbatim (the leak).
        assert mock_chat.await_args.kwargs["model"] == "gemma3:27b"
        assert result["model"] == "ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_selector_disables_the_thinking_channel(self):
        """``think=False`` on every selector call — the invariant that makes the
        default pin safe.

        ``ops_firefighter_model`` defaults to ``ollama/granite4.2:3b`` (Apache-2.0,
        chosen when the 2026-08-27 license audit retired the non-permissive
        llama3.2:3b), and Granite is a THINKING model.

        Ollama's raw API would keep the trace out of the way in a separate
        ``message.thinking`` field — but this call goes through LiteLLM, which
        returns the deliberation INLINE in ``content`` (measured: 1.7-2.2 KB of
        "We need to respond with ONLY a JSON object…" wrapping the answer). The
        ``strip_think_blocks`` call downstream cannot help, because that prose
        carries no ``<think>`` tags.

        Drop this kwarg and nothing fails loudly: the route still 200s, and
        ``_parse_selection_json``'s ``{...}``-span fallback often still digs the
        object out — so the damage shows up only as degraded picks on some
        alerts. That is exactly the kind of regression a test has to hold,
        because neither review nor a smoke test will catch it.
        """
        from routes.remediation_routes import _SelectorModelRouter

        sc = SiteConfig(initial_config={
            "ops_firefighter_model": "ollama/granite4.2:3b",
            "local_llm_api_url": "http://localhost:11434",
        })

        with patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value="{}"),
        ) as mock_chat:
            await _SelectorModelRouter(sc).invoke(
                model_class="ops_firefighter", system="s", user="u",
            )

        assert mock_chat.await_args.kwargs["think"] is False

    @pytest.mark.asyncio
    async def test_dedicated_firefighter_model_override_still_wins(self):
        """The dedicated ``ops_firefighter_model`` pin takes precedence over the
        writer chain and is unaffected by the satellite-resolver swap."""
        from routes.remediation_routes import _SelectorModelRouter

        sc = SiteConfig(initial_config={
            "ops_firefighter_model": "ollama/llama3.2:3b",
            "pipeline_writer_model": "anthropic/claude-sonnet-5",  # would leak if reached
            "local_llm_api_url": "http://localhost:11434",
        })
        router_obj = _SelectorModelRouter(sc)

        with patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value="{}"),
        ) as mock_chat:
            await router_obj.invoke(model_class="ops_firefighter", system="s", user="u")

        assert mock_chat.await_args.kwargs["model"] == "llama3.2:3b"
