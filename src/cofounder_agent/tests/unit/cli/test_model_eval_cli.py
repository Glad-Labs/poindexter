"""Click CLI tests for ``poindexter model-eval`` (Plan 1, Task 8).

Mirrors test_experiments_cli.py: CliRunner + ``patch.dict(sys.modules, ...)``
so the lazy ``import asyncpg`` never reaches a real DB, and the service
orchestrator (``run_reranker_bakeoff``) is patched so no model/Langfuse is hit.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.model_eval import model_eval_group
from services.model_eval.promotion import PromotionProposal
from services.model_eval.runner import EvalReport
from services.model_eval.types import MetricResult

_REPORT = EvalReport(
    slot="rag_rerank_model",
    metric_name="ndcg@10",
    champion="champ",
    champion_score=0.80,
    best_challenger="chall",
    best_challenger_score=0.86,
    winner="chall",
    margin=0.075,
    beats_margin=True,
    results=[MetricResult("rag_rerank_model", "champ", "ndcg@10", 0.80, 50, 1)],
)
_PROPOSAL = PromotionProposal(
    slot="rag_rerank_model",
    from_model="champ",
    to_model="chall",
    metric_name="ndcg@10",
    metric_delta=0.06,
    margin=0.075,
    kind="pr",
    body="## Model promotion\n`champ` -> `chall`\n",
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _build_pool() -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def fake_asyncpg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://t:t@localhost/t")
    mod = MagicMock()
    mod.create_pool = AsyncMock(return_value=_build_pool())
    with patch.dict(sys.modules, {"asyncpg": mod}):
        yield mod


def test_run_prints_summary_and_proposal(runner, fake_asyncpg) -> None:
    with patch(
        "services.model_eval.bakeoff.run_reranker_bakeoff",
        new=AsyncMock(return_value=(_REPORT, _PROPOSAL)),
    ):
        res = runner.invoke(model_eval_group, ["run", "--challenger", "chall"])
    assert res.exit_code == 0, res.output
    assert "chall" in res.output
    assert "winner" in res.output.lower()
    assert "pr" in res.output.lower()


def test_run_json_output(runner, fake_asyncpg) -> None:
    with patch(
        "services.model_eval.bakeoff.run_reranker_bakeoff",
        new=AsyncMock(return_value=(_REPORT, _PROPOSAL)),
    ):
        res = runner.invoke(model_eval_group, ["run", "--challenger", "chall", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["winner"] == "chall"
    assert data["proposal_kind"] == "pr"


def test_run_no_promotion_message(runner, fake_asyncpg) -> None:
    hold = EvalReport(
        slot="rag_rerank_model",
        metric_name="ndcg@10",
        champion="champ",
        champion_score=0.80,
        best_challenger="chall",
        best_challenger_score=0.805,
        winner="champ",
        margin=0.006,
        beats_margin=False,
        results=[MetricResult("rag_rerank_model", "champ", "ndcg@10", 0.80, 50, 1)],
    )
    with patch(
        "services.model_eval.bakeoff.run_reranker_bakeoff",
        new=AsyncMock(return_value=(hold, None)),
    ):
        res = runner.invoke(model_eval_group, ["run", "--challenger", "chall"])
    assert res.exit_code == 0, res.output
    assert "no promotion" in res.output.lower()


def test_run_requires_a_challenger(runner, fake_asyncpg) -> None:
    res = runner.invoke(model_eval_group, ["run"])
    assert res.exit_code != 0  # --challenger is required
