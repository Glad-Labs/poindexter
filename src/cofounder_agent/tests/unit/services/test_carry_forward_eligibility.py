"""Carry-forward eligibility is "was not picked", never "was not ranked".

The original `_load_carry_forward` filter was `operator_rank IS NULL`, which
encoded the MANUAL flow's assumption: an operator ranks only the candidates
they care about, so unranked meant unwanted-but-reusable.

`topic_auto_resolve` then arrived and set `operator_rank = rank_in_batch` on
EVERY candidate in a batch so it could resolve without an operator. That
silently made every auto-resolved batch's losers permanently ineligible.

Measured on prod 2026-08-30, before the fix:

    topic_candidates           624 rows, 0 ever carried forward
    internal_topic_candidates  453 rows, 0 ever carried forward
    decay_factor               1.0 on all 1,077 rows — never decayed
    newest resolved batch      0 of 5 candidates eligible

So the sweep was discarding four good candidates to keep one, every time,
while a whole carry-forward-with-decay mechanism sat inert as dead columns.

These are source-level assertions rather than query-execution tests on
purpose: the regression is a *predicate* change in SQL, and the thing worth
preventing is someone reinstating `operator_rank` as the eligibility signal.
The behaviour itself was verified against the live database (0 -> 4 eligible,
decay applied) and that number is recorded in the PR.
"""

from __future__ import annotations

import inspect
import re

import pytest

from services.topic_batch_service import TopicBatchService


@pytest.fixture(scope="module")
def carry_forward_src() -> str:
    """Executable body only.

    The docstring deliberately QUOTES the old `operator_rank IS NULL` filter
    to explain the regression, so asserting over the raw source would match
    the explanation rather than the code — a test that passes for the wrong
    reason, or fails for one.
    """
    import ast
    import textwrap

    raw = textwrap.dedent(inspect.getsource(TopicBatchService._load_carry_forward))
    fn_node = ast.parse(raw).body[0]
    first = fn_node.body[0]
    # A string replace on __doc__ does NOT work here: __doc__ is
    # indentation-normalised while the source is not, so it never matches.
    is_doc = isinstance(first, ast.Expr) and isinstance(
        getattr(first, "value", None), ast.Constant
    ) and isinstance(first.value.value, str)
    if not is_doc:
        return raw
    lines = raw.splitlines()
    return "\n".join(lines[first.end_lineno:])


@pytest.mark.unit
class TestEligibilityPredicate:
    def test_operator_rank_is_not_the_eligibility_signal(self, carry_forward_src) -> None:
        """The exact regression: auto-resolve ranks everything, so gating on
        operator_rank disables carry-forward entirely."""
        assert "operator_rank IS NULL" not in carry_forward_src

    def test_the_picked_candidate_is_what_is_excluded(self, carry_forward_src) -> None:
        assert "picked_candidate_id" in carry_forward_src

    def test_both_candidate_tables_are_carried(self, carry_forward_src) -> None:
        """Internal candidates were equally affected — 453 rows, 0 carried."""
        assert "topic_candidates" in carry_forward_src
        assert "internal_topic_candidates" in carry_forward_src

    def test_kind_is_checked_when_excluding_the_winner(self, carry_forward_src) -> None:
        """external and internal ids live in different tables, so excluding on
        id alone could drop the wrong row when ids collide across them."""
        assert "picked_candidate_kind" in carry_forward_src
        # internal must be matched explicitly; external tolerates a NULL kind
        # because older rows predate the column.
        assert "'internal'" in carry_forward_src
        assert "COALESCE" in carry_forward_src

    def test_decay_is_still_applied_to_carried_rows(self, carry_forward_src) -> None:
        """Carrying without decay would let a stale candidate win forever —
        the point is that old topics compete at a discount, not that they
        never lose."""
        assert "niche_carry_forward_decay_factor" in carry_forward_src
        assert re.search(r"decay_factor\"\]\)\s*\*\s*decay", carry_forward_src)
