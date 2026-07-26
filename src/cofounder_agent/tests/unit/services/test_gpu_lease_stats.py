"""services/gpu_lease_stats.py — pure fold math + best-effort DB seam
(poindexter#914 P0, plan Task A2)."""

from __future__ import annotations

import json
import random

from services.gpu_lease_stats import (
    LeaseStats,
    fold_sample,
    read_stats,
    record_release,
)


class TestFoldSample:
    def test_first_sample_seeds_everything(self):
        s = fold_sample(LeaseStats(), 1000.0)
        assert s.samples == 1
        assert s.ewma_ms == 1000.0
        assert s.p50_ms == 1000.0
        assert s.p90_ms == 1000.0
        assert "warmup" in s.q_state

    def test_warmup_quantiles_are_exact(self):
        s = LeaseStats()
        for x in (100.0, 200.0, 300.0, 400.0):
            s = fold_sample(s, x)
        assert s.samples == 4
        # Exact over the warm-up window: sorted [100,200,300,400].
        assert s.p50_ms in (200.0, 300.0)  # nearest-rank midpoint
        assert s.p90_ms == 400.0

    def test_fifth_sample_initializes_p2(self):
        s = LeaseStats()
        for x in (100.0, 200.0, 300.0, 400.0, 500.0):
            s = fold_sample(s, x)
        assert set(s.q_state) == {"p50", "p90"}
        assert s.p50_ms == 300.0  # exact median of 5 sorted samples

    def test_ewma_converges_toward_shifted_mean(self):
        s = LeaseStats()
        for _ in range(50):
            s = fold_sample(s, 100.0)
        assert abs(s.ewma_ms - 100.0) < 1.0
        for _ in range(50):
            s = fold_sample(s, 900.0)
        assert s.ewma_ms > 800.0  # α=0.2 converges well within 50 samples

    def test_constant_input_is_monotone_safe(self):
        s = LeaseStats()
        for _ in range(200):
            s = fold_sample(s, 250.0)
        assert s.p50_ms == 250.0
        assert s.p90_ms == 250.0

    def test_p2_tracks_a_known_distribution(self):
        # Uniform(0, 1000): true p50=500, p90=900. Seeded RNG = deterministic.
        rng = random.Random(914)
        s = LeaseStats()
        for _ in range(2000):
            s = fold_sample(s, rng.uniform(0.0, 1000.0))
        assert abs(s.p50_ms - 500.0) < 50.0
        assert abs(s.p90_ms - 900.0) < 50.0

    def test_fold_state_round_trips_through_json(self):
        # q_state must survive JSONB persistence: fold → serialize →
        # deserialize → keep folding, and stay coherent.
        rng = random.Random(7)
        s = LeaseStats()
        for _ in range(20):
            s = fold_sample(s, rng.uniform(100.0, 200.0))
        revived = LeaseStats(
            samples=s.samples,
            ewma_ms=s.ewma_ms,
            p50_ms=s.p50_ms,
            p90_ms=s.p90_ms,
            q_state=json.loads(json.dumps(s.q_state)),
        )
        cont = fold_sample(revived, 150.0)
        assert cont.samples == 21
        assert 100.0 <= cont.p50_ms <= 200.0

    def test_pure_no_input_mutation(self):
        s1 = fold_sample(LeaseStats(), 10.0)
        frozen = json.dumps(s1.q_state)
        fold_sample(s1, 999.0)
        assert json.dumps(s1.q_state) == frozen


class TestDbSeam:
    async def test_record_release_folds_not_overwrites(self, monkeypatch):
        """The upsert must write fold_sample(prev, x), proving the read-fold-
        write cycle — not a blind overwrite of the row."""
        import services.gpu_lease_stats as m

        prev = fold_sample(LeaseStats(), 100.0)
        executed: list[tuple] = []

        class _Conn:
            async def fetchrow(self, _sql, owner, phase):
                return {
                    "samples": prev.samples,
                    "ewma_ms": prev.ewma_ms,
                    "p50_ms": prev.p50_ms,
                    "p90_ms": prev.p90_ms,
                    "q_state": json.dumps(prev.q_state),
                }

            async def execute(self, sql, *args):
                executed.append((sql, args))

            async def close(self):
                return None

        async def _fake_connect():
            return _Conn()

        monkeypatch.setattr(m, "_connect", _fake_connect)
        await record_release("ollama", "writer", 300.0)

        assert len(executed) == 1
        _sql, args = executed[0]
        # args: owner, phase, samples, ewma, p50, p90, q_state
        assert args[0] == "ollama" and args[1] == "writer"
        expected = fold_sample(prev, 300.0)
        assert args[2] == expected.samples == 2
        assert abs(args[3] - expected.ewma_ms) < 1e-9

    async def test_record_release_swallows_all_failures(self, monkeypatch):
        import services.gpu_lease_stats as m

        async def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(m, "_connect", _boom)
        await record_release("ollama", "x", 100.0)  # must not raise

    async def test_read_stats_returns_none_when_unavailable(self, monkeypatch):
        import services.gpu_lease_stats as m

        async def _none():
            return None

        monkeypatch.setattr(m, "_connect", _none)
        assert await read_stats("ollama", "x") is None

    async def test_read_stats_maps_row(self, monkeypatch):
        import services.gpu_lease_stats as m

        class _Conn:
            async def fetchrow(self, _sql, owner, phase):
                return {
                    "samples": 7,
                    "ewma_ms": 123.0,
                    "p50_ms": 100.0,
                    "p90_ms": 200.0,
                    "q_state": json.dumps({"warmup": [1.0]}),
                }

            async def close(self):
                return None

        async def _fake_connect():
            return _Conn()

        monkeypatch.setattr(m, "_connect", _fake_connect)
        out = await read_stats("video", "")
        assert out is not None
        assert out.samples == 7 and out.p90_ms == 200.0
        assert out.q_state == {"warmup": [1.0]}
