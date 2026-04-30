"""Topic candidate ranking — goal vectors + hybrid embed/LLM scoring + decay rerank.

Spec §"Goal vectors", §"Discovery sweep" steps 4-5.
"""

from __future__ import annotations

import math

from services.logger_config import get_logger
from services.niche_service import NicheGoal

logger = get_logger(__name__)


# Per the spec — fixed prose anchor for each goal type. Cached embeddings
# are computed lazily on first use and live for the process lifetime.
GOAL_DESCRIPTIONS: dict[str, str] = {
    "TRAFFIC":     "Topic likely to attract organic search traffic; trending keyword, broad appeal, evergreen demand.",
    "EDUCATION":   "Topic that teaches the reader something concrete and useful they didn't know before.",
    "BRAND":       "Topic that reinforces the operator's positioning and unique perspective.",
    "AUTHORITY":   "Topic that demonstrates the operator's depth and expertise on something specific.",
    "REVENUE":     "Topic that drives a commercial outcome: signups, sales, conversions, paid feature awareness.",
    "COMMUNITY":   "Topic that resonates with the operator's existing audience; sparks discussion, shares, replies.",
    "NICHE_DEPTH": "Topic that goes deep on the operator's niche specialty rather than broad-audience content.",
}


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
    from plugins.registry import get_llm_providers

    providers = {p.name: p for p in get_llm_providers()}
    provider = providers.get("ollama_native")
    if provider is None:
        raise RuntimeError(
            "ollama_native provider not registered — cannot generate embeddings"
        )
    return await provider.embed(text, model="nomic-embed-text")


async def goal_vector_for(goal_type: str) -> list[float]:
    if goal_type in _GOAL_VEC_CACHE:
        return _GOAL_VEC_CACHE[goal_type]
    if goal_type not in GOAL_DESCRIPTIONS:
        raise ValueError(f"unknown goal_type: {goal_type!r}")
    vec = await _embed_text_cached(GOAL_DESCRIPTIONS[goal_type])
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
