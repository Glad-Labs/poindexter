"""Tests for ``services.media_approval_service``.

The DB queries are exercised against a mocked asyncpg-Connection-like
object — same shape both the production code (a pool acquired conn)
and the backfill jobs (a raw asyncpg.connect Connection) pass in. The
service was designed to take either, so the tests cover the lower
common denominator: an object with async ``fetchrow`` / ``fetch`` /
``execute``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import media_approval_service


@pytest.fixture
def mock_db() -> MagicMock:
    """Bare-metal asyncpg-style stub — async methods on a Mock."""
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value=None)
    db.fetch = AsyncMock(return_value=[])
    db.execute = AsyncMock(return_value="INSERT 0 1")
    return db


# ---------------------------------------------------------------------------
# Medium validation
# ---------------------------------------------------------------------------


async def test_record_pending_rejects_unknown_medium(mock_db: MagicMock) -> None:
    """Typo'd medium must fail loud — no silent default."""
    with pytest.raises(media_approval_service.InvalidMediumError):
        await media_approval_service.record_pending(
            mock_db, "00000000-0000-0000-0000-000000000001", "podcasst",
        )


async def test_is_approved_rejects_unknown_medium(mock_db: MagicMock) -> None:
    with pytest.raises(media_approval_service.InvalidMediumError):
        await media_approval_service.is_approved(
            mock_db, "00000000-0000-0000-0000-000000000001", "audio",
        )


async def test_decide_rejects_unknown_medium(mock_db: MagicMock) -> None:
    with pytest.raises(media_approval_service.InvalidMediumError):
        await media_approval_service.decide(
            mock_db, "00000000-0000-0000-0000-000000000001", "movie",
            approved=True, decided_by="operator:test",
        )


# ---------------------------------------------------------------------------
# record_pending
# ---------------------------------------------------------------------------


async def test_record_pending_inserts_pending_without_niche(
    mock_db: MagicMock,
) -> None:
    """No niche on the post → manual approval path, status='pending'."""
    # First fetchrow call resolves niche_slug → None → manual approval branch.
    mock_db.fetchrow.return_value = None

    result = await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    )

    assert result == "pending"
    # Verify the INSERT included status='pending'.
    insert_sql = mock_db.execute.call_args.args[0]
    assert "'pending'" in insert_sql
    assert "ON CONFLICT (post_id, medium) DO NOTHING" in insert_sql


async def test_niche_lookup_joins_pipeline_tasks_not_posts_column(
    mock_db: MagicMock,
) -> None:
    """Niche must be resolved via the ``pipeline_task_id`` seam, NOT a
    (nonexistent) ``posts.niche_slug`` column.

    Regression guard for the silent media-approval crash: ``posts`` has
    no ``niche_slug`` column, so ``SELECT niche_slug FROM posts`` raised
    ``column "niche_slug" does not exist`` and every generated podcast /
    video was uploaded but never entered the approval queue. A MagicMock
    can't catch a column-vs-schema mismatch, so we assert on the SQL
    shape directly.
    """
    mock_db.fetchrow.return_value = None

    await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    )

    niche_sql = mock_db.fetchrow.call_args_list[0].args[0]
    assert "pipeline_tasks" in niche_sql
    assert "pipeline_task_id" in niche_sql
    # The bug was querying a column that doesn't exist on posts.
    assert "niche_slug FROM posts" not in niche_sql.replace("\n", " ")


async def test_record_pending_auto_approves_when_niche_setting_enabled(
    mock_db: MagicMock,
) -> None:
    """Per-niche auto-approve flips status='approved' on insert."""
    # First call: niche lookup. Second call: app_settings lookup.
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "glad-labs"},
        {"value": "true"},
    ]

    result = await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    )

    assert result == "approved"
    insert_sql = mock_db.execute.call_args.args[0]
    assert "'approved'" in insert_sql
    # decided_by must record the niche so provenance is preserved.
    decided_by_arg = mock_db.execute.call_args.args[3]
    assert decided_by_arg == "auto:niche.glad-labs"


async def test_record_pending_stays_pending_when_niche_setting_disabled(
    mock_db: MagicMock,
) -> None:
    """Setting present but value=false → manual approval (conservative)."""
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "glad-labs"},
        {"value": "false"},
    ]

    result = await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    )

    assert result == "pending"


async def test_record_pending_stays_pending_when_setting_missing(
    mock_db: MagicMock,
) -> None:
    """Missing app_settings row → not enabled (no silent default)."""
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "glad-labs"},
        None,  # no app_settings row
    ]

    result = await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "video",
    )

    assert result == "pending"


# ---------------------------------------------------------------------------
# is_approved
# ---------------------------------------------------------------------------


async def test_is_approved_true_when_row_status_approved(
    mock_db: MagicMock,
) -> None:
    mock_db.fetchrow.return_value = {"status": "approved"}
    assert await media_approval_service.is_approved(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    ) is True


async def test_is_approved_false_when_row_pending(mock_db: MagicMock) -> None:
    mock_db.fetchrow.return_value = {"status": "pending"}
    assert await media_approval_service.is_approved(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    ) is False


async def test_is_approved_false_when_row_rejected(mock_db: MagicMock) -> None:
    mock_db.fetchrow.return_value = {"status": "rejected"}
    assert await media_approval_service.is_approved(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    ) is False


async def test_is_approved_false_when_no_row(mock_db: MagicMock) -> None:
    """No row = not approved (conservative default)."""
    mock_db.fetchrow.return_value = None
    assert await media_approval_service.is_approved(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    ) is False


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------


async def test_decide_approve_sets_status_approved(mock_db: MagicMock) -> None:
    mock_db.fetchrow.return_value = {"status": "approved"}

    await media_approval_service.decide(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
        approved=True, decided_by="operator:cli",
    )

    # UPDATE called with status='approved' as the 3rd positional arg.
    update_args = mock_db.fetchrow.call_args.args
    assert update_args[3] == "approved"
    assert update_args[4] == "operator:cli"


async def test_decide_reject_sets_status_rejected(mock_db: MagicMock) -> None:
    mock_db.fetchrow.return_value = {"status": "rejected"}

    await media_approval_service.decide(
        mock_db, "00000000-0000-0000-0000-000000000001", "video",
        approved=False, decided_by="operator:cli", notes="too long",
    )

    update_args = mock_db.fetchrow.call_args.args
    assert update_args[3] == "rejected"
    assert update_args[5] == "too long"


async def test_decide_raises_when_row_does_not_exist(
    mock_db: MagicMock,
) -> None:
    """No row = caller is pre-approving a not-yet-generated medium.

    Fail loud — letting this silently insert a row would let an
    operator skip the whole gate (the row would have status='approved'
    but no file on disk to distribute, masking the failure path).
    """
    mock_db.fetchrow.return_value = None

    with pytest.raises(ValueError, match="No media_approvals row"):
        await media_approval_service.decide(
            mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
            approved=True, decided_by="operator:cli",
        )


# ---------------------------------------------------------------------------
# list_pending
# ---------------------------------------------------------------------------


async def test_list_pending_returns_rows(mock_db: MagicMock) -> None:
    mock_db.fetch.return_value = [
        {
            "post_id": "abc",
            "medium": "podcast",
            "created_at": None,
            "title": "Post",
            "slug": "post",
        },
    ]
    rows = await media_approval_service.list_pending(mock_db)
    assert len(rows) == 1
    assert rows[0]["medium"] == "podcast"


async def test_list_pending_medium_filter_validates(mock_db: MagicMock) -> None:
    with pytest.raises(media_approval_service.InvalidMediumError):
        await media_approval_service.list_pending(mock_db, medium="audio")


# ---------------------------------------------------------------------------
# notify_pending_for_review — Discord ops ping when a new medium needs review
# ---------------------------------------------------------------------------


async def test_record_pending_then_notify_discord_dispatches_when_status_pending(
    mock_db: MagicMock,
) -> None:
    """Happy path: pending row → notify_operator called once with the
    rendered Discord-style message body.

    Named for the ``-k "record_pending and discord"`` filter the PR
    spec calls out.
    """
    # First fetchrow: app_settings enable check (missing → defaults on).
    # Second fetchrow: the media_approvals row + post title.
    mock_db.fetchrow.side_effect = [
        None,  # enable flag missing → defaults on
        {
            "status": "pending",
            "quality_score": 0.85,
            "quality_signals": '{"duration_seconds": 240.0, "silence_ratio": 0.05, "file_size_bytes": 2400000}',
            "title": "Why Cofounders Burn Out",
            "slug": "why-cofounders-burn-out",
        },
    ]

    from unittest.mock import AsyncMock as _AsyncMock
    mock_notify = _AsyncMock()
    with patch(
        "services.integrations.operator_notify.notify_operator",
        mock_notify,
    ):
        result = await media_approval_service.notify_pending_for_review(
            mock_db, "12345678-1234-1234-1234-123456789012", "podcast",
        )

    assert result is True
    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    kwargs = mock_notify.call_args.kwargs
    # Discord-only routing — must NOT be critical (that's Telegram).
    assert kwargs.get("critical") is False
    # Sanity: the rendered body contains the operator-useful fields.
    assert "podcast awaiting approval" in msg
    assert "Why Cofounders Burn Out" in msg
    assert "score=0.85" in msg
    assert "duration=240s" in msg
    assert "silence=5%" in msg
    # Operator commands appear so they can act on the ping.
    assert "poindexter media pending --medium podcast" in msg
    assert "poindexter media open" in msg


async def test_record_pending_auto_approve_then_discord_notify_skipped(
    mock_db: MagicMock,
) -> None:
    """Auto-approve fast path → row.status='approved' → no notify."""
    mock_db.fetchrow.side_effect = [
        None,  # enable defaults on
        {
            "status": "approved",
            "quality_score": 1.0,
            "quality_signals": "{}",
            "title": "X",
            "slug": "x",
        },
    ]

    from unittest.mock import AsyncMock as _AsyncMock
    mock_notify = _AsyncMock()
    with patch(
        "services.integrations.operator_notify.notify_operator",
        mock_notify,
    ):
        result = await media_approval_service.notify_pending_for_review(
            mock_db, "12345678-1234-1234-1234-123456789012", "podcast",
        )

    assert result is False
    mock_notify.assert_not_called()


async def test_notify_pending_for_review_skips_when_status_rejected(
    mock_db: MagicMock,
) -> None:
    """Layer 1 auto-reject leaves the row at status='rejected' — no notify."""
    mock_db.fetchrow.side_effect = [
        None,
        {
            "status": "rejected",
            "quality_score": 0.0,
            "quality_signals": "{}",
            "title": "X",
            "slug": "x",
        },
    ]

    from unittest.mock import AsyncMock as _AsyncMock
    mock_notify = _AsyncMock()
    with patch(
        "services.integrations.operator_notify.notify_operator",
        mock_notify,
    ):
        result = await media_approval_service.notify_pending_for_review(
            mock_db, "12345678-1234-1234-1234-123456789012", "podcast",
        )

    assert result is False
    mock_notify.assert_not_called()


async def test_notify_pending_for_review_skips_when_disabled(
    mock_db: MagicMock,
) -> None:
    """Operator can disable the ping via app_settings — defaults to on,
    but ``false`` honored."""
    mock_db.fetchrow.side_effect = [
        {"value": "false"},  # operator turned it off
        # Even if we got past, no second row needed because the
        # function should short-circuit.
    ]

    from unittest.mock import AsyncMock as _AsyncMock
    mock_notify = _AsyncMock()
    with patch(
        "services.integrations.operator_notify.notify_operator",
        mock_notify,
    ):
        result = await media_approval_service.notify_pending_for_review(
            mock_db, "12345678-1234-1234-1234-123456789012", "podcast",
        )

    assert result is False
    mock_notify.assert_not_called()


async def test_record_pending_notify_discord_swallows_dispatch_errors(
    mock_db: MagicMock,
) -> None:
    """Discord dispatch failure MUST NOT raise — pure observability."""
    mock_db.fetchrow.side_effect = [
        None,
        {
            "status": "pending",
            "quality_score": 0.9,
            "quality_signals": "{}",
            "title": "Post",
            "slug": "post",
        },
    ]

    from unittest.mock import AsyncMock as _AsyncMock
    mock_notify = _AsyncMock(side_effect=RuntimeError("discord exploded"))
    with patch(
        "services.integrations.operator_notify.notify_operator",
        mock_notify,
    ):
        # No raise — returns False to signal "skipped/failed".
        result = await media_approval_service.notify_pending_for_review(
            mock_db, "12345678-1234-1234-1234-123456789012", "podcast",
        )

    assert result is False
    mock_notify.assert_called_once()


async def test_record_pending_then_quality_eval_path_does_not_notify_when_auto_approved(
    mock_db: MagicMock,
) -> None:
    """End-to-end: auto-approve fast path inserts status='approved'.
    A subsequent notify_pending_for_review call MUST skip the Discord
    ping (operator has no pending decision to take).

    Validates the failure-mode the task spec called out: the Discord
    notify should NOT fire on the niche auto-approve path.
    """
    # record_pending step:
    #   fetchrow #1: niche_slug lookup → 'glad-labs'
    #   fetchrow #2: niche auto_approve setting → true
    # notify_pending_for_review step:
    #   fetchrow #3: app_settings enable flag → missing (defaults on)
    #   fetchrow #4: media_approvals row → status='approved'
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "glad-labs"},
        {"value": "true"},
        None,
        {
            "status": "approved",
            "quality_score": 1.0,
            "quality_signals": "{}",
            "title": "X",
            "slug": "x",
        },
    ]

    status = await media_approval_service.record_pending(
        mock_db, "12345678-1234-1234-1234-123456789012", "podcast",
    )
    assert status == "approved"

    from unittest.mock import AsyncMock as _AsyncMock
    mock_notify = _AsyncMock()
    with patch(
        "services.integrations.operator_notify.notify_operator",
        mock_notify,
    ):
        result = await media_approval_service.notify_pending_for_review(
            mock_db, "12345678-1234-1234-1234-123456789012", "podcast",
        )

    assert result is False
    mock_notify.assert_not_called()
