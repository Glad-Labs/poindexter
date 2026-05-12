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

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# ``set_site_config()`` for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


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


def _resolve_goal_descriptions() -> dict[str, str]:
    """Return goal-type → prose mapping, preferring the
    ``niche_goal_descriptions`` app_setting (JSON blob) over the in-code
    default. Falls back silently to ``GOAL_DESCRIPTIONS`` if the setting
    is missing, empty, or malformed — keeps test fixtures and bare
    installs working without the migration applied.
    """
    try:
        raw = site_config.get("niche_goal_descriptions", "")
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


async def _embed_text_cached(text: str) -> list[float]:
    """Embed via the registered ollama_native provider.

    The embedding service is responsible for its own caching (or not).
    This indirection exists so tests can monkeypatch.

    NB: The plan spec referenced ``from services.embedding_service import
    embed_text`` but no such module-level helper exists — embeddings go
    through the ``LLMProvider`` Protocol (matches the pattern in
    ``services.publish_service`` etc.).
    """
    from plugins.registry import get_all_llm_providers
    providers = {p.name: p for p in get_all_llm_providers()}
    provider = providers.get("ollama_native")
    if provider is None:
        raise RuntimeError(
            "ollama_native provider not registered — cannot generate embeddings"
        )
    embed_model = site_config.get("niche_embedding_model", "nomic-embed-text")
    return await provider.embed(text, model=embed_model)


async def embed_text(text: str) -> list[float]:
    """Public embedding helper. Writer modes and the batch service import
    this rather than reaching into ``_embed_text_cached`` directly.
    """
    return await _embed_text_cached(text)


async def goal_vector_for(goal_type: str) -> list[float]:
    if goal_type in _GOAL_VEC_CACHE:
        return _GOAL_VEC_CACHE[goal_type]
    descriptions = _resolve_goal_descriptions()
    if goal_type not in descriptions:
        raise ValueError(f"unknown goal_type: {goal_type!r}")
    vec = await _embed_text_cached(descriptions[goal_type])
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
async def _ollama_chat_json(prompt: str, *, model: str) -> str:
    """One-shot Ollama chat call; returns the assistant's content as a string.
    Indirection so tests can monkeypatch.

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
    import httpx
    base_url = (
        site_config.get("local_llm_api_url", "http://localhost:11434").rstrip("/")
    )
    timeout = site_config.get_float(
        "niche_ollama_chat_timeout_seconds", 60.0,
    )
    messages = [{"role": "user", "content": prompt}]
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
    }
    langfuse_context.update_current_observation(
        model=model,
        input=messages,
        metadata={"base_url": base_url, "timeout": timeout, "format": "json"},
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
) -> dict[str, ScoredCandidate]:
    """Single LLM call ranks the (already-shortlisted) candidates against weighted goals.

    Returns the same candidates with `llm_score` and `score_breakdown` filled in,
    keyed by candidate id.

    ``model`` defaults to the operator-tuned ``pipeline_writer_model``
    app_setting (already used by the rest of the content pipeline). The
    ``ollama/`` prefix some tenants use is stripped to mirror the
    behaviour in ``ai_content_generator.py``. Falls back to the prior
    hardcoded ``glm-4.7-5090:latest`` if no setting is configured so
    test fixtures that don't seed site_config keep working.
    """
    if model is None:
        model = (
            site_config.get("pipeline_writer_model", "glm-4.7-5090:latest")
            or "glm-4.7-5090:latest"
        ).removeprefix("ollama/")
    descriptions = _resolve_goal_descriptions()
    weights_descr = "\n".join(f"- {g.goal_type} (weight {g.weight_pct}%): {descriptions[g.goal_type]}" for g in weights)
    cand_block = "\n".join(f"[{c.id}] {c.title} — {c.summary or ''}" for c in candidates)
    prompt = get_prompt_manager().get_prompt(
        "topic.ranking",
        weights_descr=weights_descr,
        cand_block=cand_block,
    )
    raw = await _ollama_chat_json(prompt, model=model)
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
