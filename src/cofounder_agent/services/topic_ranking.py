"""Topic candidate ranking — goal vectors + hybrid embed/LLM scoring + decay rerank.

Spec §"Goal vectors", §"Discovery sweep" steps 4-5.
"""

from __future__ import annotations

import json
import math

from services.logger_config import get_logger
from services.niche_service import NicheGoal
from services.prompt_manager import get_prompt_manager
from services.site_config import SiteConfig

logger = get_logger(__name__)


# Per the spec — fixed prose anchor for each goal type. Cached embeddings
# are computed lazily on first use and live for the process lifetime.
#
# These are the IN-CODE FALLBACK defaults. Operators can override the
# anchor prose per-deployment by setting the ``niche_goal_descriptions``
# app_setting to a JSON object keyed by goal_type — see migration 0119.
# Resolution happens lazily inside ``_resolve_goal_descriptions`` so a
# DB reload picks up changes without a restart.
GOAL_DESCRIPTIONS: dict[str, str] = {
    "TRAFFIC":     "Topic likely to attract organic search traffic; trending keyword, broad appeal, evergreen demand.",
    "EDUCATION":   "Topic that teaches the reader something concrete and useful they didn't know before.",
    "BRAND":       "Topic that reinforces the operator's positioning and unique perspective.",
    "AUTHORITY":   "Topic that demonstrates the operator's depth and expertise on something specific.",
    "REVENUE":     "Topic that drives a commercial outcome: signups, sales, conversions, paid feature awareness.",
    "COMMUNITY":   "Topic that resonates with the operator's existing audience; sparks discussion, shares, replies.",
    "NICHE_DEPTH": "Topic that goes deep on the operator's niche specialty rather than broad-audience content.",
}


def _resolve_goal_descriptions(*, site_config: SiteConfig) -> dict[str, str]:
    """Return goal-type → prose mapping, preferring the
    ``niche_goal_descriptions`` app_setting (JSON blob) over the in-code
    default. Falls back silently to ``GOAL_DESCRIPTIONS`` if the setting
    is missing, empty, or malformed — keeps test fixtures and bare
    installs working without the migration applied.

    DI (#272 Phase-2b): ``site_config`` is keyword-required — the public
    callers (``goal_vector_for`` / ``llm_final_score``) thread the
    injected instance.
    """
    _sc = site_config
    try:
        raw = _sc.get("niche_goal_descriptions", "")
        if not raw:
            return GOAL_DESCRIPTIONS
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed:
            # Merge over defaults so a partial override still resolves
            # the keys the operator didn't set explicitly.
            merged = dict(GOAL_DESCRIPTIONS)
            merged.update({str(k): str(v) for k, v in parsed.items()})
            return merged
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("[NICHE] niche_goal_descriptions resolve failed: %s", exc)
    return GOAL_DESCRIPTIONS


_GOAL_VEC_CACHE: dict[str, list[float]] = {}


async def _embed_text_cached(text: str, *, site_config: SiteConfig) -> list[float]:
    """Embed via the registered ollama_native provider.

    The embedding service is responsible for its own caching (or not).
    This indirection exists so tests can monkeypatch.

    NB: The plan spec referenced ``from services.embedding_service import
    embed_text`` but no such module-level helper exists — embeddings go
    through the ``LLMProvider`` Protocol (matches the pattern in
    ``services.publish_service`` etc.).

    DI (#272 Phase-2b): ``site_config`` is keyword-required. The
    embed-model read drives only the provider call, not the
    ``_GOAL_VEC_CACHE`` key (which is keyed on goal_type prose), so
    threading the param is cache-safe.
    """
    _sc = site_config
    from plugins.registry import get_all_llm_providers
    providers = {p.name: p for p in get_all_llm_providers()}
    provider = providers.get("ollama_native")
    if provider is None:
        raise RuntimeError(
            "ollama_native provider not registered — cannot generate embeddings"
        )
    embed_model = _sc.get("niche_embedding_model", "nomic-embed-text")
    return await provider.embed(text, model=embed_model)


async def embed_text(text: str, *, site_config: SiteConfig) -> list[float]:
    """Public embedding helper. Writer modes and the batch service import
    this rather than reaching into ``_embed_text_cached`` directly.

    DI (#272 Phase-2b): ``site_config`` is keyword-required and threaded
    straight down to the reader.
    """
    return await _embed_text_cached(text, site_config=site_config)


async def goal_vector_for(goal_type: str, *, site_config: SiteConfig) -> list[float]:
    if goal_type in _GOAL_VEC_CACHE:
        return _GOAL_VEC_CACHE[goal_type]
    descriptions = _resolve_goal_descriptions(site_config=site_config)
    if goal_type not in descriptions:
        raise ValueError(f"unknown goal_type: {goal_type!r}")
    vec = await _embed_text_cached(descriptions[goal_type], site_config=site_config)
    _GOAL_VEC_CACHE[goal_type] = vec
    return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def weighted_cosine_score(
    candidate_vec: list[float],
    goal_vecs: dict[str, list[float]],
    weights: list[NicheGoal],
) -> tuple[float, dict[str, float]]:
    """Returns (overall_score, per_goal_breakdown).

    overall_score = sum over goals of (cosine(candidate, goal_vec) * weight_pct/100).
    breakdown maps goal_type -> its weighted contribution.
    """
    breakdown: dict[str, float] = {}
    total = 0.0
    for g in weights:
        gv = goal_vecs.get(g.goal_type)
        if gv is None:
            continue
        cos = cosine_similarity(candidate_vec, gv)
        contribution = cos * (g.weight_pct / 100.0)
        breakdown[g.goal_type] = contribution
        total += contribution
    return total, breakdown


from dataclasses import dataclass


@dataclass
class ScoredCandidate:
    id: str
    title: str
    summary: str | None
    embedding_score: float
    llm_score: float | None = None
    score_breakdown: dict[str, float] | None = None


from services.langfuse_shim import langfuse_context, observe


@observe(as_type="generation", name="topic_ranking._ollama_chat_json")
async def _ollama_chat_json(
    prompt: str, *, model: str, pool: object | None = None,
    site_config: SiteConfig,
) -> str:
    """One-shot LLM chat call returning the assistant's content as a string.

    Routes through :func:`services.llm_providers.dispatcher.dispatch_complete`
    when a ``pool`` is reachable (production / worker path — picks up
    the configured provider per ``plugin.llm_provider.primary.<tier>``).
    Falls back to a direct ``httpx`` POST to local Ollama's ``/api/chat``
    when no pool is available (tests / bootstrap).

    Indirection so tests can monkeypatch this helper directly. The
    keyword-only ``pool`` arg is auto-discovered from
    ``site_config._pool`` when not supplied, so existing call sites
    (``llm_final_score``, ``story_spine``, ``ai_content_generator``,
    ``internal_rag_source``) get the dispatcher routing for free
    without threading a new arg through every caller.

    2026-05-28 (finish-PR-#4 sweep): retires the last direct-httpx
    ``/api/chat`` survivor in ``services/``. The legacy payload used
    ``"format": "json"`` to force structured-JSON output from Ollama;
    the dispatcher path now achieves the same thing via
    ``response_format={"type": "json_object"}``, which LiteLLM maps to
    Ollama's ``format=json`` automatically. The direct-httpx fallback
    below preserves the original wire shape for the bootstrap path.

    The base URL is resolved from ``local_llm_api_url`` (the existing
    Ollama base-URL app_setting, seeded by migration 0116) and the
    request timeout from ``niche_ollama_chat_timeout_seconds`` (migration
    0119). Both fall back to the prior hardcoded values if app_settings
    isn't loaded so unit-test fixtures that don't seed site_config
    keep working.

    Langfuse trace: every call lands a ``generation`` span. The
    writer_rag_modes (story_spine outline, two_pass revise) and the
    LLM-judge fallback path in topic_ranking.llm_final_score all route
    through here, so wrapping at this layer covers all three call sites
    at once.
    """
    # DI (#272 Phase-2b): ``site_config`` is keyword-required — every
    # caller (``llm_final_score``, ``ai_content_generator``,
    # ``internal_rag_source``) threads its own wired instance.
    _sc = site_config

    messages = [{"role": "user", "content": prompt}]
    timeout = _sc.get_float(
        "niche_ollama_chat_timeout_seconds", 60.0,
    )

    # Auto-discover the pool via the same DI seam ai_content_generator
    # uses (``site_config._pool``). Lets every existing caller pick up
    # dispatcher routing without a signature change; tests + bootstrap
    # paths that don't wire ``_pool`` keep the httpx fallback.
    resolved_pool = pool if pool is not None else getattr(_sc, "_pool", None)

    if resolved_pool is not None:
        from services.llm_providers.dispatcher import dispatch_complete

        langfuse_context.update_current_observation(
            model=model,
            input=messages,
            metadata={"via": "dispatcher", "timeout": timeout, "format": "json"},
        )
        completion = await dispatch_complete(
            pool=resolved_pool,
            messages=messages,
            model=model,
            tier="standard",
            timeout_s=int(timeout),
            # LiteLLM maps this to Ollama's ``format=json`` automatically;
            # other backends (OpenAI / Anthropic / etc.) honor the OpenAI
            # response-format convention natively.
            response_format={"type": "json_object"},
        )
        content = getattr(completion, "text", "") or ""
        langfuse_context.update_current_observation(
            output=content,
            usage={
                "input": int(getattr(completion, "prompt_tokens", 0) or 0),
                "output": int(getattr(completion, "completion_tokens", 0) or 0),
                "unit": "TOKENS",
            },
        )
        return content

    # Test / bootstrap fallback — direct httpx → local Ollama. Same
    # behavior as before the dispatcher cutover. Used when there's no
    # DB pool reachable (unit tests stub site_config but not a full
    # asyncpg pool; bootstrap scripts run before any pool is established).
    import httpx
    base_url = (
        _sc.get("local_llm_api_url", "http://localhost:11434").rstrip("/")
    )
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
    }
    langfuse_context.update_current_observation(
        model=model,
        input=messages,
        metadata={"via": "httpx", "base_url": base_url, "timeout": timeout, "format": "json"},
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data["message"]["content"]
    langfuse_context.update_current_observation(
        output=content,
        usage={
            "input": data.get("prompt_eval_count"),
            "output": data.get("eval_count"),
            "unit": "TOKENS",
        },
    )
    return content


async def llm_final_score(
    candidates: list[ScoredCandidate],
    weights: list[NicheGoal],
    *,
    model: str | None = None,
    site_config: SiteConfig,
) -> dict[str, ScoredCandidate]:
    """Single LLM call ranks the (already-shortlisted) candidates against weighted goals.

    Returns the same candidates with `llm_score` and `score_breakdown` filled in,
    keyed by candidate id.

    ``model`` defaults to the operator-tuned ``pipeline_writer_model``
    app_setting (already used by the rest of the content pipeline). The
    ``ollama/`` prefix some tenants use is stripped inside
    :func:`services.llm_text.resolve_local_model`.

    poindexter#485 fail-loud sweep: previously this baked Matt's
    ``glm-4.7-5090:latest`` model name in as a Python-side fallback,
    which silently masked misconfiguration on forks that don't have
    that model loaded in Ollama. Now resolves via the shared chain
    (``pipeline_writer_model`` → ``cost_tier.standard.model``) and
    raises ``ValueError`` if neither is set — surfaces misconfig at
    pipeline-entry instead of as an opaque Ollama 404 mid-call.
    """
    # DI (#272 Phase-2b): ``site_config`` is keyword-required — threaded
    # down into resolve_local_model, the goal-descriptions reader, and
    # the LLM-chat helper so every downstream read honors the injected
    # instance.
    _sc = site_config
    if model is None:
        # Structured-JSON ranking call: resolve a JSON-reliable model
        # (DB-configurable ``structured_extraction_model``), NOT the writer
        # model — a reasoning writer model returns empty ``content`` under
        # ``response_format=json_object`` (2026-05-28 content-gen stall).
        from services.llm_text import resolve_structured_model
        model = resolve_structured_model(site_config=_sc)
    descriptions = _resolve_goal_descriptions(site_config=_sc)
    weights_descr = "\n".join(f"- {g.goal_type} (weight {g.weight_pct}%): {descriptions[g.goal_type]}" for g in weights)
    cand_block = "\n".join(f"[{c.id}] {c.title} — {c.summary or ''}" for c in candidates)
    prompt = get_prompt_manager().get_prompt(
        "topic.ranking",
        weights_descr=weights_descr,
        cand_block=cand_block,
    )
    raw = await _ollama_chat_json(prompt, model=model, site_config=_sc)
    if not raw or not raw.strip():
        # Empty LLM response would explode json.loads("") with the opaque
        # "Expecting value: line 1 column 1" — fail loud with context
        # instead. Caller (run_sweep) degrades the sweep gracefully.
        raise ValueError(
            f"topic_ranking.llm_final_score: empty response from model "
            f"{model!r} (response_format=json_object). A reasoning model "
            f"may be configured for structured extraction — set "
            f"``structured_extraction_model`` to a JSON-reliable instruct model."
        )
    parsed = json.loads(raw)
    result: dict[str, ScoredCandidate] = {}
    for c in candidates:
        score_blob = parsed.get(c.id)
        if score_blob is None:
            logger.warning("LLM scorer omitted candidate %s; defaulting to embedding_score", c.id)
            score_blob = {"score": c.embedding_score * 100, "breakdown": {}}
        c.llm_score = float(score_blob.get("score", 0.0))
        c.score_breakdown = dict(score_blob.get("breakdown", {}))
        result[c.id] = c
    return result


def apply_decay(*, score: float, decay_factor: float) -> float:
    """Effective score = raw score × decay_factor. Used both at insertion time
    (decay_factor=1.0 for fresh, <1.0 for carried-forward) and at re-rank time
    (carried-forward candidates get an additional decay multiplier applied here).
    """
    return score * decay_factor
