"""Tests for ``services.media_approval_service``.

The DB queries are exercised against a mocked asyncpg-Connection-like
object — same shape both the production code (a pool acquired conn)
and the backfill jobs (a raw asyncpg.connect Connection) pass in. The
service was designed to take either, so the tests cover the lower
common denominator: an object with async ``fetchrow`` / ``fetch`` /
``execute``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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
