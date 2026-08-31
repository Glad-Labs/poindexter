"""Discovery continues while promotion is throttled (poindexter#1036).

An open batch used to end the sweep outright, which meant a paused pipeline
stopped discovering: the taps kept filling `topic_pool` but nothing drained it,
so the backlog aged into the pool TTL and was deleted. The halt on *promotion*
is deliberate — it is what stops the operator being buried — but it should not
also halt *discovery*.

An untouched open batch is now refreshed in place. These tests pin the
judgements that keep that safe:

1. a batch a human is judging is never redrawn under them,
2. refreshing reuses the same batch row, so the one-open-batch-per-niche
   invariant that `get_open_batch` / `topic_auto_resolve` / the operator CLI
   all assume still holds,
3. incumbents come from the batch being refreshed, not from the last resolved
   batch — otherwise a refresh would discard everything that batch had found.
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

from services.topic_batch_service import TopicBatchService


def _src(fn) -> str:
    return textwrap.dedent(inspect.getsource(fn))


def _refresh_branch(fn) -> str:
    """Just the `if replace_batch_id is not None:` arm.

    Scoped to the branch rather than a fixed character window: a window wide
    enough to contain both DELETEs also swallows the `else:` arm, so a
    "refresh must not INSERT" assertion would fire on the new-batch path and
    fail for the wrong reason.
    """
    body = _src(fn)
    after = body.split("replace_batch_id is not None", 1)[1]
    return after.split("else:", 1)[0]


@pytest.mark.unit
class TestOperatorWorkIsNeverRedrawn:
    def test_a_ranked_open_batch_stops_the_sweep(self) -> None:
        body = _src(TopicBatchService.run_sweep)
        assert "_operator_has_ranked" in body
        assert "return None" in body.split("_operator_has_ranked", 1)[1][:400]

    def test_edits_count_as_operator_investment_not_just_ranks(self) -> None:
        body = _src(TopicBatchService._operator_has_ranked)
        assert "operator_rank IS NOT NULL" in body
        assert "operator_edited_topic" in body
        assert "operator_edited_angle" in body

    def test_both_candidate_tables_are_checked_for_operator_touch(self) -> None:
        body = _src(TopicBatchService._operator_has_ranked)
        assert body.count("topic_candidates") >= 2


@pytest.mark.unit
class TestRefreshReusesTheBatchRow:
    def test_refresh_updates_rather_than_inserting_a_second_batch(self) -> None:
        assert "replace_batch_id" in _src(TopicBatchService._write_batch)
        head = _refresh_branch(TopicBatchService._write_batch)
        assert "UPDATE topic_batches" in head
        assert "INSERT INTO topic_batches" not in head

    def test_candidates_are_replaced_wholesale(self) -> None:
        head = _refresh_branch(TopicBatchService._write_batch)
        assert "DELETE FROM topic_candidates" in head
        assert "DELETE FROM internal_topic_candidates" in head

    def test_expiry_is_pushed_out_on_refresh(self) -> None:
        assert "expires_at" in _refresh_branch(TopicBatchService._write_batch)


@pytest.mark.unit
class TestIncumbentsSurviveTheRefresh:
    def test_carry_forward_sources_from_the_batch_being_refreshed(self) -> None:
        body = _src(TopicBatchService._load_carry_forward)
        assert "from_batch_id" in body
        head = body.split("from_batch_id is not None", 1)[1][:700]
        assert "WHERE batch_id = $1" in head
        assert "picked_candidate_id" not in head

    def test_decay_is_applied_to_refreshed_incumbents(self) -> None:
        body = _src(TopicBatchService._load_carry_forward)
        head = body.split("from_batch_id is not None", 1)[1][:700]
        assert "decay_factor" in head and "* decay" in head


@pytest.mark.unit
class TestRefreshIsNotNoisy:
    def test_only_a_new_batch_notifies_the_operator(self) -> None:
        body = _src(TopicBatchService.run_sweep)
        idx = body.find("_open_topic_decision_gate")
        assert idx > 0
        assert "refresh_of is None" in body[max(0, idx - 400):idx]
