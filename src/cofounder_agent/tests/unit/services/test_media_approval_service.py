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
    """Setting present but value=false → manual approval (conservative).
    Tier-2 earned-autonomy also disabled (master switch off) so stays pending.
    """
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "glad-labs"},
        {"value": "false"},  # Tier-1 manual opt-in: off
        {"value": "false"},  # Tier-2 earned_autonomy_enabled: off
    ]

    result = await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    )

    assert result == "pending"


async def test_record_pending_stays_pending_when_setting_missing(
    mock_db: MagicMock,
) -> None:
    """Missing app_settings row → not enabled (no silent default).
    Tier-2 earned-autonomy also absent → stays pending.
    """
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "glad-labs"},
        None,  # Tier-1 manual opt-in: no row
        None,  # Tier-2 earned_autonomy_enabled: no row
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
# decide — rebuild matching feed on approve (self-healing propagation)
# ---------------------------------------------------------------------------


async def test_decide_approve_rebuilds_matching_feed(mock_db: MagicMock) -> None:
    """On approve with a site_config, the matching R2 feed is rebuilt so the
    approval reaches Apple/Spotify/the video feed immediately — media is
    approved AFTER publish, when the publish-time R2 rebuild already ran."""
    mock_db.fetchrow.return_value = {"status": "approved"}
    sc = MagicMock()
    with patch(
        "services.media_feed_rebuild.rebuild_feed_for_medium",
        new=AsyncMock(),
    ) as rebuild:
        await media_approval_service.decide(
            mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
            approved=True, decided_by="operator:cli", site_config=sc,
        )
    rebuild.assert_awaited_once_with(sc, "podcast")


async def test_decide_reject_does_not_rebuild_feed(mock_db: MagicMock) -> None:
    """A rejection never reaches a public surface, so nothing to rebuild."""
    mock_db.fetchrow.return_value = {"status": "rejected"}
    sc = MagicMock()
    with patch(
        "services.media_feed_rebuild.rebuild_feed_for_medium",
        new=AsyncMock(),
    ) as rebuild:
        await media_approval_service.decide(
            mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
            approved=False, decided_by="operator:cli", site_config=sc,
        )
    rebuild.assert_not_awaited()


async def test_decide_without_site_config_does_not_rebuild(
    mock_db: MagicMock,
) -> None:
    """Backcompat: callers that don't pass site_config (existing call sites,
    jobs, tests) still work — the rebuild is simply skipped, no error."""
    mock_db.fetchrow.return_value = {"status": "approved"}
    with patch(
        "services.media_feed_rebuild.rebuild_feed_for_medium",
        new=AsyncMock(),
    ) as rebuild:
        await media_approval_service.decide(
            mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
            approved=True, decided_by="operator:cli",
        )
    rebuild.assert_not_awaited()


async def test_decide_rebuild_failure_is_non_fatal(mock_db: MagicMock) -> None:
    """A feed-rebuild failure must NOT bubble out of decide() — the approval is
    already committed to the DB; the rebuild is additive self-healing."""
    mock_db.fetchrow.return_value = {"status": "approved"}
    sc = MagicMock()
    with patch(
        "services.media_feed_rebuild.rebuild_feed_for_medium",
        new=AsyncMock(side_effect=RuntimeError("worker down")),
    ):
        # Must not raise.
        await media_approval_service.decide(
            mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
            approved=True, decided_by="operator:cli", site_config=sc,
        )


# ---------------------------------------------------------------------------
# list_approved_undispatched — the upload-dispatcher selector
# ---------------------------------------------------------------------------


async def test_list_approved_undispatched_excludes_grandfather(
    mock_db: MagicMock,
) -> None:
    """The upload dispatchers must NOT re-deliver grandfathered media.

    Grandfather rows (``decided_by LIKE '%grandfather%'``) bless already-live
    media as ``approved`` so a newly-gated RSS feed keeps showing it — but the
    media is already distributed and must never be queued for upload. The
    selector therefore excludes grandfather rows, NULL-safe via COALESCE so
    operator rows with a NULL ``decided_by`` are still returned. Regression
    guard for the 2026-06-15 re-upload incident (glad-labs-stack#1596).
    """
    mock_db.fetch.return_value = []
    await media_approval_service.list_approved_undispatched(mock_db, medium="video")
    sql = mock_db.fetch.call_args.args[0]
    assert "ma.dispatched_at IS NULL" in sql  # still gates on never-delivered
    assert "COALESCE(ma.decided_by, '') NOT LIKE '%grandfather%'" in sql


async def test_list_approved_undispatched_still_returns_normal_rows(
    mock_db: MagicMock,
) -> None:
    """The grandfather guard must not disturb the normal return path."""
    mock_db.fetch.return_value = [
        {
            "post_id": "abc", "medium": "video", "title": "T", "content": "c",
            "excerpt": "e", "seo_keywords": "k", "slug": "s",
        },
    ]
    rows = await media_approval_service.list_approved_undispatched(
        mock_db, medium="video",
    )
    assert len(rows) == 1
    assert rows[0]["post_id"] == "abc"


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


# ---------------------------------------------------------------------------
# Earned-autonomy (#531) — _earned_autonomy_check + record_pending Tier-2
# ---------------------------------------------------------------------------


async def test_earned_autonomy_grants_when_threshold_met(
    mock_db: MagicMock,
) -> None:
    """With 5 consecutive successful dispatches and master switch on, Tier-2
    fires and record_pending returns 'approved'."""
    # record_pending flow:
    #   fetchrow #1: niche lookup → 'glad-labs'
    #   fetchrow #2: niche manual auto_approve → false (Tier-1 skip)
    #   _earned_autonomy_check flow:
    #   fetchrow #3: earned_autonomy_enabled → true
    #   fetchrow #4: per-niche override key → missing
    #   fetchrow #5: global min_dispatches → '5'
    #   fetch #1:   last 5 dispatched rows → all dispatch_success=true
    niche_slug = "glad-labs"
    mock_db.fetchrow.side_effect = [
        {"niche_slug": niche_slug},                 # niche lookup
        {"value": "false"},                          # Tier-1 setting: off
        {"value": "true"},                           # earned_autonomy_enabled
        None,                                        # per-niche override: missing
        {"value": "5"},                              # global min_dispatches
    ]
    mock_db.fetch.return_value = [
        {"dispatch_success": True},
        {"dispatch_success": True},
        {"dispatch_success": True},
        {"dispatch_success": True},
        {"dispatch_success": True},
    ]

    with patch(
        "services.media_approval_service.emit_finding",
        return_value=None,
    ):
        result = await media_approval_service.record_pending(
            mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
        )

    assert result == "approved"
    insert_sql = mock_db.execute.call_args.args[0]
    assert "'approved'" in insert_sql
    decided_by_arg = mock_db.execute.call_args.args[3]
    assert decided_by_arg == f"auto:earned_autonomy:{niche_slug}"


async def test_earned_autonomy_stays_pending_when_insufficient_history(
    mock_db: MagicMock,
) -> None:
    """Only 3 dispatches when threshold=5 → stays pending (conservative)."""
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "glad-labs"},
        {"value": "false"},   # Tier-1 off
        {"value": "true"},    # earned_autonomy_enabled
        None,                 # no per-niche override
        {"value": "5"},       # min_dispatches = 5
    ]
    mock_db.fetch.return_value = [
        {"dispatch_success": True},
        {"dispatch_success": True},
        {"dispatch_success": True},
    ]  # only 3 — not enough

    result = await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    )

    assert result == "pending"


async def test_earned_autonomy_stays_pending_when_any_failure_in_history(
    mock_db: MagicMock,
) -> None:
    """One failed dispatch in the last N breaks the streak → stays pending."""
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "glad-labs"},
        {"value": "false"},
        {"value": "true"},
        None,
        {"value": "3"},  # threshold = 3
    ]
    mock_db.fetch.return_value = [
        {"dispatch_success": True},
        {"dispatch_success": False},  # failure breaks streak
        {"dispatch_success": True},
    ]

    result = await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "video",
    )

    assert result == "pending"


async def test_earned_autonomy_disabled_by_master_switch(
    mock_db: MagicMock,
) -> None:
    """Master switch off → skip even with perfect dispatch history."""
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "glad-labs"},
        {"value": "false"},   # Tier-1 off
        {"value": "false"},   # earned_autonomy_enabled = false
    ]
    mock_db.fetch.return_value = []  # should never be called

    result = await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    )

    assert result == "pending"
    mock_db.fetch.assert_not_called()


async def test_earned_autonomy_skipped_when_no_niche(mock_db: MagicMock) -> None:
    """No niche slug → Tier-2 is skipped entirely, no extra DB calls."""
    mock_db.fetchrow.return_value = None  # niche lookup: no row

    result = await media_approval_service.record_pending(
        mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
    )

    assert result == "pending"
    # Only the niche-lookup fetchrow should have been called.
    assert mock_db.fetchrow.call_count == 1
    mock_db.fetch.assert_not_called()


async def test_earned_autonomy_per_niche_threshold_override(
    mock_db: MagicMock,
) -> None:
    """Per-niche threshold override takes precedence over global value."""
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "gaming"},
        {"value": "false"},  # Tier-1 off
        {"value": "true"},   # earned_autonomy_enabled
        {"value": "3"},      # per-niche override: min_dispatches = 3
        # global default should NOT be queried (override took precedence)
    ]
    mock_db.fetch.return_value = [
        {"dispatch_success": True},
        {"dispatch_success": True},
        {"dispatch_success": True},
    ]  # exactly 3 — meets per-niche threshold

    with patch(
        "services.media_approval_service.emit_finding",
        return_value=None,
    ):
        result = await media_approval_service.record_pending(
            mock_db, "00000000-0000-0000-0000-000000000001", "video",
        )

    assert result == "approved"
    # Global default query (5th fetchrow) must NOT have been called.
    assert mock_db.fetchrow.call_count == 4


async def test_earned_autonomy_emit_finding_called_on_grant(
    mock_db: MagicMock,
) -> None:
    """On Tier-2 grant, emit_finding is called with kind='media_earned_autonomy_granted'."""
    mock_db.fetchrow.side_effect = [
        {"niche_slug": "gaming"},
        {"value": "false"},
        {"value": "true"},
        None,
        {"value": "2"},
    ]
    mock_db.fetch.return_value = [
        {"dispatch_success": True},
        {"dispatch_success": True},
    ]

    captured: list = []

    def fake_emit_finding(**kwargs):
        captured.append(kwargs)

    with patch(
        "services.media_approval_service.emit_finding",
        side_effect=fake_emit_finding,
    ):
        await media_approval_service.record_pending(
            mock_db, "00000000-0000-0000-0000-000000000001", "video",
        )

    assert len(captured) == 1
    assert captured[0]["kind"] == "media_earned_autonomy_granted"
    assert captured[0]["severity"] == "info"
    assert "gaming" in captured[0]["title"]


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


# ---------------------------------------------------------------------------
# record_dispatched — dispatch tracking (poindexter#558)
# ---------------------------------------------------------------------------


async def test_record_dispatched_success_sets_dispatched_at(
    mock_db: MagicMock,
) -> None:
    """Successful dispatch stamps dispatched_at via COALESCE (first-write wins)."""
    await media_approval_service.record_dispatched(
        mock_db, "00000000-0000-0000-0000-000000000001", "video", success=True,
    )
    sql = mock_db.execute.call_args.args[0]
    assert "dispatched_at" in sql
    assert "COALESCE" in sql
    assert "dispatch_success = true" in sql


async def test_record_dispatched_failure_does_not_set_dispatched_at(
    mock_db: MagicMock,
) -> None:
    """Failed dispatch must NOT stamp dispatched_at — row stays eligible for retry."""
    await media_approval_service.record_dispatched(
        mock_db, "00000000-0000-0000-0000-000000000001", "video", success=False,
    )
    sql = mock_db.execute.call_args.args[0]
    assert "dispatch_success = false" in sql
    # dispatched_at must NOT be written on failure
    assert "dispatched_at" not in sql


async def test_record_dispatched_rejects_unknown_medium(mock_db: MagicMock) -> None:
    with pytest.raises(media_approval_service.InvalidMediumError):
        await media_approval_service.record_dispatched(
            mock_db, "00000000-0000-0000-0000-000000000001", "reel", success=True,
        )


# ---------------------------------------------------------------------------
# list_approved_undispatched — dispatch-only pass query (poindexter#558)
# ---------------------------------------------------------------------------


async def test_list_approved_undispatched_returns_rows(mock_db: MagicMock) -> None:
    mock_db.fetch.return_value = [
        {
            "post_id": "abc",
            "medium": "video",
            "title": "GPU Frenzy",
            "content": "...",
            "excerpt": "Short",
            "seo_keywords": "gpu, nvidia",
            "slug": "gpu-frenzy",
        },
    ]
    rows = await media_approval_service.list_approved_undispatched(
        mock_db, medium="video",
    )
    assert len(rows) == 1
    assert rows[0]["medium"] == "video"


async def test_list_approved_undispatched_queries_approved_and_null_dispatched(
    mock_db: MagicMock,
) -> None:
    """SQL must select approved rows with dispatched_at IS NULL."""
    mock_db.fetch.return_value = []
    await media_approval_service.list_approved_undispatched(mock_db)
    sql = mock_db.fetch.call_args.args[0]
    assert "status = 'approved'" in sql
    assert "dispatched_at IS NULL" in sql


async def test_list_approved_undispatched_medium_filter_validates(
    mock_db: MagicMock,
) -> None:
    with pytest.raises(media_approval_service.InvalidMediumError):
        await media_approval_service.list_approved_undispatched(
            mock_db, medium="reel",
        )


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
