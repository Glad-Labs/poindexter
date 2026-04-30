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


async def embed_text(text: str) -> list[float]:
    """Public embedding helper. Writer modes and the batch service import
    this rather than reaching into ``_embed_text_cached`` directly.
    """
    return await _embed_text_cached(text)


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


import json
from dataclasses import dataclass


@dataclass
class ScoredCandidate:
    id: str
    title: str
    summary: str | None
    embedding_score: float
    llm_score: float | None = None
    score_breakdown: dict[str, float] | None = None


async def _ollama_chat_json(prompt: str, *, model: str) -> str:
    """One-shot Ollama chat call; returns the assistant's content as a string.
    Indirection so tests can monkeypatch.
    """
    import httpx
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post("http://localhost:11434/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["message"]["content"]


async def llm_final_score(
    candidates: list[ScoredCandidate],
    weights: list[NicheGoal],
    *,
    model: str = "glm-4.7-5090:latest",
) -> dict[str, ScoredCandidate]:
    """Single LLM call ranks the (already-shortlisted) candidates against weighted goals.

    Returns the same candidates with `llm_score` and `score_breakdown` filled in,
    keyed by candidate id.
    """
    weights_descr = "\n".join(f"- {g.goal_type} (weight {g.weight_pct}%): {GOAL_DESCRIPTIONS[g.goal_type]}" for g in weights)
    cand_block = "\n".join(f"[{c.id}] {c.title} — {c.summary or ''}" for c in candidates)
    prompt = f"""You are scoring topic candidates for a content pipeline against the operator's weighted goals.

Goals (weight in pct):
{weights_descr}

Candidates:
{cand_block}

Return STRICT JSON keyed by candidate id, of the form:
{{"<id>": {{"score": <0-100>, "breakdown": {{"<GOAL_TYPE>": <weighted contribution 0-1>, ...}}}}, ...}}

The breakdown values per candidate should approximately sum to (score / 100).
Return ONLY the JSON, no commentary.
"""
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
