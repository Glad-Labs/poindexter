"""Unit tests for ``TopicBatchService._apply_source_diversity_cap`` (finding #5).

Pure-function tests — no DB, no async. The cap guarantees external/consumer
topic sources representation in a discovery batch so internal_rag (the
system-introspection corpus) can't take every slot, as it did on the
2026-06-19 validation run (4/5 candidates were internal-meta topics like
"Operator Surface Unreachability").
"""

from __future__ import annotations

from types import SimpleNamespace

from services.topic_batch_service import TopicBatchService

_cap = TopicBatchService._apply_source_diversity_cap


def _c(cid: str, score: float):
    """A ScoredCandidate stand-in — the cap only reads ``.id`` + ``.llm_score``."""
    return SimpleNamespace(id=cid, llm_score=score)


def test_caps_internal_share_when_external_available():
    # 4 high-scoring internal + 3 lower external. batch_size=5, cap=0.5 →
    # at most floor(5*0.5)=2 internal; the other 3 slots go to external.
    internal = [_c(f"i{i}", 90 - i) for i in range(4)]
    external = [_c(f"e{i}", 70 - i) for i in range(3)]
    internal_ids = {c.id for c in internal}
    picked = _cap(internal + external, internal_ids, 5, 0.5)
    assert len(picked) == 5
    assert sum(1 for c in picked if c.id in internal_ids) == 2
    assert {c.id for c in picked if c.id not in internal_ids} == {"e0", "e1", "e2"}


def test_backfills_internal_when_external_too_thin():
    # Only 1 external; the batch must still fill to 5 → backfill the remaining
    # slots with internal by score rather than ship a short batch.
    internal = [_c(f"i{i}", 90 - i) for i in range(6)]
    external = [_c("e0", 50)]
    internal_ids = {c.id for c in internal}
    picked = _cap(internal + external, internal_ids, 5, 0.5)
    assert len(picked) == 5
    assert "e0" in {c.id for c in picked}


def test_all_internal_batch_is_unchanged_top_n_by_score():
    # Existing single-source (internal-only) batches must be identical to the
    # old plain top-N-by-score slice — backfill restores the full set in order.
    internal = [_c(f"i{i}", 90 - i) for i in range(5)]
    picked = _cap(internal, {c.id for c in internal}, 3, 0.5)
    assert [c.id for c in picked] == ["i0", "i1", "i2"]


def test_cap_disabled_at_one_point_zero():
    internal = [_c(f"i{i}", 90 - i) for i in range(5)]
    external = [_c("e0", 10)]
    picked = _cap(internal + external, {c.id for c in internal}, 5, 1.0)
    # Pure top-5 by score → all 5 internal (external out-scored, cap off).
    assert [c.id for c in picked] == ["i0", "i1", "i2", "i3", "i4"]


def test_preserves_score_order_within_selection():
    internal = [_c("i0", 100)]
    external = [_c("e0", 90), _c("e1", 80)]
    picked = _cap(internal + external, {"i0"}, 3, 0.5)
    # batch_size=3 → max_internal=1; all three fit, ordered by score.
    assert [c.id for c in picked] == ["i0", "e0", "e1"]


def test_empty_input_returns_empty():
    assert _cap([], set(), 5, 0.5) == []
