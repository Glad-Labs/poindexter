"""Unit tests for weighted variant selection (#361 part 1).

Pins the new opt-in weighted-selection branch added to
``experiment_runner.pick_variant`` and the ``_weighted_choice`` helper:

- Flag OFF (default) -> uniform random (Phase-1 behaviour unchanged).
- Flag ON -> allocation proportional to ``experiment_variants.weight``.
- ``_weighted_choice`` math: a high-weight variant wins far more often;
  all-equal / all-zero weights fall back to uniform.

No real DB — a tiny asyncpg-shaped stub returns the seeded variant rows
from ``fetch`` and the flag value from ``fetchval``.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

import pytest

from services import experiment_runner
from services.experiment_runner import _weighted_choice, pick_variant


class _Row(dict):
    """Asyncpg Record-shape: dict subscripted by column name."""


class _FakeConn:
    def __init__(self, rows: list[_Row], flag_value: Any) -> None:
        self._rows = rows
        self._flag_value = flag_value

    async def fetch(self, sql: str, *args: Any) -> list[_Row]:
        return list(self._rows)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        # The only fetchval pick_variant issues is the weighted-selection flag.
        return self._flag_value

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _FakePool:
    def __init__(self, rows: list[_Row], flag_value: Any) -> None:
        self._conn = _FakeConn(rows, flag_value)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


def _variant_row(label: str, weight: float) -> _Row:
    exp_id = str(uuid.uuid4())
    return _Row(
        experiment_id=exp_id,
        experiment_key="exp/weighting-test",
        variant_id=str(uuid.uuid4()),
        variant_label=label,
        prompt_template_key=None,
        prompt_template_version=None,
        writer_model=None,
        rag_config={},
        weight=weight,
    )


# ---------------------------------------------------------------------------
# _weighted_choice helper math
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWeightedChoice:
    def test_high_weight_dominates(self):
        rows = [_variant_row("A", 0.95), _variant_row("B", 0.05)]
        picks = Counter(_weighted_choice(rows)["variant_label"] for _ in range(2000))
        # A is 19x heavier; it must win the strong majority.
        assert picks["A"] > picks["B"]
        assert picks["A"] > 1500

    def test_all_equal_falls_back_to_uniform(self):
        rows = [_variant_row("A", 0.5), _variant_row("B", 0.5)]
        # Both labels appear over many draws (no starvation / crash).
        picks = Counter(_weighted_choice(rows)["variant_label"] for _ in range(500))
        assert picks["A"] > 0 and picks["B"] > 0

    def test_all_zero_falls_back_to_uniform(self):
        rows = [_variant_row("A", 0.0), _variant_row("B", 0.0)]
        picks = Counter(_weighted_choice(rows)["variant_label"] for _ in range(500))
        assert picks["A"] > 0 and picks["B"] > 0


# ---------------------------------------------------------------------------
# pick_variant gating
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPickVariantWeightingGate:
    async def test_flag_off_uses_uniform(self, monkeypatch):
        rows = [_variant_row("A", 0.99), _variant_row("B", 0.01)]
        pool = _FakePool(rows, flag_value="false")

        called = {"weighted": False, "uniform": False}
        real_uniform = experiment_runner.random.choice

        def spy_choice(seq):
            called["uniform"] = True
            return real_uniform(seq)

        def spy_weighted(seq):
            called["weighted"] = True
            return seq[0]

        monkeypatch.setattr(experiment_runner.random, "choice", spy_choice)
        monkeypatch.setattr(experiment_runner, "_weighted_choice", spy_weighted)

        variant = await pick_variant(pool, "test-niche", "task-1")
        assert variant is not None
        assert called["uniform"] is True
        assert called["weighted"] is False

    async def test_flag_on_uses_weighted(self, monkeypatch):
        rows = [_variant_row("A", 0.99), _variant_row("B", 0.01)]
        pool = _FakePool(rows, flag_value="true")

        called = {"weighted": False}

        def spy_weighted(seq):
            called["weighted"] = True
            return seq[0]

        monkeypatch.setattr(experiment_runner, "_weighted_choice", spy_weighted)

        variant = await pick_variant(pool, "test-niche", "task-1")
        assert variant is not None
        assert called["weighted"] is True

    async def test_flag_on_respects_weights_end_to_end(self):
        # No monkeypatch — real weighted path. The 0.97-weight variant
        # should be assigned the strong majority of the time.
        rows = [_variant_row("heavy", 0.97), _variant_row("light", 0.03)]
        pool = _FakePool(rows, flag_value="true")
        labels = Counter()
        for i in range(800):
            v = await pick_variant(pool, "test-niche", f"task-{i}")
            assert v is not None
            labels[v.variant_label] += 1
        assert labels["heavy"] > labels["light"]
        assert labels["heavy"] > 600
