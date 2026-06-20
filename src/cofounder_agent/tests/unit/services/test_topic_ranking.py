"""Tests for topic_ranking — goal vectors + weighted cosine scoring."""

from unittest.mock import MagicMock

import pytest

from services.niche_service import NicheGoal
from services.site_config import SiteConfig
from services.topic_ranking import (
    GOAL_DESCRIPTIONS,
    goal_vector_for,
    weighted_cosine_score,
)

# No module-level asyncio mark: ``asyncio_mode = "auto"`` (pyproject.toml)
# already auto-marks coroutine tests. An explicit mark wrongly tagged the
# sync tests here, emitting a PytestWarning (Glad-Labs/glad-labs-stack#997).


# #272 Phase-2b: topic_ranking's public helpers take a keyword-required
# ``site_config``. Tests pass this empty instance — the embed-model /
# goal-descriptions reads fall back to their defaults on it.
_SC = SiteConfig()


async def test_all_goal_types_have_descriptions():
    expected = {"TRAFFIC","EDUCATION","BRAND","AUTHORITY","REVENUE","COMMUNITY","NICHE_DEPTH"}
    assert set(GOAL_DESCRIPTIONS.keys()) == expected
    for desc in GOAL_DESCRIPTIONS.values():
        assert isinstance(desc, str) and len(desc) > 20


async def test_goal_vector_caches_embeddings(monkeypatch):
    calls = []
    async def fake_embed(text, *, site_config=None):
        calls.append(text)
        return [0.1] * 768
    monkeypatch.setattr("services.topic_ranking._embed_text_cached", fake_embed)
    v1 = await goal_vector_for("TRAFFIC", site_config=_SC)
    v2 = await goal_vector_for("TRAFFIC", site_config=_SC)
    assert v1 == v2
    # _embed_text_cached itself caches, so calls should be 1
    assert len(calls) == 1


async def test_weighted_cosine_score_combines_per_goal_signals():
    candidate_vec = [1.0, 0.0, 0.0]
    # Two goals; one aligns perfectly with candidate, the other is orthogonal.
    goal_vecs = {"TRAFFIC": [1.0, 0.0, 0.0], "EDUCATION": [0.0, 1.0, 0.0]}
    weights = [NicheGoal("TRAFFIC", 60), NicheGoal("EDUCATION", 40)]
    score, breakdown = weighted_cosine_score(candidate_vec, goal_vecs, weights)
    # 0.6 * 1.0 (perfect TRAFFIC) + 0.4 * 0.0 (orthogonal EDUCATION) = 0.6
    assert score == pytest.approx(0.6, abs=0.01)
    assert breakdown == {"TRAFFIC": pytest.approx(0.6, abs=0.01),
                         "EDUCATION": pytest.approx(0.0, abs=0.01)}


async def test_llm_final_score_returns_score_per_candidate(monkeypatch):
    from services.topic_ranking import ScoredCandidate, llm_final_score

    async def fake_ollama_chat(prompt: str, *, model: str, pool=None, site_config=None) -> str:
        # Simulated JSON response from glm-4.7-5090
        return '{"c1": {"score": 87.5, "breakdown": {"TRAFFIC": 0.5, "EDUCATION": 0.375}},'  \
               ' "c2": {"score": 42.0, "breakdown": {"TRAFFIC": 0.2, "EDUCATION": 0.22}}}'
    monkeypatch.setattr("services.topic_ranking._ollama_chat_json", fake_ollama_chat)

    candidates = [
        ScoredCandidate(id="c1", title="A", summary="x", embedding_score=0.6),
        ScoredCandidate(id="c2", title="B", summary="y", embedding_score=0.4),
    ]
    weights = [NicheGoal("TRAFFIC", 60), NicheGoal("EDUCATION", 40)]
    # Pass model= explicitly so the test doesn't go through
    # resolve_local_model (poindexter#485 fail-loud sweep) — that
    # path is exercised in test_llm_text.py.
    scored = await llm_final_score(candidates, weights, model="glm-4.7:latest", site_config=_SC)
    assert scored["c1"].llm_score == 87.5
    assert scored["c2"].llm_score == 42.0


def test_apply_decay_multiplies_score():
    from services.topic_ranking import apply_decay
    assert apply_decay(score=80, decay_factor=1.0) == 80
    assert apply_decay(score=80, decay_factor=0.7) == pytest.approx(56)
    assert apply_decay(score=80, decay_factor=0.49) == pytest.approx(39.2)


# ---------------------------------------------------------------------------
# cosine_similarity — pure helper, all guard branches
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical_vectors_returns_one():
    from services.topic_ranking import cosine_similarity
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_returns_zero():
    from services.topic_ranking import cosine_similarity
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_mismatched_lengths_returns_zero():
    """Length mismatch is the first guard — must short-circuit to 0.0
    rather than raise. Without this branch, callers that cross provider
    boundaries (different embedding models with different dimensions)
    would 500 instead of degrading gracefully."""
    from services.topic_ranking import cosine_similarity
    assert cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_similarity_zero_vector_returns_zero():
    """Both 'a is zero' and 'b is zero' branches — division-by-zero
    guard. A zero embedding can come from a provider that failed
    silently or an empty-string embed; we must not propagate NaN."""
    from services.topic_ranking import cosine_similarity
    assert cosine_similarity([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == 0.0
    assert cosine_similarity([1.0, 2.0, 3.0], [0.0, 0.0, 0.0]) == 0.0


def test_cosine_similarity_anti_aligned_returns_negative_one():
    from services.topic_ranking import cosine_similarity
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# goal_vector_for — error + cache miss paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_vector_for_unknown_goal_type_raises(monkeypatch):
    """Goals come from operator-set niche_goals rows; if a stale row
    references a retired goal_type the caller must see ValueError, not
    silently embed an arbitrary string."""
    from services import topic_ranking
    # Clear cache so a prior test's 'TRAFFIC' fill doesn't accidentally
    # short-circuit before the goal_type check.
    monkeypatch.setattr(topic_ranking, "_GOAL_VEC_CACHE", {})

    async def fake_embed(text, *, site_config=None):  # pragma: no cover — must not be called
        raise AssertionError("embed should not run for an unknown goal_type")
    monkeypatch.setattr(topic_ranking, "_embed_text_cached", fake_embed)

    with pytest.raises(ValueError, match="unknown goal_type"):
        await topic_ranking.goal_vector_for("NOT_A_REAL_GOAL", site_config=_SC)


# ---------------------------------------------------------------------------
# weighted_cosine_score — sparse goal_vecs + empty weights
# ---------------------------------------------------------------------------


def test_weighted_cosine_score_skips_goals_missing_from_vec_map():
    """If a goal weight references a goal_type whose vector failed to
    embed (None in goal_vecs), it must be skipped — not crash, not
    contribute. Otherwise an embedding-provider hiccup nukes the whole
    rerank pass."""
    from services.topic_ranking import weighted_cosine_score
    candidate = [1.0, 0.0]
    goal_vecs = {"TRAFFIC": [1.0, 0.0]}  # EDUCATION absent
    weights = [NicheGoal("TRAFFIC", 60), NicheGoal("EDUCATION", 40)]
    score, breakdown = weighted_cosine_score(candidate, goal_vecs, weights)
    # Only TRAFFIC contributes (1.0 * 0.6); EDUCATION skipped silently.
    assert score == pytest.approx(0.6, abs=0.01)
    assert "EDUCATION" not in breakdown
    assert breakdown["TRAFFIC"] == pytest.approx(0.6, abs=0.01)


def test_weighted_cosine_score_empty_weights_returns_zero():
    from services.topic_ranking import weighted_cosine_score
    score, breakdown = weighted_cosine_score([1.0, 0.0], {"TRAFFIC": [1.0, 0.0]}, [])
    assert score == 0.0
    assert breakdown == {}


# ---------------------------------------------------------------------------
# llm_final_score — fallback when LLM omits a candidate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_final_score_falls_back_when_llm_omits_candidate(monkeypatch):
    """When the LLM scorer's JSON skips a candidate (truncated output,
    hallucinated keys), we must NOT drop it — it gets backfilled with
    embedding_score * 100. Verifies the warn-and-recover branch."""
    from services.topic_ranking import ScoredCandidate, llm_final_score

    async def fake_ollama_chat(prompt: str, *, model: str, pool=None, site_config=None) -> str:
        # 'present' is scored; 'missing' is omitted entirely.
        return '{"present": {"score": 91.0, "breakdown": {"TRAFFIC": 0.91}}}'
    monkeypatch.setattr("services.topic_ranking._ollama_chat_json", fake_ollama_chat)

    candidates = [
        ScoredCandidate(id="present", title="A", summary="x", embedding_score=0.5),
        ScoredCandidate(id="missing", title="B", summary="y", embedding_score=0.42),
    ]
    weights = [NicheGoal("TRAFFIC", 100)]
    # Same pattern as above: bypass resolve_local_model for the
    # tests that aren't exercising the model-resolution contract.
    scored = await llm_final_score(candidates, weights, model="glm-4.7:latest", site_config=_SC)

    assert scored["present"].llm_score == 91.0
    # Backfilled: embedding_score (0.42) * 100 = 42.0; breakdown is {}
    assert scored["missing"].llm_score == pytest.approx(42.0)
    assert scored["missing"].score_breakdown == {}


# ---------------------------------------------------------------------------
# llm_final_score — fail-loud on missing model config (poindexter#485)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_final_score_raises_value_error_when_no_model_configured(monkeypatch):
    """poindexter#485 fail-loud sweep contract.

    Previously this path silently fell back to the literal
    ``"glm-4.7-5090:latest"`` — Matt's specific custom Ollama model
    that forks installing Poindexter wouldn't have. The fallback
    masked misconfiguration as an opaque "model not found" at LLM
    call time. Now it raises ``ValueError`` at resolution time.
    """
    from services.topic_ranking import ScoredCandidate, llm_final_score

    # Clean SiteConfig with no model setting and no cost_tier fallback —
    # mirrors a misconfigured fork. The resolver must NOT silently
    # use a hardcoded model. #272 Phase-2b: pass the stub explicitly via
    # the now-required ``site_config=`` kwarg (no module global to patch).
    stub_sc = MagicMock()
    stub_sc.get = MagicMock(return_value="")

    candidates = [ScoredCandidate(id="c1", title="A", summary="x", embedding_score=0.6)]
    weights = [NicheGoal("TRAFFIC", 100)]

    # 2026-05-28 stall fix: ranking now resolves via
    # ``resolve_structured_model`` (structured_extraction_model →
    # cost_tier.standard.model) instead of the writer-model chain, so a
    # misconfigured fork fails loud here with the structured-model message.
    with pytest.raises(ValueError, match="no structured-extraction model resolvable"):
        # Note: no model= kwarg → forces resolution
        await llm_final_score(candidates, weights, site_config=stub_sc)


# ---------------------------------------------------------------------------
# apply_decay — boundaries
# ---------------------------------------------------------------------------


def test_apply_decay_zero_factor_zeroes_score():
    from services.topic_ranking import apply_decay
    assert apply_decay(score=80, decay_factor=0.0) == 0.0
    # decay_factor > 1 (theoretically a re-promotion) still multiplies
    assert apply_decay(score=50, decay_factor=1.2) == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# import hygiene — the operator topic chain must stay PyYAML-free
# ---------------------------------------------------------------------------


def test_topic_chain_imports_without_pyyaml():
    """Importing ``services.topic_batch_service`` must not require PyYAML.

    The MCP server runs in a lightweight venv and imports this chain for the
    operator topic tools (``topics_show_batch`` / ``rank`` / ``edit`` /
    ``resolve`` / ``reject``) — all pure-DB paths. ``prompt_manager`` does
    ``import yaml`` and is only needed by ``llm_final_score`` (the discovery
    sweep), so its import is lazy. A top-level re-import would resurface the
    ``ModuleNotFoundError: No module named 'yaml'`` that broke the topic MCP
    tools. Run in a subprocess with ``yaml`` blocked to mimic that venv.
    """
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    cofounder_root = Path(__file__).resolve().parents[3]
    probe = textwrap.dedent(
        """
        import builtins
        import sys

        _real_import = builtins.__import__

        def _no_yaml(name, *args, **kwargs):
            if name == "yaml" or name.startswith("yaml."):
                raise ModuleNotFoundError("No module named 'yaml'")
            return _real_import(name, *args, **kwargs)

        builtins.__import__ = _no_yaml

        # Must succeed with PyYAML unavailable.
        import services.topic_batch_service  # noqa: F401

        # And prompt_manager must not have been pulled in transitively.
        assert "services.prompt_manager" not in sys.modules, (
            "prompt_manager (PyYAML-bearing) must stay a lazy import "
            "in topic_ranking.llm_final_score"
        )
        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        # Inherit the parent env (Windows needs SYSTEMROOT etc. for the stdlib)
        # and add the cofounder root so ``services.*`` is importable.
        env={**os.environ, "PYTHONPATH": str(cofounder_root)},
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    assert "OK" in proc.stdout
