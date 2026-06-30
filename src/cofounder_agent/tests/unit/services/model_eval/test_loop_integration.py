"""End-to-end loop integration (Plan 1, Task 9).

Drives the whole vertical slice through run_reranker_bakeoff: golden-set build
(fake pool) -> RerankerScorer (fake marker encoder) -> runner -> comparison ->
promotion. Offline + deterministic; no DB, no langfuse, no model download.
"""

from __future__ import annotations

from services.model_eval.bakeoff import run_reranker_bakeoff
from services.model_eval.harness import InMemoryEvalHarness
from services.model_eval.scorers.reranker import RerankerScorer
from services.site_config import SiteConfig

# Distinct, non-substring markers so a query marker matches exactly one doc.
_MARKERS = ["alpha", "bravo", "charlie", "delta"]


class _FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetch(self, *_a, **_k):  # type: ignore[no-untyped-def]
        return self._rows


class _FakePool:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def acquire(self):  # type: ignore[no-untyped-def]
        rows = self._rows

        class _Acq:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return _FakeConn(rows)

            async def __aexit__(self, *_a):  # type: ignore[no-untyped-def]
                return False

        return _Acq()


def _posts() -> list[dict]:
    # Each post's content starts with its own marker (== its title/query), so a
    # "strong" reranker that matches the query marker ranks the relevant doc first.
    return [
        {"id": f"p{i}", "title": m, "content": f"{m} " + ("word " * 200)}
        for i, m in enumerate(_MARKERS)
    ]


class _MarkerEncoder:
    """Strong: score 1 when the doc contains the query marker. Weak: inverted."""

    def __init__(self, *, strong: bool) -> None:
        self._strong = strong

    def predict(self, pairs):  # type: ignore[no-untyped-def]
        out = []
        for query, doc in pairs:
            match = query in doc
            out.append((1.0 if match else 0.0) if self._strong else (0.0 if match else 1.0))
        return out


def _cfg() -> SiteConfig:
    return SiteConfig(
        initial_config={
            "rag_rerank_model": "champ",
            "rag_rerank_device": "cpu",
            "model_eval_promotion_margin": "0.02",
            "model_eval_reranker_golden_size": "4",
            "model_eval_reranker_candidates_per_case": "3",
        }
    )


async def test_full_loop_promotes_stronger_challenger() -> None:
    def factory(name, device):  # type: ignore[no-untyped-def]
        return _MarkerEncoder(strong=(name == "chall"))

    harness = InMemoryEvalHarness()
    report, proposal = await run_reranker_bakeoff(
        pool=_FakePool(_posts()),
        site_config=_cfg(),
        challengers=["chall"],
        harness=harness,
        scorer=RerankerScorer(encoder_factory=factory),
        run_name="itest",
    )

    assert report.champion == "champ"
    assert report.winner == "chall"
    assert report.beats_margin is True

    assert proposal is not None
    assert proposal.kind == "pr"  # no auto_promote opt-in -> PR
    assert proposal.to_model == "chall"

    latest = harness.latest_by_model("rag_rerank_model", "ndcg@10")
    assert set(latest) == {"champ", "chall"}
    assert latest["chall"] > latest["champ"]


async def test_full_loop_holds_champion_on_tie() -> None:
    def factory(name, device):  # type: ignore[no-untyped-def]
        return _MarkerEncoder(strong=True)  # both models identical -> tie

    report, proposal = await run_reranker_bakeoff(
        pool=_FakePool(_posts()),
        site_config=_cfg(),
        challengers=["chall"],
        harness=InMemoryEvalHarness(),
        scorer=RerankerScorer(encoder_factory=factory),
        run_name="itest-tie",
    )

    assert report.winner == "champ"
    assert report.beats_margin is False
    assert proposal is None
