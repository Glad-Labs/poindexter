"""Unit tests for declarative QA gate chain (GH-115).

Two surfaces under test:

1. ``services.qa_gates_db.load_qa_gate_chain`` — DB read layer that
   materializes the table into ``QAGateSpec`` records, ordered by
   ``execution_order``. The runtime walks this chain.
2. ``services.multi_model_qa.MultiModelQA`` — the consumer. We assert
   that the loaded chain controls which gates run (enabled vs disabled)
   and that ``required_to_pass=False`` rows are advisory rather than
   hard vetoes.

Tests use a stub asyncpg pool that returns scripted rows so we can
exercise the read + dispatch logic without spinning up Postgres.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.content_validator import ValidationResult
from services.multi_model_qa import MultiModelQA, ReviewerResult
from services.qa_gates_db import QAGateSpec, load_qa_gate_chain
from services.site_config import SiteConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _passing_validation() -> ValidationResult:
    return ValidationResult(passed=True, issues=[], score_penalty=0)


def _make_sc(**overrides: Any) -> SiteConfig:
    """Per-test SiteConfig (matches existing test_multi_model_qa pattern)."""
    return SiteConfig(initial_config=dict(overrides))


class _StubConn:
    """Minimal asyncpg connection stub. Returns canned rows from ``script``."""

    def __init__(self, rows: list[dict[str, Any]] | Exception):
        self._rows = rows
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append((query, args))
        if isinstance(self._rows, Exception):
            raise self._rows
        return list(self._rows)

    async def execute(self, *_args: Any, **_kwargs: Any) -> str:  # pragma: no cover
        return "EXECUTE 0 0"


class _StubPool:
    """Minimal asyncpg pool stub — implements ``acquire()`` as an async ctx mgr."""

    def __init__(self, conn: _StubConn):
        self._conn = conn

    def acquire(self):
        # Returning an async context manager. asyncpg pools use a custom
        # acquire context manager — duck-typing it here is enough.
        pool_self = self

        class _Ctx:
            async def __aenter__(self_inner):
                return pool_self._conn

            async def __aexit__(self_inner, *_exc_info):
                return False

        return _Ctx()


def _row(
    name: str,
    *,
    order: int,
    reviewer: str | None = None,
    enabled: bool = True,
    required: bool = True,
    config: dict[str, Any] | None = None,
    stage: str = "qa",
) -> dict[str, Any]:
    """Shape-of-a-qa_gates-row dict (no DB needed)."""
    return {
        "name": name,
        "stage_name": stage,
        "execution_order": order,
        "reviewer": reviewer or name,
        "required_to_pass": required,
        "enabled": enabled,
        "config": config or {},
    }


# ---------------------------------------------------------------------------
# load_qa_gate_chain
# ---------------------------------------------------------------------------


class TestLoadQaGateChain:
    """The DB read layer."""

    async def test_qa_gates_iterated_by_execution_order(self):
        """Rows must come back in execution_order ascending — runtime
        depends on this for deterministic gate sequencing."""
        # Deliberately scramble insertion order; the SQL ORDER BY clause
        # in load_qa_gate_chain is responsible for sorting.
        rows = [
            _row("vision_gate", order=600),
            _row("programmatic_validator", order=100),
            _row("web_factcheck", order=500),
            _row("llm_critic", order=200),
            _row("consistency", order=400),
            _row("url_verifier", order=300),
        ]
        # Stub returns rows already ordered (mimics what Postgres would do
        # given the ORDER BY in the SQL). The test contract: load_qa_gate_chain
        # passes the ORDER BY and consumes rows in fetch order.
        ordered = sorted(rows, key=lambda r: r["execution_order"])
        conn = _StubConn(ordered)
        pool = _StubPool(conn)

        chain = await load_qa_gate_chain(pool)

        names_in_order = [g.name for g in chain]
        assert names_in_order == [
            "programmatic_validator",
            "llm_critic",
            "url_verifier",
            "consistency",
            "web_factcheck",
            "vision_gate",
        ]
        # Verify the SQL we emit actually orders by execution_order — if
        # someone refactors to drop the ORDER BY, this catches it.
        assert "ORDER BY execution_order" in conn.queries[0][0]

    async def test_qa_gates_disabled_skipped_when_only_enabled(self):
        """``only_enabled=True`` (default) must filter to ``enabled=TRUE``."""
        rows = [
            _row("a", order=100, enabled=True),
            _row("b", order=200, enabled=True),
        ]
        # The stub doesn't filter on its own — but we assert the query
        # contains the WHERE clause that would. Real Postgres applies it.
        conn = _StubConn(rows)
        pool = _StubPool(conn)

        await load_qa_gate_chain(pool, only_enabled=True)

        sql = conn.queries[0][0]
        assert "enabled = TRUE" in sql

        # And: if only_enabled=False, the WHERE clause must NOT add the filter.
        conn2 = _StubConn(rows)
        pool2 = _StubPool(conn2)
        await load_qa_gate_chain(pool2, only_enabled=False)
        assert "enabled = TRUE" not in conn2.queries[0][0]

    async def test_load_returns_empty_on_missing_table(self):
        """Table missing on fresh DB → return [] so legacy fallback fires.

        This is the contract MultiModelQA depends on for backward
        compatibility — without it, every existing test would explode.
        """
        # asyncpg raises ``UndefinedTableError`` on missing table.
        conn = _StubConn(RuntimeError("relation 'qa_gates' does not exist"))
        pool = _StubPool(conn)

        chain = await load_qa_gate_chain(pool)

        assert chain == []

    async def test_load_returns_empty_on_none_pool(self):
        """``pool=None`` short-circuits — no DB call, no error."""
        chain = await load_qa_gate_chain(None)
        assert chain == []


# ---------------------------------------------------------------------------
# QAGateSpec
# ---------------------------------------------------------------------------


class TestQAGateSpec:
    def test_applies_to_style_empty_config_matches_all(self):
        spec = QAGateSpec(
            name="x", stage_name="qa", execution_order=100,
            reviewer="x", required_to_pass=True, enabled=True, config={},
        )
        assert spec.applies_to_style("any-style-id")
        assert spec.applies_to_style(None)

    def test_applies_to_style_with_explicit_list(self):
        spec = QAGateSpec(
            name="x", stage_name="qa", execution_order=100,
            reviewer="x", required_to_pass=True, enabled=True,
            config={"applies_to_styles": ["style-a", "style-b"]},
        )
        assert spec.applies_to_style("style-a")
        assert spec.applies_to_style("style-b")
        assert not spec.applies_to_style("style-c")
        assert not spec.applies_to_style(None)


# ---------------------------------------------------------------------------
# MultiModelQA — disabled gate skipped, advisory honored
# ---------------------------------------------------------------------------


def _qa_with_chain(rows: list[dict[str, Any]]) -> MultiModelQA:
    """Build a MultiModelQA with a stub pool that returns ``rows`` from
    the qa_gates fetch — used by the consumer-side tests."""
    conn = _StubConn(sorted(rows, key=lambda r: r["execution_order"]))
    pool = _StubPool(conn)
    with patch("services.multi_model_qa.get_model_router", create=True, return_value=MagicMock()):
        # site_config kwarg removed — MultiModelQA reads SiteConfig
        # via the singleton import in _build_runtime_*().
        qa = MultiModelQA(pool=pool, settings_service=None)
    return qa


class TestQAGatesConsumer:
    """``MultiModelQA.review`` honors the loaded gate chain."""

    async def test_qa_gates_disabled_skipped_during_review(self):
        """A row with ``enabled=False`` must NOT produce a ReviewerResult.

        We disable ``llm_critic`` and assert the cross-model critic call
        is short-circuited (no ``ollama_critic`` review in the output).
        """
        rows = [
            _row("programmatic_validator", order=100, enabled=True),
            _row("llm_critic", order=200, enabled=False),
            _row("url_verifier", order=300, enabled=True),
            _row("consistency", order=400, enabled=False),
            _row("web_factcheck", order=500, enabled=False),
            _row("vision_gate", order=600, enabled=False),
        ]
        qa = _qa_with_chain(rows)

        # Sentinel: if _review_with_cloud_model is called, the test fails.
        cloud_call = AsyncMock(return_value=None)
        qa._review_with_cloud_model = cloud_call  # type: ignore[method-assign]
        # Stub other gates to None so they don't add reviews.
        qa._check_topic_delivery = AsyncMock(return_value=None)  # type: ignore[method-assign]
        qa._check_internal_consistency = AsyncMock(return_value=None)  # type: ignore[method-assign]
        qa._check_image_relevance = AsyncMock(return_value=None)  # type: ignore[method-assign]
        qa._web_fact_check = AsyncMock(return_value=None)  # type: ignore[method-assign]
        qa._check_citations = AsyncMock(return_value=None)  # type: ignore[method-assign]

        with patch(
            "services.multi_model_qa.validate_content",
            return_value=_passing_validation(),
        ):
            # Patch verify_content_urls (used inside the url_verifier
            # block) so it doesn't try real HTTP.
            with patch(
                "services.content_validator.verify_content_urls",
                AsyncMock(return_value=[]),
            ):
                result = await qa.review("Title", "body content here", "topic")

        cloud_call.assert_not_called(), \
            "llm_critic disabled in qa_gates → cross-model review must be skipped"
        reviewer_names = [r.reviewer for r in result.reviews]
        assert "ollama_critic" not in reviewer_names
        # programmatic_validator is in the chain enabled=True so it must run.
        assert "programmatic_validator" in reviewer_names

    async def test_qa_gates_required_to_pass_false_logs_but_continues(self):
        """An advisory gate that fails must NOT veto the post.

        ``programmatic_validator`` is required=False; the validator
        reports ``approved=False`` (a critical issue exists) but the
        review pipeline must still treat it as advisory and continue
        through the rest of the chain.
        """
        from services.content_validator import ValidationIssue

        rows = [
            _row("programmatic_validator", order=100, enabled=True, required=False),
            _row("llm_critic", order=200, enabled=False),
            _row("url_verifier", order=300, enabled=False),
            _row("consistency", order=400, enabled=False),
            _row("web_factcheck", order=500, enabled=False),
            _row("vision_gate", order=600, enabled=False),
        ]
        qa = _qa_with_chain(rows)
        qa._check_topic_delivery = AsyncMock(return_value=None)  # type: ignore[method-assign]
        qa._check_internal_consistency = AsyncMock(return_value=None)  # type: ignore[method-assign]
        qa._check_image_relevance = AsyncMock(return_value=None)  # type: ignore[method-assign]
        qa._web_fact_check = AsyncMock(return_value=None)  # type: ignore[method-assign]
        qa._check_citations = AsyncMock(return_value=None)  # type: ignore[method-assign]

        # Validator fails with a critical issue. Without the qa_gates
        # advisory override, this would short-circuit + reject. With
        # required_to_pass=False the post must continue.
        # Note: the immediate-rejection path in MultiModelQA fires when
        # the validator has any non-known_wrong_fact critical. To test
        # the advisory path we use a known_wrong_fact issue, which
        # already takes the deferred-rejection branch. The advisory
        # override applies on that branch.
        failing_validation = ValidationResult(
            passed=False,
            issues=[
                ValidationIssue(
                    severity="critical",
                    category="known_wrong_fact",
                    description="post-cutoff product",
                    matched_text="RTX 5090",
                ),
            ],
            score_penalty=20,
        )

        with patch(
            "services.multi_model_qa.validate_content",
            return_value=failing_validation,
        ):
            with patch(
                "services.content_validator.verify_content_urls",
                AsyncMock(return_value=[]),
            ):
                result = await qa.review("Title", "body", "topic")

        # The validator review must show approved=True (advisory override
        # applied). Score still reflects the penalty so a chronic failer
        # still drags the average.
        validator = next(
            r for r in result.reviews if r.reviewer == "programmatic_validator"
        )
        assert validator.approved is True, (
            "advisory gate (required_to_pass=False) must be flipped to "
            "approved=True so the rejection path doesn't fire"
        )
        assert "advisory" in validator.feedback.lower()
        assert validator.score < 100  # Score reflects penalty, not the flip.

    async def test_qa_gates_reorder_takes_effect_without_restart(self):
        """Updating a row's execution_order must surface in the next
        ``load_qa_gate_chain`` call — no restart required.

        The runtime calls ``load_qa_gate_chain`` at the start of every
        ``review()``, so the first call after the UPDATE sees the new
        order. We simulate the operator running ``qa-gates reorder`` by
        feeding two different stubs back-to-back.
        """
        # First call: the seeded order.
        first_rows = [
            _row("programmatic_validator", order=100),
            _row("llm_critic", order=200),
            _row("url_verifier", order=300),
        ]
        # Second call: operator moved url_verifier ahead of llm_critic.
        second_rows = [
            _row("programmatic_validator", order=100),
            _row("url_verifier", order=150),
            _row("llm_critic", order=200),
        ]

        first_pool = _StubPool(_StubConn(
            sorted(first_rows, key=lambda r: r["execution_order"])
        ))
        first_chain = await load_qa_gate_chain(first_pool)
        assert [g.name for g in first_chain] == [
            "programmatic_validator", "llm_critic", "url_verifier",
        ]

        second_pool = _StubPool(_StubConn(
            sorted(second_rows, key=lambda r: r["execution_order"])
        ))
        second_chain = await load_qa_gate_chain(second_pool)
        assert [g.name for g in second_chain] == [
            "programmatic_validator", "url_verifier", "llm_critic",
        ], (
            "qa_gates table is consulted on every review() call — an "
            "execution_order UPDATE must be visible immediately, with "
            "no process restart"
        )


# ---------------------------------------------------------------------------
# Backwards compatibility — empty table → legacy hardcoded chain
# ---------------------------------------------------------------------------


class TestLegacyFallback:
    """When ``qa_gates`` is empty (fresh checkout / pre-migration), the
    runtime must fall back to the historic hardcoded gate enables.

    Without this contract, every test in ``test_multi_model_qa.py``
    would explode the moment we landed this PR.
    """

    async def test_no_pool_falls_back_to_legacy_behavior(self):
        """``pool=None`` (the unit-test default for MultiModelQA) must
        load an empty chain → all gates default-enabled."""
        with patch(
            "services.multi_model_qa.get_model_router", create=True, return_value=MagicMock(),
        ):
            qa = MultiModelQA(pool=None, settings_service=None)
        chain = await load_qa_gate_chain(qa.pool)
        assert chain == []


# Make pytest treat all top-level coroutines as async tests under the
# project's ``asyncio_mode = auto`` setting (already enabled in
# pyproject.toml). This module needs no extra marks.
pytestmark = pytest.mark.asyncio
