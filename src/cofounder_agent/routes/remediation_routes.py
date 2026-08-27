"""``POST /api/remediation/select`` — firefighter LLM long-tail action selector.

Plan B of the self-healing firefighter (``docs/superpowers/specs/
2026-07-04-self-healing-firefighter-design.md``). When an alert has NO
deterministic ``remediation_rule``, the brain calls this route with the alert
plus the registry's action catalog; a small local model picks ONE catalog
action (or abstains). The route is a thin adapter over
:func:`services.firefighter_service.select_remediation_action` — the
validation + "abstain on anything wrong" contract lives there, so an off-list
or malformed model output can never reach the brain as an executable action.

Design notes:

- **Auth.** OAuth-only via the shared ``verify_api_token`` dependency, same as
  ``/api/triage``. The brain is a registered OAuth client and mints JWTs.
- **Local-only, no cost_guard.** The selector runs on local Ollama
  (``ops_firefighter_model``); the firefighter is Ollama-only by policy (never a
  cloud LLM), so every call is $0 and there is no cost_guard preflight.
- **No caching.** Unlike triage (an idempotent diagnosis), a selection is a live
  decision the brain re-requests per persistent alert; the engine's circuit
  breaker + global rate cap bound how often a chosen action actually runs.
- **Failure mapping.** 401 bad JWT (middleware); 503 ``firefighter_disabled``
  when ``ops_firefighter_enabled=false``; 503 ``no_provider`` when
  ``local_llm_api_url`` is unset. A model / parse failure is NOT a 5xx — the
  service returns ``action_name=""`` (abstain) and the route returns 200, which
  the brain reads as "no LLM pick → page as usual".
- **Pluggable model_router.** The default :class:`_SelectorModelRouter` wraps
  the local Ollama call; tests inject their own via
  :func:`set_model_router_for_tests` (mirrors ``triage_routes``).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from middleware.api_token_auth import verify_api_token
from services.firefighter_service import select_remediation_action
from services.logger_config import get_logger
from services.site_config import SiteConfig
from utils.rate_limiter import _settings_limit, limiter
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/remediation",
    tags=["remediation"],
    # Operator surface — auth enforced on every route.
    dependencies=[Depends(verify_api_token)],
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SelectRequest(BaseModel):
    """Brain-side payload: the about-to-page alert + the actions on offer.

    ``action_catalog`` is the brain's allowlisted registry actions — the worker
    never imports the brain registry, so the catalog crosses the wire. Each
    entry is ``{name, description, params_schema}``.
    """

    alert: dict[str, Any] = Field(default_factory=dict)
    action_catalog: list[dict[str, Any]] = Field(default_factory=list)


class SelectResponse(BaseModel):
    action_name: str = Field(..., description="Chosen catalog action, or '' to abstain")
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field("")
    model: str = Field("", description="Concrete model that produced the selection")
    ms: int = Field(..., ge=0, description="Wall-clock LLM round-trip time")


# ---------------------------------------------------------------------------
# Default model_router adapter — local Ollama, resolves ops_firefighter_model.
# Mirrors triage_routes._DefaultModelRouter; threads the pool so the call holds
# gpu.lock("ollama") (#1794) instead of ollama_chat_text's lock-bypassing
# direct-httpx fallback.
# ---------------------------------------------------------------------------


class _SelectorModelRouter:
    def __init__(self, site_config: SiteConfig, pool: Any = None) -> None:
        self._site_config = site_config
        self._pool = pool

    async def invoke(
        self, *, model_class: str, system: str, user: str, max_tokens: int | None = None
    ) -> dict[str, Any]:
        del model_class, max_tokens  # model is the ops_firefighter_model setting
        from services.llm_providers.thinking_models import strip_think_blocks
        from services.llm_text import ollama_chat_text, resolve_local_writer_model

        configured = (self._site_config.get("ops_firefighter_model", "") or "").strip()
        # Remediation selection is a SATELLITE phase (it names a docker/sweep
        # action; it is NOT the blog draft), so the fallback resolves a
        # guaranteed-LOCAL writer-grade model and never bills cloud prices when
        # pipeline_writer_model is pinned to a paid model for a writer experiment
        # (the 2026-07-07 Sonnet-canary, Glad-Labs/poindexter#866).
        model_name = (
            configured.removeprefix("ollama/")
            if configured
            else resolve_local_writer_model(None, site_config=self._site_config)
        )
        raw = await ollama_chat_text(
            prompt=user, system=system, model=model_name,
            site_config=self._site_config, pool=self._pool,
            # think=False is what makes a THINKING model safe to pin here, and
            # the default (granite4.2:3b) is one.
            #
            # Do NOT reason from Ollama's raw API here. Called raw, Ollama puts
            # the trace in a separate ``message.thinking`` field and leaves
            # ``content`` clean — which makes thinking look like a mere latency
            # cost. This call does not go raw: it goes through LiteLLM, and
            # LiteLLM hands the deliberation back INLINE IN ``content`` with
            # ``reasoning_content`` empty. Measured on this exact selector
            # prompt with granite4.2:3b: 1696-2202 chars of "We need to respond
            # with ONLY a JSON object, no prose. The system says..." wrapped
            # around the answer, versus a clean 220-char object with think=False.
            # ``strip_think_blocks`` below cannot rescue that — the prose
            # carries no <think> tags to strip — so _parse_selection_json is
            # left digging a JSON span out of an essay.
            #
            # Harmless on non-thinking models (Ollama ignores the field), so an
            # operator who re-pins one loses nothing.
            think=False,
        )
        # Belt-and-braces: think=False suppresses the separate thinking channel,
        # but a model that emits INLINE <think> blocks into content would still
        # leak them into the JSON the selector must parse. Strip regardless.
        text = strip_think_blocks(raw)
        return {"text": text, "model": f"ollama/{model_name}", "tokens": 0}


_router_factory: Any | None = None


def set_model_router_for_tests(factory: Any | None) -> None:
    """Inject a ``(site_config) -> router-like`` factory for unit tests
    (``None`` restores the default). Cleared between tests in conftest."""
    global _router_factory
    _router_factory = factory


def _build_router(site_config: SiteConfig, pool: Any = None) -> Any:
    if _router_factory is not None:
        return _router_factory(site_config)
    return _SelectorModelRouter(site_config, pool=pool)


def _provider_check(site_config: SiteConfig) -> None:
    """503 ``no_provider`` when no local LLM endpoint is configured."""
    if not site_config.get("local_llm_api_url", "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "no_provider",
                "message": (
                    "firefighter selector has no local provider "
                    "(local_llm_api_url is unset). Set app_settings.local_llm_api_url."
                ),
            },
        )


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post(
    "/select",
    response_model=SelectResponse,
    status_code=status.HTTP_200_OK,
    summary="Select one remediation action for an un-ruled alert (or abstain)",
    responses={
        200: {"description": "Selection (action_name='' when the model abstains or output was invalid)"},
        401: {"description": "Missing / invalid OAuth JWT"},
        503: {"description": "Firefighter disabled or no local provider"},
    },
)
@limiter.limit(_settings_limit("rate_limit_remediation_select_per_ip", "30/minute"))
async def post_select(
    request: Request,
    payload: SelectRequest,
    _token: str = Depends(verify_api_token),
    site_config: SiteConfig = Depends(get_site_config_dependency),
    db_service: Any = Depends(get_database_dependency),
) -> SelectResponse:
    """Pick one allowlisted action for an un-ruled alert, or abstain."""
    if not site_config.get_bool("ops_firefighter_enabled", True):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "firefighter_disabled",
                "message": (
                    "ops_firefighter_enabled=false — the self-healing firefighter "
                    "is off. Flip the app_settings row to re-enable."
                ),
            },
        )
    _provider_check(site_config)

    pool = getattr(db_service, "pool", db_service)
    router_obj = _build_router(site_config, pool)
    result = await select_remediation_action(
        alert=payload.alert,
        action_catalog=payload.action_catalog,
        site_config=site_config,
        model_router=router_obj,
    )

    alertname = (payload.alert.get("labels", {}) or {}).get("alertname", "?")
    logger.info(
        "[firefighter] select alert=%s -> action=%s confidence=%.2f model=%s ms=%d",
        alertname, result.get("action_name") or "(abstain)",
        float(result.get("confidence", 0.0) or 0.0), result.get("model", ""),
        int(result.get("ms", 0) or 0),
    )

    return SelectResponse(
        action_name=result.get("action_name", "") or "",
        params=result.get("params", {}) or {},
        confidence=float(result.get("confidence", 0.0) or 0.0),
        reason=result.get("reason", "") or "",
        model=result.get("model", "") or "",
        ms=int(result.get("ms", 0) or 0),
    )
