"""Unit tests for ``services.qa_gates_db_writer``.

The writer is the missing half of the qa_gates telemetry contract —
``qa_gates_db.py`` (read) was always there, this file (write) was
discovered to be missing on 2026-05-09 when every gate showed
``last_run_at = NEVER``. These tests pin the contract so the gap can't
silently reappear.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.qa_gates_db_writer import _REVIEWER_TO_GATE, record_chain_run


class _Review:
    """Minimal duck-type for ReviewerResult."""

    def __init__(self, reviewer: str, approved: bool = True, advisory: bool = False):
        self.reviewer = reviewer
        self.approved = approved
        self.advisory = advisory


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def transaction(self):
        return self

    async def execute(self, query, *args):
        self._pool.executes.append((query, args))
        return "UPDATE 1"


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    def acquire(self):
        return _FakeConn(self)


@pytest.mark.asyncio
async def test_pool_none_no_ops():
    """Match the read-side fallback shape: pool=None must not raise."""
    await record_chain_run(None, [_Review("programmatic_validator")])


@pytest.mark.asyncio
async def test_empty_reviews_no_ops():
    pool = _FakePool()
    await record_chain_run(pool, [])
    assert pool.executes == []


@pytest.mark.asyncio
async def test_unknown_reviewer_skipped():
    """Inline reviewers without a qa_gates row must NOT trigger an UPDATE
    — there's no row to bump.

    ``rendered_preview`` is the finalize-time preview reviewer (minted via
    the preview_token AFTER the qa.* chain), so it genuinely has no
    qa_gates row. NB: citation_verifier / topic_delivery USED to belong
    here, but they were given gate rows on 2026-06-03 (#659/#658) and now
    bump counters — see test_restored_rail_gates_bump_their_counters."""
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("rendered_preview"),
        _Review("some_reviewer_with_no_gate_row"),
    ])
    assert pool.executes == []


@pytest.mark.asyncio
async def test_known_reviewer_bumps_counter():
    pool = _FakePool()
    await record_chain_run(pool, [_Review("programmatic_validator", approved=True)])
    assert len(pool.executes) == 1
    query, args = pool.executes[0]
    assert "UPDATE qa_gates" in query
    assert "total_runs = total_runs + 1" in query
    assert args == ("programmatic_validator", "passed", 0)


@pytest.mark.asyncio
async def test_rejected_review_increments_rejections():
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("programmatic_validator", approved=False),
    ])
    _, args = pool.executes[0]
    assert args == ("programmatic_validator", "rejected", 1)


@pytest.mark.asyncio
async def test_alias_mapping_image_relevance_to_vision_gate():
    """The inline reviewer name 'image_relevance' must update the
    qa_gates row named 'vision_gate'."""
    pool = _FakePool()
    await record_chain_run(pool, [_Review("image_relevance", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "vision_gate"


@pytest.mark.asyncio
async def test_alias_mapping_internal_consistency_to_consistency():
    pool = _FakePool()
    await record_chain_run(pool, [_Review("internal_consistency", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "consistency"


@pytest.mark.asyncio
async def test_alias_mapping_ollama_critic_to_llm_critic():
    pool = _FakePool()
    await record_chain_run(pool, [_Review("ollama_critic", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "llm_critic"


@pytest.mark.asyncio
async def test_duplicate_reviewer_collapses_to_one_update():
    """url_verifier appends a ReviewerResult on both the dead-link and
    the bonus path. The writer must collapse those into a single
    UPDATE so total_runs doesn't double-count one execution."""
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("url_verifier", approved=True),
        _Review("url_verifier", approved=True),
    ])
    assert len(pool.executes) == 1


@pytest.mark.asyncio
async def test_full_chain_writes_one_update_per_gate():
    """End-to-end: a typical chain emits 4-7 reviews; each maps to one
    gate row UPDATE."""
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("programmatic_validator", approved=True),
        _Review("ollama_critic", approved=True),
        _Review("internal_consistency", approved=True),
        _Review("web_factcheck", approved=True),
        _Review("url_verifier", approved=True),
        # citation_verifier + topic_delivery now HAVE gate rows (seeded
        # #659/#658 on 2026-06-03) so they bump too:
        _Review("citation_verifier", approved=True),
        _Review("topic_delivery", approved=True),
        # rendered_preview is the only genuinely rowless reviewer here:
        _Review("rendered_preview", approved=True),
    ])
    bumped_gates = {args[0] for _, args in pool.executes}
    assert bumped_gates == {
        "programmatic_validator",
        "llm_critic",
        "consistency",
        "web_factcheck",
        "url_verifier",
        "citation_verifier",
        "topic_delivery",
    }


def test_alias_table_covers_every_known_inline_reviewer():
    """Documentation-as-test: when a new inline reviewer ships, this
    test forces the implementer to either add it to the alias table
    (if it has a qa_gates row) or to the explicit allow-list of
    reviewers that intentionally lack a row.

    2026-05-27: the assertion list was bare ("programmatic_validator",
    "ollama_critic") which let the deepeval/guardrails/ragas reviewers
    fall through without forcing a writer update. Every known inline
    reviewer that ships a qa_gates row is now pinned here so a future
    reviewer can't silently regress to `total_runs=0`.
    """
    inline_reviewers_with_row = set(_REVIEWER_TO_GATE)
    inline_reviewers_without_row = {
        # Finalize-time preview reviewer — minted via the preview_token
        # AFTER the qa.* chain, so it has no qa_gates row by design.
        # (citation_verifier / topic_delivery were moved OUT of this set on
        # 2026-06-11: they were given gate rows on 2026-06-03 (#659/#658),
        # so they MUST be aliased — leaving them here is what let the
        # third alias-drop recurrence pass CI.)
        "rendered_preview",
    }
    documented = inline_reviewers_with_row | inline_reviewers_without_row
    # If you trip this assertion, either:
    #   (a) add the reviewer name + gate name to _REVIEWER_TO_GATE, OR
    #   (b) add the name to inline_reviewers_without_row above.
    must_be_documented = {
        # Hardcoded gates seeded in 0000_baseline + the qa_gates seed
        # migrations. Every one of these emits a ReviewerResult; if
        # the gate row is to track total_runs accurately the writer
        # MUST know about the alias.
        "programmatic_validator",
        "ollama_critic",
        "internal_consistency",
        "image_relevance",
        "web_factcheck",
        "url_verifier",
        # Lane D #329 OSS rails — migrations 20260510_022034,
        # 20260510_030530, 20260510_032959. The reviewers ship in
        # multi_model_qa.py; missing entries here = the gates ran but
        # the operator dashboard showed last_run_at=NEVER. Discovered
        # 2026-05-27.
        "deepeval_brand_fabrication",
        "deepeval_g_eval",
        "deepeval_faithfulness",
        "guardrails_brand",
        "guardrails_competitor",
        "ragas_eval",
        # Restoration rails — qa_gates rows seeded #659/#658 (2026-06-03)
        # and #621 (2026-06-07). They emit ReviewerResults on the live
        # graph_def path; missing entries here = the gates ran but the
        # dashboard showed last_run_at=NEVER (third recurrence, 2026-06-11).
        "citation_verifier",
        "topic_delivery",
        "self_consistency",
    }
    missing = must_be_documented - documented
    assert not missing, (
        f"qa_gates_db_writer._REVIEWER_TO_GATE is missing aliases for "
        f"{sorted(missing)!r}. Either add them to _REVIEWER_TO_GATE so "
        f"record_chain_run() bumps the gate counters, or add them to "
        f"inline_reviewers_without_row above if they intentionally "
        f"have no qa_gates row."
    )


@pytest.mark.asyncio
async def test_new_oss_rails_bump_their_gate_counters():
    """Regression test for the 2026-05-27 silent-skip discovery: the
    deepeval/guardrails/ragas reviewers were producing ReviewerResults
    on every QA pass but record_chain_run was silently dropping them
    because their names weren't in _REVIEWER_TO_GATE. Pin the wiring
    so the bug can't reappear."""
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("deepeval_g_eval", approved=True, advisory=True),
        _Review("deepeval_faithfulness", approved=True, advisory=True),
        _Review("deepeval_brand_fabrication", approved=True, advisory=True),
        _Review("guardrails_brand", approved=True, advisory=True),
        _Review("guardrails_competitor", approved=True, advisory=True),
        _Review("ragas_eval", approved=True, advisory=True),
    ])
    bumped_gates = {args[0] for _, args in pool.executes}
    assert bumped_gates == {
        "deepeval_g_eval",
        "deepeval_faithfulness",
        "deepeval_brand_fabrication",
        "guardrails_brand",
        "guardrails_competitor",
        "ragas_eval",
    }


@pytest.mark.asyncio
async def test_restored_rail_gates_bump_their_counters():
    """Regression for the 2026-06-11 alias-drop recurrence (the THIRD).

    The citation_verifier / topic_delivery / self_consistency rails were
    restored/added as qa.* atoms (#659 / #658 / #621) and seeded their own
    qa_gates rows, but their reviewer names were never added to
    _REVIEWER_TO_GATE — so record_chain_run silently dropped the counter
    and `poindexter qa-gates list` showed total_runs=0 while audit_log
    proved 97 / 49 / 24 real runs. Pin the wiring so it can't regress."""
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("citation_verifier", approved=True, advisory=True),
        _Review("topic_delivery", approved=True, advisory=True),
        _Review("self_consistency", approved=True, advisory=False),
    ])
    bumped_gates = {args[0] for _, args in pool.executes}
    assert bumped_gates == {
        "citation_verifier",
        "topic_delivery",
        "self_consistency",
    }


@pytest.mark.asyncio
async def test_accepts_dict_shaped_reviews():
    """qa.aggregate (the graph_def QA path since #355) carries the rail
    reviews as ``reviewer_to_dict()`` dicts on the ``qa_rail_reviews``
    channel, NOT as ``ReviewerResult`` objects. The writer must read its
    fields from dicts as well as attributes — otherwise ``getattr`` returns
    the default for every dict, no gate matches, and ``total_runs`` stays
    frozen at 0 on the prod path (poindexter#553). Pin both shapes so a
    future serializer change can't silently re-break the counter."""
    pool = _FakePool()
    await record_chain_run(pool, [
        {"reviewer": "ollama_critic", "approved": True, "advisory": False,
         "score": 90.0, "provider": "ollama"},
        {"reviewer": "ragas_eval", "approved": False, "advisory": True,
         "score": 40.0, "provider": "ollama"},
    ])
    bumped = {args[0]: tuple(args[1:]) for _, args in pool.executes}
    # ollama_critic aliases to the llm_critic gate row.
    assert bumped["llm_critic"] == ("passed", 0)
    # A failing rail (even advisory) records a rejection on its own counter.
    assert bumped["ragas_eval"] == ("rejected", 1)
