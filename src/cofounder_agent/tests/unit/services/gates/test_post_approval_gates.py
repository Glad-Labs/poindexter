"""Tests for the per-medium approval gate engine (Glad-Labs/poindexter#24).

Round-trips against the real Postgres test DB via the ``db_pool``
fixture in :mod:`tests.unit.conftest`. When no live Postgres is
reachable the fixture skips the module — same pattern as
``test_niche_service.py``.

We deliberately avoid hand-rolled asyncpg row fakes: the parallel
``fix/test-mock-freshness-row-faker-cleanup`` work is migrating
existing tests AWAY from that pattern, so new tests follow the
db_pool path from day one.
"""

from __future__ import annotations

import pytest

from services.gates.post_approval_gates import (
    CANONICAL_GATE_NAMES,
    GATE_STATE_APPROVED,
    GATE_STATE_PENDING,
    GATE_STATE_REJECTED,
    GATE_STATE_REVISING,
    GateCascadeRequiredError,
    GateNotFoundError,
    GateStateError,
    advance_workflow,
    approve_gate,
    create_gates_for_post,
    get_gates_for_post,
    get_next_pending_gate,
    record_media_failure,
    reject_gate,
    reopen_gate,
    reset_gate_to_pending,
    revise_gate,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_post(pool, *, status: str = "draft", title: str = "Test post") -> str:
    """Insert a minimal posts row, return its UUID as string.

    The schema mandates non-null title/slug/content; we use random slug
    suffixes so concurrent test runs don't collide on the UNIQUE
    constraint.
    """
    import secrets

    slug = f"test-{secrets.token_hex(6)}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO posts (title, slug, content, status)
            VALUES ($1, $2, '', $3)
            RETURNING id::text AS id
            """,
            title, slug, status,
        )
    return row["id"]


async def _drop_test_post(pool, post_id: str) -> None:
    """Clean up a test post (CASCADE drops its gate rows)."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM posts WHERE id::text = $1", post_id)


# ---------------------------------------------------------------------------
# create_gates_for_post
# ---------------------------------------------------------------------------


async def test_create_gates_for_post_inserts_in_workflow_order(db_pool):
    pid = await _make_post(db_pool)
    try:
        gates = await create_gates_for_post(
            db_pool, pid, ["topic", "draft", "final"]
        )
        assert [g["gate_name"] for g in gates] == ["topic", "draft", "final"]
        assert [g["ordinal"] for g in gates] == [0, 1, 2]
        assert all(g["state"] == GATE_STATE_PENDING for g in gates)
    finally:
        await _drop_test_post(db_pool, pid)


async def test_create_gates_for_post_empty_list_returns_empty(db_pool):
    """Autonomous workflow — no gates created, returns empty list."""
    pid = await _make_post(db_pool)
    try:
        gates = await create_gates_for_post(db_pool, pid, [])
        assert gates == []
        # And nothing landed in the table for this post.
        assert await get_gates_for_post(db_pool, pid) == []
    finally:
        await _drop_test_post(db_pool, pid)


async def test_create_gates_for_post_rejects_unknown_gate_name(db_pool):
    pid = await _make_post(db_pool)
    try:
        with pytest.raises(ValueError, match="Unknown gate name 'totallyfake'"):
            await create_gates_for_post(db_pool, pid, ["topic", "totallyfake"])
        # Transactional INSERTs: the partial 'topic' insert should have
        # been rolled back, so no rows should exist for the post.
        assert await get_gates_for_post(db_pool, pid) == []
    finally:
        await _drop_test_post(db_pool, pid)


async def test_canonical_gate_names_includes_media_generation_failed():
    """Sanity — the escalation gate name is in the canonical set so
    record_media_failure can insert it without ValueError."""
    assert "media_generation_failed" in CANONICAL_GATE_NAMES


# ---------------------------------------------------------------------------
# get_next_pending_gate / advance_workflow
# ---------------------------------------------------------------------------


async def test_get_next_pending_gate_returns_lowest_ordinal_pending(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic", "draft", "final"])
        nxt = await get_next_pending_gate(db_pool, pid)
        assert nxt is not None
        assert nxt["gate_name"] == "topic"
        assert nxt["ordinal"] == 0
    finally:
        await _drop_test_post(db_pool, pid)


async def test_advance_workflow_returns_next_gate(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic", "final"])
        adv = await advance_workflow(db_pool, pid)
        assert adv.next_gate is not None
        assert adv.next_gate["gate_name"] == "topic"
        assert not adv.ready_to_distribute
        assert not adv.finished
    finally:
        await _drop_test_post(db_pool, pid)


async def test_advance_workflow_ready_to_distribute_when_all_decided(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic", "final"])
        await approve_gate(db_pool, pid, "topic", approver="alice")
        await approve_gate(db_pool, pid, "final", approver="alice")
        adv = await advance_workflow(db_pool, pid)
        assert adv.ready_to_distribute is True
        assert adv.next_gate is None
    finally:
        await _drop_test_post(db_pool, pid)


async def test_advance_workflow_autonomous_post_immediately_distributes(db_pool):
    """No gate rows = autonomous = ready_to_distribute the moment we ask."""
    pid = await _make_post(db_pool)
    try:
        # No create_gates_for_post call — the post is bare.
        adv = await advance_workflow(db_pool, pid)
        assert adv.ready_to_distribute is True
    finally:
        await _drop_test_post(db_pool, pid)


async def test_advance_workflow_finished_when_post_rejected(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic"])
        await reject_gate(db_pool, pid, "topic", approver="alice", reason="no")
        adv = await advance_workflow(db_pool, pid)
        assert adv.finished is True
        assert adv.reason == "post_rejected"
    finally:
        await _drop_test_post(db_pool, pid)


# ---------------------------------------------------------------------------
# approve_gate
# ---------------------------------------------------------------------------


async def test_approve_gate_flips_state_and_records_approver(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic"])
        row = await approve_gate(
            db_pool, pid, "topic", approver="alice", notes="lgtm",
        )
        assert row["state"] == GATE_STATE_APPROVED
        assert row["approver"] == "alice"
        assert row["notes"] == "lgtm"
        assert row["decided_at"] is not None
    finally:
        await _drop_test_post(db_pool, pid)


async def test_approve_gate_raises_on_unknown_gate(db_pool):
    pid = await _make_post(db_pool)
    try:
        with pytest.raises(GateNotFoundError):
            await approve_gate(db_pool, pid, "topic", approver="alice")
    finally:
        await _drop_test_post(db_pool, pid)


async def test_approve_gate_raises_when_already_decided(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic"])
        await approve_gate(db_pool, pid, "topic", approver="alice")
        with pytest.raises(GateStateError, match="state 'approved'"):
            await approve_gate(db_pool, pid, "topic", approver="bob")
    finally:
        await _drop_test_post(db_pool, pid)


# ---------------------------------------------------------------------------
# reject_gate — kills the post
# ---------------------------------------------------------------------------


async def test_reject_gate_kills_post(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic", "draft", "final"])
        await reject_gate(
            db_pool, pid, "topic", approver="alice", reason="bad topic",
        )
        # Gate row reflects rejection
        gates = await get_gates_for_post(db_pool, pid)
        topic_row = next(g for g in gates if g["gate_name"] == "topic")
        assert topic_row["state"] == GATE_STATE_REJECTED
        assert topic_row["notes"] == "bad topic"
        # Post status flipped
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM posts WHERE id::text = $1", pid,
            )
        assert row["status"] == "rejected"
    finally:
        await _drop_test_post(db_pool, pid)


# ---------------------------------------------------------------------------
# revise_gate
# ---------------------------------------------------------------------------


async def test_revise_gate_sets_revising_state_with_feedback_in_metadata(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["draft"])
        row = await revise_gate(
            db_pool, pid, "draft", approver="alice",
            feedback="please tighten the intro",
        )
        assert row["state"] == GATE_STATE_REVISING
        assert row["notes"] == "please tighten the intro"
        meta = row["metadata"]
        assert meta["feedback"] == "please tighten the intro"
        assert len(meta["revisions"]) == 1
        assert meta["revisions"][0]["approver"] == "alice"
    finally:
        await _drop_test_post(db_pool, pid)


async def test_revise_gate_appends_to_revisions_history(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["draft"])
        await revise_gate(db_pool, pid, "draft", approver="alice", feedback="v1")
        # Gate is now in revising — second revise extends the history.
        row = await revise_gate(
            db_pool, pid, "draft", approver="bob", feedback="v2",
        )
        meta = row["metadata"]
        assert [r["feedback"] for r in meta["revisions"]] == ["v1", "v2"]
        assert meta["feedback"] == "v2"
    finally:
        await _drop_test_post(db_pool, pid)


async def test_revise_gate_requires_feedback(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["draft"])
        with pytest.raises(ValueError, match="non-empty feedback"):
            await revise_gate(db_pool, pid, "draft", approver="alice", feedback="")
    finally:
        await _drop_test_post(db_pool, pid)


async def test_reset_gate_to_pending_after_revise(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["draft"])
        await revise_gate(db_pool, pid, "draft", approver="alice", feedback="x")
        row = await reset_gate_to_pending(db_pool, pid, "draft")
        assert row["state"] == GATE_STATE_PENDING
        assert row["approver"] is None
    finally:
        await _drop_test_post(db_pool, pid)


# ---------------------------------------------------------------------------
# reopen_gate — cascade behavior
# ---------------------------------------------------------------------------


async def test_reopen_gate_without_cascade_refuses_when_downstream_decided(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic", "draft", "final"])
        await approve_gate(db_pool, pid, "topic", approver="alice")
        await approve_gate(db_pool, pid, "draft", approver="alice")
        # Trying to reopen 'topic' would invalidate 'draft' silently.
        with pytest.raises(GateCascadeRequiredError, match="downstream decided"):
            await reopen_gate(db_pool, pid, "topic", cascade=False)
        # State unchanged.
        gates = await get_gates_for_post(db_pool, pid)
        assert {g["gate_name"]: g["state"] for g in gates} == {
            "topic": GATE_STATE_APPROVED,
            "draft": GATE_STATE_APPROVED,
            "final": GATE_STATE_PENDING,
        }
    finally:
        await _drop_test_post(db_pool, pid)


async def test_reopen_gate_with_cascade_invalidates_downstream(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic", "draft", "final"])
        await approve_gate(db_pool, pid, "topic", approver="alice")
        await approve_gate(db_pool, pid, "draft", approver="alice")
        row = await reopen_gate(db_pool, pid, "topic", cascade=True)
        assert row["state"] == GATE_STATE_PENDING
        # All downstream rows ALSO flipped back to pending.
        gates = await get_gates_for_post(db_pool, pid)
        states = {g["gate_name"]: g["state"] for g in gates}
        assert states == {
            "topic": GATE_STATE_PENDING,
            "draft": GATE_STATE_PENDING,
            "final": GATE_STATE_PENDING,
        }
    finally:
        await _drop_test_post(db_pool, pid)


async def test_reopen_gate_no_downstream_succeeds_without_cascade(db_pool):
    """Reopening the LAST decided gate doesn't need cascade."""
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic", "draft"])
        await approve_gate(db_pool, pid, "topic", approver="alice")
        # 'draft' still pending — reopening 'topic' has no downstream
        # decided gates, so cascade=False is fine.
        row = await reopen_gate(db_pool, pid, "topic", cascade=False)
        assert row["state"] == GATE_STATE_PENDING
    finally:
        await _drop_test_post(db_pool, pid)


# ---------------------------------------------------------------------------
# record_media_failure — retry budget + escalation
# ---------------------------------------------------------------------------


async def test_record_media_failure_under_limit_does_not_escalate(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["podcast"])
        result = await record_media_failure(
            db_pool, pid, "podcast", "boom 1", retry_limit=2,
        )
        assert result["escalated"] is False
        assert result["attempts"] == 1
        assert result["gate_id"] is None
        # No media_generation_failed row inserted yet.
        gates = await get_gates_for_post(db_pool, pid)
        assert {g["gate_name"] for g in gates} == {"podcast"}
    finally:
        await _drop_test_post(db_pool, pid)


async def test_record_media_failure_escalates_after_retry_limit(db_pool):
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["podcast"])
        # retry_limit=2 means attempt #3 is the one that escalates.
        await record_media_failure(db_pool, pid, "podcast", "boom 1", retry_limit=2)
        await record_media_failure(db_pool, pid, "podcast", "boom 2", retry_limit=2)
        result = await record_media_failure(
            db_pool, pid, "podcast", "boom 3", retry_limit=2,
        )
        assert result["escalated"] is True
        assert result["attempts"] == 3
        assert result["gate_id"] is not None
        gates = await get_gates_for_post(db_pool, pid)
        # An auto-inserted media_generation_failed gate is now present.
        assert "media_generation_failed" in {g["gate_name"] for g in gates}
        esc = next(g for g in gates if g["gate_name"] == "media_generation_failed")
        assert esc["state"] == GATE_STATE_PENDING
        assert esc["metadata"]["failed_medium"] == "podcast"
        assert esc["metadata"]["last_error"] == "boom 3"
    finally:
        await _drop_test_post(db_pool, pid)


async def test_record_media_failure_rejects_unknown_medium(db_pool):
    pid = await _make_post(db_pool)
    try:
        with pytest.raises(ValueError, match="Unknown medium"):
            await record_media_failure(
                db_pool, pid, "tiktok", "nope", retry_limit=2,
            )
    finally:
        await _drop_test_post(db_pool, pid)


# ---------------------------------------------------------------------------
# End-to-end state machine — full happy-path multi-gate
# ---------------------------------------------------------------------------


async def test_full_workflow_multi_gate_happy_path(db_pool):
    """Walk a post through topic → draft → final, asserting at each step."""
    pid = await _make_post(db_pool)
    try:
        await create_gates_for_post(db_pool, pid, ["topic", "draft", "final"])

        # Step 1: topic pending
        adv = await advance_workflow(db_pool, pid)
        assert adv.next_gate["gate_name"] == "topic"

        # Approve topic
        await approve_gate(db_pool, pid, "topic", approver="op")
        adv = await advance_workflow(db_pool, pid)
        assert adv.next_gate["gate_name"] == "draft"

        # Approve draft
        await approve_gate(db_pool, pid, "draft", approver="op")
        adv = await advance_workflow(db_pool, pid)
        assert adv.next_gate["gate_name"] == "final"

        # Approve final → ready to distribute
        await approve_gate(db_pool, pid, "final", approver="op")
        adv = await advance_workflow(db_pool, pid)
        assert adv.ready_to_distribute is True
    finally:
        await _drop_test_post(db_pool, pid)
