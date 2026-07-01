"""Unit tests for Tier 1b queue trace propagation in the content-generation flow.

- ``claim_pending_task`` SELECTs the stored ``trace_context`` column so the
  claimed row carries the enqueuer's W3C carrier.
- ``_parent_context_from_claimed`` re-hydrates that carrier into an OTel parent
  context, which the flow attaches around its root span so a content run links
  to the trace of whatever created the task (Glad-Labs/poindexter#1997).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

_TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C
_SPAN_ID = 0x00F067AA0BA902B7


def _db_double_capturing(row):
    """A database_service double whose pool yields a conn that records the
    SELECT SQL and returns ``row`` from fetchrow."""
    captured: dict[str, str] = {}
    conn = MagicMock()

    async def _fetchrow(sql, *_a, **_k):
        captured["select_sql"] = sql
        return row

    async def _execute(_sql, *_a, **_k):
        return None

    conn.fetchrow = _fetchrow
    conn.execute = _execute

    @asynccontextmanager
    async def _tx():
        yield

    conn.transaction = MagicMock(side_effect=lambda *a, **k: _tx())

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    db = MagicMock()
    db.pool = pool
    return db, captured


class TestClaimSelectsTraceContext:
    @pytest.mark.asyncio
    async def test_claim_select_includes_trace_context_column(self):
        from services.flows.content_generation import claim_pending_task

        row = {
            "task_id": "t1", "topic": "AI", "style": None, "tone": None,
            "target_length": 900, "target_audience": None, "niche_slug": None,
            "template_slug": None, "primary_keyword": None, "site_id": None,
            "trace_context": None,
        }
        db, captured = _db_double_capturing(row)

        claimed = await claim_pending_task.fn(db)

        assert claimed is not None
        assert "trace_context" in captured["select_sql"], (
            "claim_pending_task SELECT must read trace_context so the flow can "
            "link its root span to the enqueuer's trace"
        )


class TestParentContextFromClaimed:
    def test_none_claimed_returns_none(self):
        from services.flows.content_generation import _parent_context_from_claimed

        assert _parent_context_from_claimed(None) is None

    def test_claimed_without_trace_context_returns_none(self):
        from services.flows.content_generation import _parent_context_from_claimed

        assert _parent_context_from_claimed({"task_id": "t1"}) is None

    def test_claimed_with_carrier_returns_linked_context(self):
        from opentelemetry import trace as ot
        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

        from plugins.tracing import inject_trace_context
        from services.flows.content_generation import _parent_context_from_claimed

        sc = SpanContext(
            trace_id=_TRACE_ID, span_id=_SPAN_ID, is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        carrier = inject_trace_context(
            context=ot.set_span_in_context(NonRecordingSpan(sc))
        )
        # asyncpg hands jsonb back as a string — exercise that shape.
        ctx = _parent_context_from_claimed(
            {"task_id": "t1", "trace_context": json.dumps(carrier)}
        )

        assert ctx is not None
        assert ot.get_current_span(ctx).get_span_context().trace_id == _TRACE_ID
