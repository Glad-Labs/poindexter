"""Carried candidates record where they came from, and how much they were
handicapped getting there (poindexter#1036 follow-up).

`carried_from_batch_id` and `decay_factor` were columns on both candidate
tables that NOTHING ever wrote — 0 of 1,077 rows had provenance and every
`decay_factor` was 1.0. So a carried candidate was indistinguishable from a
freshly-discovered one, and "why does this topic keep reappearing?" had no
answer in the data.

Writing the real `decay_factor` also fixes compounding. `_load_carry_forward`
computes the next multiplier as `stored_decay * decay`. With the stored value
pinned at 1.0 that was always `1.0 * 0.7` — a flat 30% handicap no matter how
many rounds a candidate had survived, so a stale topic could never actually
fade. Persisting it gives 0.7 -> 0.49 -> 0.343, which is the `factor^n`
behaviour `_load_carry_forward`'s own docstring describes.
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

from services.topic_batch_service import _carried_origin
from services.topic_ranking import ScoredCandidate


@pytest.mark.unit
class TestOriginDetection:
    def test_a_fresh_pool_candidate_has_no_origin(self) -> None:
        """Fresh items arrive as {"kind":..., "data":...} — the ABSENCE of a
        row is what marks them new."""
        assert _carried_origin({"kind": "external", "data": {"title": "x"}}, {"title": "x"}) is None

    def test_a_carry_forward_records_its_batch(self) -> None:
        row = {"batch_id": "batch-1", "title": "x"}
        assert _carried_origin({"row": row, "decay_factor": 0.7}, row) == "batch-1"

    def test_the_original_batch_survives_repeated_carries(self) -> None:
        """An in-place refresh re-reads the same open batch, so recording the
        immediate source would point at the batch the row is already in — true
        but useless. The first batch answers "how long has this been going
        round?"."""
        row = {"batch_id": "batch-9", "carried_from_batch_id": "batch-1"}
        assert _carried_origin({"row": row, "decay_factor": 0.49}, row) == "batch-1"

    def test_a_non_dict_row_is_tolerated(self) -> None:
        """Internal candidates can arrive as a dataclass rather than a row."""
        class _Obj:
            distilled_topic = "x"
        assert _carried_origin({"row": _Obj(), "decay_factor": 0.7}, _Obj()) is None


@pytest.mark.unit
class TestScoredCandidateCarriesProvenance:
    def test_defaults_are_fresh(self) -> None:
        c = ScoredCandidate(id="i", title="t", summary=None, embedding_score=1.0)
        assert c.carried_from_batch_id is None
        assert c.decay_factor == 1.0


@pytest.mark.unit
class TestBothTablesPersistIt:
    """A field nothing writes is exactly the bug being fixed, so assert the
    INSERTs actually carry it."""

    def test_write_batch_persists_provenance_for_both_tables(self) -> None:
        from services.topic_batch_service import TopicBatchService

        body = textwrap.dedent(inspect.getsource(TopicBatchService._write_batch))
        assert body.count("carried_from_batch_id") >= 2, "both INSERTs must name the column"
        assert body.count("c.carried_from_batch_id") >= 2, "and bind it"
        # The hardcoded 1.0 that made decay non-compounding must be gone.
        assert "c.decay_factor" in body
        insert_region = body.split("INSERT INTO", 1)[1]
        assert "\n                            1.0,\n" not in insert_region
