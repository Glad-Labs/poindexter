"""Tests for the reranker golden-set bootstrap (Plan 1, Task 5).

A fake asyncpg-style pool feeds controlled posts so the test is offline and
deterministic. No real DB / asyncpg required.
"""

from __future__ import annotations

import pytest

from services.model_eval.golden_sets.reranker import build_reranker_golden_set
from services.site_config import SiteConfig


class _FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetch(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
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


def _posts(n: int) -> list[dict]:
    return [
        {"id": f"p{i}", "title": f"Title {i}", "content": ("word " * 200) + f" unique-token-{i}"}
        for i in range(n)
    ]


def _sc(size: int, per_case: int) -> SiteConfig:
    return SiteConfig(
        initial_config={
            "model_eval_reranker_golden_size": str(size),
            "model_eval_reranker_candidates_per_case": str(per_case),
        }
    )


async def test_one_relevant_doc_per_case() -> None:
    gs = await build_reranker_golden_set(pool=_FakePool(_posts(6)), site_config=_sc(3, 4))
    assert len(gs.cases) == 3
    for case in gs.cases:
        assert len(case.candidates) == 4
        relevant = [c for c in case.candidates if c["relevance"] == 1]
        assert len(relevant) == 1


async def test_version_is_deterministic_for_same_posts() -> None:
    pool = _FakePool(_posts(6))
    gs1 = await build_reranker_golden_set(pool=pool, site_config=_sc(3, 4))
    gs2 = await build_reranker_golden_set(pool=pool, site_config=_sc(3, 4))
    assert gs1.version == gs2.version
    # same version -> same distractor sampling -> identical cases
    assert [c.query for c in gs1.cases] == [c.query for c in gs2.cases]


async def test_caps_cases_at_available_posts() -> None:
    # size=50 requested but only 5 posts -> 5 cases.
    gs = await build_reranker_golden_set(pool=_FakePool(_posts(5)), site_config=_sc(50, 4))
    assert len(gs.cases) == 5


async def test_fails_loud_when_too_few_posts() -> None:
    with pytest.raises(RuntimeError, match="published posts"):
        await build_reranker_golden_set(pool=_FakePool(_posts(3)), site_config=_sc(3, 4))
