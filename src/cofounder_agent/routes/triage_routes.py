"""``POST /api/triage`` — firefighter ops LLM diagnosis route.

Implements step 3 of Glad-Labs/poindexter#347. Given an ``alert_event_id``
plus the alertname + severity + labels + annotations the brain just
dispatched, this route:

1. Builds a curated context bundle via
   :func:`services.firefighter_service.build_triage_context` (alert row +
   5 same-name history + 10 most-recent ``audit_log`` rows + the affected
   ``pipeline_tasks`` row when a ``task_id`` label is present + a snapshot
   of relevant ``app_settings`` keys).
2. Verifies a provider is wired up for the configured ``ops_triage`` tier
   and that ``cost_guard`` would not deny the call.
3. Calls :func:`services.firefighter_service.run_triage` to produce a
   single short paragraph diagnosis via the local LLM.
4. Returns ``{diagnosis, model, tokens, ms}``.

Design notes:

- **Auth.** OAuth-only via the shared ``verify_api_token`` dependency
  (Phase 3 #249 closed the static-Bearer fallback). The brain is a
  registered OAuth client (``brain_oauth_client_id`` / ``_secret``) and
  mints JWTs through ``POST /token``.

- **Idempotency.** A process-local TTL cache keyed on ``alert_event_id``
  returns the cached diagnosis on a duplicate call within the configured
  window (``ops_triage_cache_ttl_seconds``, default 3600). The spec
  forbids new columns on ``alert_events`` for v1 — diagnoses are
  transient by design.

- **Failure mapping.**

  =====================================  ================  ====================
  Condition                              HTTP status       Body
  =====================================  ================  ====================
  Auth missing / bad JWT                 401               from middleware
  ``ops_triage_enabled=false``           503               ``triage_disabled``
  No provider for the configured tier    503               ``no_provider``
  ``cost_guard`` denies the call         402               ``cost_guarded``
  LLM returns empty (or raises)          200 + empty diag  ``diagnosis=""``
  =====================================  ================  ====================

  Empty diagnosis is intentionally NOT a 5xx — the brain side reads
  ``diagnosis==""`` as "skip the follow-up" per the spec's failure
  table.

- **Pluggable model_router.** The default is :func:`_default_router`,
  a thin adapter over :func:`services.llm_text.ollama_chat_text`.
  Tests inject their own via :func:`set_model_router_for_tests`.

- **DI.** Site config + DB pool come through the existing dependencies
  (``get_site_config_dependency``, ``get_database_dependency``). No
  module-level singletons — Phase H rules.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from middleware.api_token_auth import verify_api_token
from services.firefighter_service import build_triage_context, run_triage
from services.logger_config import get_logger
from services.site_config import SiteConfig
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(prefix="/api/triage", tags=["triage"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TriageRequest(BaseModel):
    """Brain-side payload — everything the dispatcher already has on the row.

    The route re-fetches the row from the DB via ``alert_event_id`` (so
    the LLM context always reflects the canonical persisted state, not
    whatever the brain reshaped). The other fields are kept on the
    request for logging + future use (per-severity routing, etc.).
    """

    alert_event_id: int = Field(..., description="alert_events.id (canonical key)")
    alertname: str = Field("", description="Mirror of alert_events.alertname")
    severity: str = Field("", description="Mirror of alert_events.severity")
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)


class TriageResponse(BaseModel):
    diagnosis: str = Field(..., description="One short paragraph (or '' on skip)")
    model: str = Field(..., description="Concrete model that produced the diagnosis")
    tokens: int = Field(..., ge=0)
    ms: int = Field(..., ge=0, description="Wall-clock LLM round-trip time")
    cached: bool = Field(False, description="True if served from idempotency cache")


# ---------------------------------------------------------------------------
# Idempotency cache — process-local, TTL-based. Spec says no new columns
# on alert_events; this keeps diagnoses transient (matches the v1
# Telegram-thread design).
# ---------------------------------------------------------------------------


_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}


def _cache_get(alert_event_id: int, ttl_seconds: int) -> dict[str, Any] | None:
    now = time.time()
    hit = _CACHE.get(alert_event_id)
    if hit is None:
        return None
    inserted_at, payload = hit
    if now - inserted_at > ttl_seconds:
        _CACHE.pop(alert_event_id, None)
        return None
    return payload


def _cache_put(alert_event_id: int, payload: dict[str, Any]) -> None:
    _CACHE[alert_event_id] = (time.time(), payload)


def _cache_clear_for_tests() -> None:
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Default model_router adapter — wraps the local Ollama plain-text call
# in the {text, model, tokens} shape that firefighter_service.run_triage
# expects. Cloud paths and cost_guard preflight live in
# ``_provider_check`` / ``_cost_guard_check`` so the wiring stays
# orthogonal.
# ---------------------------------------------------------------------------


class _DefaultModelRouter:
    """Thin adapter — fulfils ``services.firefighter_service._ModelRouterLike``.

    Calls :func:`services.llm_text.ollama_chat_text` against the local
    Ollama endpoint. Returns the text wrapped in the standard envelope.
    Always uses the local-LLM path; cloud routing is not currently in
    scope for ``ops_triage`` per the v1 spec.
    """

    def __init__(self, site_config: SiteConfig) -> None:
        self._site_config = site_config

    async def invoke(
        self,
        *,
        model_class: str,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        del model_class, max_tokens  # tier→model resolution is the writer-model setting
        from services.llm_text import ollama_chat_text, resolve_local_model

        model_name = resolve_local_model(None)
        text = await ollama_chat_text(prompt=user, system=system, model=model_name)
        return {"text": text, "model": f"ollama/{model_name}", "tokens": 0}


# ``_router_factory`` is the seam tests use to inject a stub router.
# Production leaves it None and the route constructs ``_DefaultModelRouter``
# per request (cheap — no state on the adapter).
_router_factory: Any | None = None


def set_model_router_for_tests(factory: Any | None) -> None:
    """Inject a router factory for unit tests.

    ``factory`` is a callable ``(site_config) -> router-like`` (or
    ``None`` to restore the default). Cleared in conftest fixtures
    between tests so leakage is impossible.
    """
    global _router_factory
    _router_factory = factory


def _build_router(site_config: SiteConfig) -> Any:
    if _router_factory is not None:
        return _router_factory(site_config)
    return _DefaultModelRouter(site_config)


# ---------------------------------------------------------------------------
# Provider + cost_guard pre-checks. These are split out so the route can
# map their failures cleanly to 503 / 402 without depending on the
# router itself raising the right exception type.
# ---------------------------------------------------------------------------


def _provider_check(site_config: SiteConfig) -> None:
    """Raise 503 ``no_provider`` when the tier has no configured provider.

    For the local-default ops_triage tier, ``no provider`` means
    ``local_llm_api_url`` is unset (or unreachable wiring intent). We
    don't probe Ollama here — that would couple latency. Operators get
    a 503 they can act on (set the URL) instead of an obscure 5xx.
    """
    base_url = site_config.get("local_llm_api_url", "").strip()
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "no_provider",
                "message": (
                    "model_router has no provider for the requested ops_triage "
                    "tier (local_llm_api_url is unset). Set "
                    "app_settings.local_llm_api_url or change "
                    "ops_triage_model_class to a tier with a wired provider."
                ),
            },
        )


async def _cost_guard_check(site_config: SiteConfig) -> None:
    """Raise 402 ``cost_guarded`` when the cost guard would deny the call.

    For the local-default ops_triage tier, ``CostGuard.preflight`` is a
    no-op (``is_local=True``). The check is wired in ahead of time so a
    later A/B that points ops_triage at a paid tier surfaces the denial
    cleanly. Failures other than ``CostGuardExhausted`` are logged and
    swallowed — a broken cost_guard MUST NOT silently block triage.
    """
    try:
        from services.cost_guard import (
            CostEstimate,
            CostGuard,
            CostGuardExhausted,
            is_local_base_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[triage] cost_guard module unavailable: %s — skipping check", exc)
        return

    base_url = site_config.get("local_llm_api_url", "")
    if is_local_base_url(base_url):
        return

    try:
        guard = CostGuard()
        estimate = CostEstimate(
            estimated_usd=0.0,
            is_local=False,
            model="ops_triage",
            provider="ops_triage",
        )
        await guard.preflight(estimate)
    except CostGuardExhausted as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "cost_guarded",
                "message": str(exc),
                "scope": getattr(exc, "scope", "daily"),
                "spent_usd": getattr(exc, "spent_usd", 0.0),
                "limit_usd": getattr(exc, "limit_usd", 0.0),
            },
        ) from exc


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=TriageResponse,
    status_code=status.HTTP_200_OK,
    summary="Run firefighter ops LLM triage on a persisted alert_events row",
    responses={
        200: {"description": "Diagnosis paragraph (possibly empty when LLM produced nothing)"},
        401: {"description": "Missing / invalid OAuth JWT"},
        402: {"description": "cost_guard denied the call"},
        503: {"description": "Triage disabled or no provider for the configured tier"},
    },
)
async def post_triage(
    payload: TriageRequest,
    _token: str = Depends(verify_api_token),
    site_config: SiteConfig = Depends(get_site_config_dependency),
    db_service: Any = Depends(get_database_dependency),
) -> TriageResponse:
    """Diagnose one alert. Idempotent on ``payload.alert_event_id``."""

    # 1. Master kill-switch.
    if not site_config.get_bool("ops_triage_enabled", True):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "triage_disabled",
                "message": (
                    "ops_triage_enabled=false — operators have turned the "
                    "firefighter LLM off. Flip the app_settings row to "
                    "re-enable."
                ),
            },
        )

    # 2. Idempotency cache. Second call for the same alert returns the
    #    cached diagnosis without re-invoking the LLM.
    cache_ttl = site_config.get_int("ops_triage_cache_ttl_seconds", 3600)
    cached = _cache_get(payload.alert_event_id, cache_ttl)
    if cached is not None:
        logger.debug(
            "[triage] cache hit alert_event_id=%s", payload.alert_event_id,
        )
        return TriageResponse(**cached, cached=True)

    # 3. Pre-checks. These map cleanly to 503 / 402 per the spec table.
    _provider_check(site_config)
    await _cost_guard_check(site_config)

    # 4. Build context + invoke router. ``run_triage`` swallows router
    #    exceptions and returns ``diagnosis=""`` — the spec's "LLM empty"
    #    contract — so callers always get a 200.
    pool = getattr(db_service, "pool", db_service)
    try:
        context = await build_triage_context(pool, payload.alert_event_id, site_config)
    except Exception as exc:
        logger.exception(
            "[triage] build_triage_context raised for alert_event_id=%s",
            payload.alert_event_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "context_build_failed", "message": str(exc)[:400]},
        ) from exc

    router_obj = _build_router(site_config)
    result = await run_triage(context, site_config, router_obj)

    response_payload = {
        "diagnosis": result.get("diagnosis", "") or "",
        "model": result.get("model", "ops_triage"),
        "tokens": int(result.get("tokens", 0) or 0),
        "ms": int(result.get("ms", 0) or 0),
    }

    # 5. Cache success (including the spec'd empty-diagnosis case — the
    #    brain skips the follow-up either way; caching prevents a retry
    #    loop from re-burning LLM budget on the same row).
    _cache_put(payload.alert_event_id, response_payload)

    logger.info(
        "[triage] alert_event_id=%s alertname=%s model=%s tokens=%d ms=%d "
        "diag_len=%d",
        payload.alert_event_id, payload.alertname, response_payload["model"],
        response_payload["tokens"], response_payload["ms"],
        len(response_payload["diagnosis"]),
    )

    return TriageResponse(**response_payload, cached=False)
