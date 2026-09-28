"""Tests for ``services.media_approval_service``.

The DB queries are exercised against a mocked asyncpg-Connection-like
object — same shape both the production code (a pool acquired conn)
and the backfill jobs (a raw asyncpg.connect Connection) pass in. The
service was designed to take either, so the tests cover the lower
common denominator: an object with async ``fetchrow`` / ``fetch`` /
``execute``.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import media_approval_service


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
        None,  # _evaluate_and_notify row re-read (insert raced → skip)
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
        None,  # _evaluate_and_notify row re-read (insert raced → skip)
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
# decide — rebuild the matching feed after every decision (self-healing
# propagation). An approve adds the item to the feed; rejecting an item that
# was already approved takes it back out (poindexter#1088).
# ---------------------------------------------------------------------------

_DECIDE_POST = "00000000-0000-0000-0000-000000000001"


def _rss(post_ids: list[str]) -> str:
    """A minimal RSS body with one ``<item>`` per post, as the feed route renders."""
    items = "".join(f"<item><guid>{p}</guid></item>" for p in post_ids)
    return f'<?xml version="1.0"?><rss><channel>{items}</channel></rss>'


async def test_decide_approve_rebuilds_matching_feed(mock_db: MagicMock) -> None:
    """On approve with a site_config, the matching R2 feed is rebuilt so the
    approval reaches Apple/Spotify/the video feed immediately, rather than
    waiting for the next event-coupled trigger or reconciliation cycle."""
    mock_db.fetchrow.return_value = {"status": "approved"}
    sc = MagicMock()
    with patch(
        "poindexter.services.media_feed_rebuild.rebuild_feed_for_medium",
        new=AsyncMock(),
    ) as rebuild:
        await media_approval_service.decide(
            mock_db, "00000000-0000-0000-0000-000000000001", "podcast",
            approved=True, decided_by="operator:cli", site_config=sc,
        )
    rebuild.assert_awaited_once_with(sc, "podcast")


@pytest.mark.parametrize(
    ("medium", "r2_path"),
    [("podcast", "podcast/feed.xml"), ("video", "video/feed.xml")],
)
async def test_decide_reject_rebuilds_matching_feed(
    mock_db: MagicMock, medium: str, r2_path: str,
) -> None:
    """A reject rebuilds the feed that listed the item (poindexter#1088).

    Rejecting an item that was already approved takes it out of the feed.
    Before the fix nothing rebuilt on reject: the item stayed live until the
    reconciler converged it, and that pass reported a missed upstream
    rebuild for a removal the operator made on purpose (7 CLI rejects of
    approved videos on 2026-09-28). A pending item's reject rebuilds too,
    re-uploading an identical feed, which is harmless.

    Runs through the real ``rebuild_feed_for_medium`` routing, so the
    assertion is on the feed object that gets rebuilt, not on a seam name.
    """
    from poindexter.services import media_feed_rebuild

    mock_db.fetchrow.return_value = {"status": "rejected"}
    sc = MagicMock()
    with patch.object(
        media_feed_rebuild, "_rebuild_feed", new=AsyncMock(),
    ) as rebuild:
        await media_approval_service.decide(
            mock_db, _DECIDE_POST, medium,
            approved=False, decided_by="operator:cli", site_config=sc,
        )
    assert mock_db.fetchrow.call_args.args[3] == "rejected"
    rebuild.assert_awaited_once()
    assert rebuild.await_args.kwargs["r2_path"] == r2_path


async def test_reject_burst_past_max_shrink_publishes_where_one_reconcile_refuses(
    mock_db: MagicMock,
) -> None:
    """Rejecting approved items takes each one out of the published feed,
    and a burst larger than ``media_feed_reconcile_max_shrink`` never trips
    the shrink guard, because every reject rebuilds and shrinks the feed by
    one.

    The counterfactual is why the reject rebuild has to be per-decision.
    Left to one reconciler pass, the same removals are a single shrink past
    the limit, which the guard can't tell from a collapsed render. It
    refuses, and the rejected items stay live.

    The feed route, the bucket and the upload are faked; the rebuild, the
    shrink guard and ``reconcile_feed`` are real.
    """
    from poindexter.services import media_feed_rebuild

    limit = 5
    live = [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 21)]
    eligible = list(live)  # what the feed route renders from the DB
    bucket = {"video/feed.xml": _rss(live)}  # what subscribers read

    async def _update(_sql, post_id, _medium, status, _by, _notes):
        # decide()'s UPDATE: a rejected item leaves the feed's eligible set.
        if status == "rejected":
            eligible.remove(post_id)
        return {"status": status}

    async def _render(_sc, _route):
        return media_feed_rebuild._FeedFetch(body=_rss(eligible), status_code=200)

    async def _read(_sc, r2_path):
        return bucket.get(r2_path)

    async def _upload(_sc, body, *, r2_path, label):
        bucket[r2_path] = body
        return True

    sc = MagicMock()
    sc.get.side_effect = lambda k, d=None: {
        "media_feed_reconcile_max_shrink": str(limit),
    }.get(k, d)
    mock_db.fetchrow = AsyncMock(side_effect=_update)
    rejected = live[: limit + 1]

    with patch.object(
        media_feed_rebuild, "_fetch_rendered_feed", new=_render,
    ), patch.object(
        media_feed_rebuild, "_read_published_feed", new=_read,
    ), patch.object(
        media_feed_rebuild, "_upload_feed", new=_upload,
    ), patch.object(
        media_feed_rebuild, "_emit_render_collapse_finding",
    ) as collapse:
        for post_id in rejected:
            await media_approval_service.decide(
                mock_db, post_id, "video",
                approved=False, decided_by="operator:cli", site_config=sc,
            )

        collapse.assert_not_called()
        published = bucket["video/feed.xml"]
        assert media_feed_rebuild.count_feed_items(published) == len(live) - len(rejected)
        assert not any(f"<guid>{p}</guid>" in published for p in rejected)

        # Counterfactual: the same six removals, left to one reconciler pass.
        bucket["video/feed.xml"] = _rss(live)
        res = await media_feed_rebuild.reconcile_feed(sc, "video")

    assert res.refused and not res.healed
    assert bucket["video/feed.xml"] == _rss(live)  # rejected items still live


@pytest.mark.parametrize("approved", [True, False])
async def test_decide_without_site_config_does_not_rebuild(
    mock_db: MagicMock, approved: bool,
) -> None:
    """Backcompat: callers that don't pass site_config (existing call sites,
    jobs, tests) still work — the rebuild is simply skipped, no error."""
    mock_db.fetchrow.return_value = {
        "status": "approved" if approved else "rejected",
    }
    with patch(
        "poindexter.services.media_feed_rebuild.rebuild_feed_for_medium",
        new=AsyncMock(),
    ) as rebuild:
        await media_approval_service.decide(
            mock_db, _DECIDE_POST, "podcast",
            approved=approved, decided_by="operator:cli",
        )
    rebuild.assert_not_awaited()


@pytest.mark.parametrize("approved", [True, False])
async def test_decide_rebuild_failure_is_non_fatal(
    mock_db: MagicMock, approved: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    """A feed-rebuild failure must NOT bubble out of decide(), on an approve
    or a reject: the decision is already committed to the DB, and the rebuild
    is additive self-healing that the reconciler backstops. The warning names
    the exception type, because ``str()`` of a bare timeout is empty."""
    status = "approved" if approved else "rejected"
    mock_db.fetchrow.return_value = {"status": status}
    sc = MagicMock()
    with patch(
        "poindexter.services.media_feed_rebuild.rebuild_feed_for_medium",
        new=AsyncMock(side_effect=TimeoutError()),
    ), caplog.at_level(logging.WARNING, logger=media_approval_service.__name__):
        # Must not raise.
        await media_approval_service.decide(
            mock_db, _DECIDE_POST, "video",
            approved=approved, decided_by="operator:cli", site_config=sc,
        )
    assert mock_db.fetchrow.call_args.args[3] == status
    assert any(
        f"feed rebuild after video was {status}" in r.message
        and "TimeoutError" in r.message
        for r in caplog.records
    )


@pytest.mark.parametrize("approved", [True, False])
async def test_decide_video_short_rebuilds_no_feed(
    mock_db: MagicMock, approved: bool,
) -> None:
    """Shorts go to YouTube Shorts and have no RSS surface, so a decision on
    one, approve or reject, rebuilds nothing. Runs through the real
    ``rebuild_feed_for_medium`` routing down to ``_rebuild_feed``, the one
    funnel both feeds share, so a short can't reach either feed or R2."""
    from poindexter.services import media_feed_rebuild

    mock_db.fetchrow.return_value = {
        "status": "approved" if approved else "rejected",
    }
    with patch.object(
        media_feed_rebuild, "_rebuild_feed", new=AsyncMock(),
    ) as rebuild:
        await media_approval_service.decide(
            mock_db, _DECIDE_POST, "video_short",
            approved=approved, decided_by="operator:cli", site_config=MagicMock(),
        )
    rebuild.assert_not_awaited()


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
            "quality_score": 85.0,  # 0-100 scale (spec 2026-07-09)
            "quality_signals": '{"duration_seconds": 240.0, "silence_ratio": 0.05, "file_size_bytes": 2400000}',
            "title": "Why Cofounders Burn Out",
            "slug": "why-cofounders-burn-out",
        },
    ]

    from unittest.mock import AsyncMock as _AsyncMock
    mock_notify = _AsyncMock()
    with patch(
        "poindexter.services.integrations.operator_notify.notify_operator",
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
    # 0-100 scale renders with no decimals (.0f), not the old .2f.
    assert "score=85" in msg
    assert "score=85.00" not in msg
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
        "poindexter.services.integrations.operator_notify.notify_operator",
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
        "poindexter.services.integrations.operator_notify.notify_operator",
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
        "poindexter.services.integrations.operator_notify.notify_operator",
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
        "poindexter.services.media_approval_service.emit_finding",
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
        None,                 # _evaluate_and_notify row re-read → skip
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
        None,  # _evaluate_and_notify row re-read → skip
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
        None,                 # _evaluate_and_notify row re-read → skip
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
    # Niche-lookup fetchrow + the _evaluate_and_notify row re-read (#816);
    # crucially NO Tier-2 settings queries in between.
    assert mock_db.fetchrow.call_count == 2
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
        # Tier 2 now also runs the quality eval, which re-reads the row.
        {"status": "approved", "decided_by": "auto:earned_autonomy:gaming",
         "quality_evaluated_at": None},
        None,   # notify: discord-enabled flag (absent → on)
        {"status": "approved", "quality_score": None, "quality_signals": "{}",
         "title": "T", "slug": "t"},
    ]
    mock_db.fetch.return_value = [
        {"dispatch_success": True},
        {"dispatch_success": True},
        {"dispatch_success": True},
    ]  # exactly 3 — meets per-niche threshold

    with patch(
        "poindexter.services.media_approval_service.emit_finding",
        return_value=None,
    ):
        result = await media_approval_service.record_pending(
            mock_db, "00000000-0000-0000-0000-000000000001", "video",
        )

    assert result == "approved"
    # The global default must NOT have been consulted once the per-niche
    # override matched. Asserted on the QUERY ARGS rather than a call count:
    # the count was 4 only because the eval used to be skipped on this tier,
    # so it silently encoded the Tier-2 grading hole as an invariant.
    queried_keys = [
        c.args[1] for c in mock_db.fetchrow.await_args_list if len(c.args) > 1
    ]
    assert "media.gate2.earned_autonomy_min_dispatches" not in queried_keys


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
        "poindexter.services.media_approval_service.emit_finding",
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
        "poindexter.services.integrations.operator_notify.notify_operator",
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
    # Dispatch on the query rather than on call ORDER: Tier 1 now also runs
    # the quality eval (auto-approve skips the operator, not the checks), so a
    # fixed side_effect list encodes the call count as an invariant and breaks
    # the moment the tier does more work.
    approved_row = {
        "status": "approved",
        "decided_by": "auto:niche.glad-labs",
        "quality_evaluated_at": None,
        "quality_score": None,
        "quality_signals": "{}",
        "title": "X",
        "slug": "x",
    }

    async def _fetchrow(sql, *args, **_kw):
        text = " ".join(str(sql).split())
        if "pipeline_tasks pt" in text:
            return {"niche_slug": "glad-labs"}
        if "FROM app_settings" in text:
            return {"value": "true"} if args and "auto_approve" in str(args[0]) else None
        if "media_approvals" in text:
            return approved_row
        return None

    mock_db.fetchrow.side_effect = _fetchrow

    status = await media_approval_service.record_pending(
        mock_db, "12345678-1234-1234-1234-123456789012", "podcast",
    )
    assert status == "approved"

    from unittest.mock import AsyncMock as _AsyncMock
    mock_notify = _AsyncMock()
    with patch(
        "poindexter.services.integrations.operator_notify.notify_operator",
        mock_notify,
    ):
        result = await media_approval_service.notify_pending_for_review(
            mock_db, "12345678-1234-1234-1234-123456789012", "podcast",
        )

    assert result is False
    mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# record_pending → Layer-1 eval reattachment (poindexter#816)
#
# The #648 eval chain died when its only callers (the backfill jobs) were
# retired in #1460 — media_approvals.quality_score sat NULL on every row and
# pending media seeded silently. record_pending now owns the eval at the
# seeding choke point so no future seeder can forget it again.
# ---------------------------------------------------------------------------

_POST = "12345678-1234-1234-1234-123456789012"


def _pending_row_side_effects(*, evaluated_at=None, status="pending") -> list:
    """fetchrow sequence for the no-niche Tier-3 path + the eval re-read."""
    return [
        None,  # niche lookup: no row → Tier 3
        {"status": status, "quality_evaluated_at": evaluated_at},
    ]


async def test_record_pending_with_file_path_runs_podcast_eval(
    mock_db: MagicMock,
) -> None:
    """Pending + file_path → evaluate_podcast runs on the asset (#816)."""
    mock_db.fetchrow.side_effect = _pending_row_side_effects()

    eval_podcast = AsyncMock()
    with patch(
        "poindexter.services.media_quality_service.evaluate_podcast", eval_podcast,
    ):
        status = await media_approval_service.record_pending(
            mock_db, _POST, "podcast", file_path="/data/media/pod.mp3",
        )

    assert status == "pending"
    eval_podcast.assert_awaited_once_with(
        mock_db, _POST, "/data/media/pod.mp3", site_config=None,
    )


@pytest.mark.parametrize("medium", ["video", "video_short"])
async def test_record_pending_with_file_path_runs_video_eval(
    mock_db: MagicMock, medium: str,
) -> None:
    """Video flavors route to evaluate_video with the medium threaded."""
    mock_db.fetchrow.side_effect = _pending_row_side_effects()

    eval_video = AsyncMock()
    with patch(
        "poindexter.services.media_quality_service.evaluate_video", eval_video,
    ):
        await media_approval_service.record_pending(
            mock_db, _POST, medium, file_path="/data/media/clip.mp4",
        )

    eval_video.assert_awaited_once_with(
        mock_db, _POST, "/data/media/clip.mp4", medium=medium, site_config=None,
    )


async def test_record_pending_threads_site_config_to_evaluator(
    mock_db: MagicMock,
) -> None:
    """A site_config handed to record_pending reaches the Layer-2 evaluator.

    Pins the Task-5 wiring: the master switch + thresholds Layer 2 reads
    all live on site_config, so it MUST be threaded from the job caller
    through record_pending → _evaluate_and_notify → evaluate_*.
    """
    mock_db.fetchrow.side_effect = _pending_row_side_effects()
    sentinel = object()

    eval_video = AsyncMock()
    with patch(
        "poindexter.services.media_quality_service.evaluate_video", eval_video,
    ):
        await media_approval_service.record_pending(
            mock_db, _POST, "video", file_path="/data/media/clip.mp4",
            site_config=sentinel,
        )

    assert eval_video.await_args.kwargs["site_config"] is sentinel


async def test_record_pending_without_file_path_still_pings_operator(
    mock_db: MagicMock,
) -> None:
    """No asset path (e.g. reconciliation R2-only stamp) → the review ping
    still fires so a pending medium is never seeded silently (the 6.4-day
    unnoticed-pending failure mode)."""
    mock_db.fetchrow.side_effect = _pending_row_side_effects()

    notify = AsyncMock(return_value=True)
    with patch.object(
        media_approval_service, "notify_pending_for_review", notify,
    ):
        status = await media_approval_service.record_pending(
            mock_db, _POST, "podcast",
        )

    assert status == "pending"
    notify.assert_awaited_once_with(mock_db, _POST, "podcast")


async def test_record_pending_skips_eval_when_already_evaluated(
    mock_db: MagicMock,
) -> None:
    """quality_evaluated_at set → idempotent re-seed must not re-run
    ffprobe or re-ping Discord (scheduled jobs re-seed every cycle)."""
    mock_db.fetchrow.side_effect = _pending_row_side_effects(
        evaluated_at="2026-07-02T00:00:00Z",
    )

    eval_podcast = AsyncMock()
    notify = AsyncMock()
    with patch(
        "poindexter.services.media_quality_service.evaluate_podcast", eval_podcast,
    ), patch.object(
        media_approval_service, "notify_pending_for_review", notify,
    ):
        await media_approval_service.record_pending(
            mock_db, _POST, "podcast", file_path="/data/media/pod.mp3",
        )

    eval_podcast.assert_not_awaited()
    notify.assert_not_awaited()


async def test_record_pending_skips_eval_when_prior_decision_holds(
    mock_db: MagicMock,
) -> None:
    """ON CONFLICT DO NOTHING kept an operator-approved row → the eval must
    NOT run (its auto-reject path would clobber the human decision)."""
    mock_db.fetchrow.side_effect = _pending_row_side_effects(status="approved")

    eval_podcast = AsyncMock()
    with patch(
        "poindexter.services.media_quality_service.evaluate_podcast", eval_podcast,
    ):
        await media_approval_service.record_pending(
            mock_db, _POST, "podcast", file_path="/data/media/pod.mp3",
        )

    eval_podcast.assert_not_awaited()


async def test_record_pending_eval_failure_never_fails_the_seed(
    mock_db: MagicMock,
) -> None:
    """The eval is additive — an ffprobe/DB explosion inside it must not
    bubble out of record_pending (the gate row is already inserted)."""
    mock_db.fetchrow.side_effect = _pending_row_side_effects()

    with patch(
        "poindexter.services.media_quality_service.evaluate_podcast",
        AsyncMock(side_effect=RuntimeError("ffprobe exploded")),
    ):
        status = await media_approval_service.record_pending(
            mock_db, _POST, "podcast", file_path="/data/media/pod.mp3",
        )

    assert status == "pending"


# ---------------------------------------------------------------------------
# The quality eval is tier-independent
#
# _evaluate_and_notify used to be called only from the Tier-3 branch, so
# flipping on a niche auto_approve or earned autonomy silently turned OFF
# grading for that niche rather than only skipping the human step: the medium
# shipped with no Layer-1 checks, no semantic score, and quality_score NULL.
# Auto-approve is a statement about trusting content judgment, not a licence
# to ship a 0-byte render.
# ---------------------------------------------------------------------------


def _tier1_db(mock_db: MagicMock, *, evaluated_at=None) -> MagicMock:
    """Query-dispatched stub for the niche auto-approve (Tier-1) path."""
    async def _fetchrow(sql, *args, **_kw):
        text = " ".join(str(sql).split())
        if "pipeline_tasks pt" in text:
            return {"niche_slug": "glad-labs"}
        if "FROM app_settings" in text:
            return {"value": "true"} if args and "auto_approve" in str(args[0]) else None
        if "media_approvals" in text:
            return {
                "status": "approved",
                "decided_by": "auto:niche.glad-labs",
                "quality_evaluated_at": evaluated_at,
            }
        return None

    mock_db.fetchrow.side_effect = _fetchrow
    return mock_db


async def test_tier1_auto_approve_still_runs_the_quality_eval(
    mock_db: MagicMock,
) -> None:
    """Niche auto-approve skips the operator, NOT the checks."""
    _tier1_db(mock_db)

    eval_podcast = AsyncMock()
    with patch(
        "poindexter.services.media_quality_service.evaluate_podcast", eval_podcast,
    ):
        status = await media_approval_service.record_pending(
            mock_db, _POST, "podcast", file_path="/data/media/pod.mp3",
        )

    assert status == "approved"
    eval_podcast.assert_awaited_once_with(
        mock_db, _POST, "/data/media/pod.mp3", site_config=None,
    )


async def test_tier2_earned_autonomy_still_runs_the_quality_eval(
    mock_db: MagicMock,
) -> None:
    """Earned autonomy skips the operator, NOT the checks."""
    async def _fetchrow(sql, *args, **_kw):
        text = " ".join(str(sql).split())
        if "pipeline_tasks pt" in text:
            return {"niche_slug": "gaming"}
        if "FROM app_settings" in text:
            # The master switch and the global threshold inline their key in
            # the SQL; the per-niche keys arrive as a bind arg.
            key = str(args[0]) if args else text
            if "auto_approve" in key:
                return {"value": "false"}          # Tier 1 off
            if "earned_autonomy_enabled" in key:
                return {"value": "true"}
            if "min_dispatches" in key:
                return {"value": "3"}
            return None
        if "media_approvals" in text:
            return {
                "status": "approved",
                "decided_by": "auto:earned_autonomy:gaming",
                "quality_evaluated_at": None,
            }
        return None

    mock_db.fetchrow.side_effect = _fetchrow
    mock_db.fetch.return_value = [{"dispatch_success": True}] * 3

    eval_video = AsyncMock()
    with patch(
        "poindexter.services.media_approval_service.emit_finding", return_value=None,
    ), patch(
        "poindexter.services.media_quality_service.evaluate_video", eval_video,
    ):
        status = await media_approval_service.record_pending(
            mock_db, _POST, "video", file_path="/data/media/clip.mp4",
        )

    assert status == "approved"
    eval_video.assert_awaited_once_with(
        mock_db, _POST, "/data/media/clip.mp4", medium="video", site_config=None,
    )


async def test_auto_approved_row_is_never_pinged_even_though_it_is_graded(
    mock_db: MagicMock,
) -> None:
    """Grading and pinging are separate: only a PENDING row pings Discord."""
    _tier1_db(mock_db)

    notify = AsyncMock(return_value=True)
    with patch(
        "poindexter.services.media_quality_service.evaluate_podcast", AsyncMock(),
    ), patch.object(media_approval_service, "notify_pending_for_review", notify):
        await media_approval_service.record_pending(
            mock_db, _POST, "podcast", file_path="/data/media/pod.mp3",
        )

    notify.assert_not_awaited()


async def test_eval_skips_a_row_a_human_already_decided(
    mock_db: MagicMock,
) -> None:
    """An operator's call is final — never re-judged, never clobbered."""
    mock_db.fetchrow.side_effect = [
        None,  # no niche → Tier 3
        {"status": "rejected", "decided_by": "operator",
         "quality_evaluated_at": None},
    ]

    eval_podcast = AsyncMock()
    with patch(
        "poindexter.services.media_quality_service.evaluate_podcast", eval_podcast,
    ):
        await media_approval_service.record_pending(
            mock_db, _POST, "podcast", file_path="/data/media/pod.mp3",
        )

    eval_podcast.assert_not_awaited()


async def test_eval_still_runs_on_a_row_an_AUTO_tier_decided(
    mock_db: MagicMock,
) -> None:
    """An auto decision is exactly what the eval underwrites, so it stays
    eligible — otherwise a re-seed of an auto-approved row could never pick
    up the grading it was skipped for."""
    mock_db.fetchrow.side_effect = [
        None,  # no niche → Tier 3 insert (ON CONFLICT DO NOTHING keeps the row)
        {"status": "approved", "decided_by": "auto:niche.glad-labs",
         "quality_evaluated_at": None},
    ]

    eval_podcast = AsyncMock()
    with patch(
        "poindexter.services.media_quality_service.evaluate_podcast", eval_podcast,
    ):
        await media_approval_service.record_pending(
            mock_db, _POST, "podcast", file_path="/data/media/pod.mp3",
        )

    eval_podcast.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_preview_media — the draft preview's media (Glad-Labs/poindexter#1089)
#
# The SQL (both sides of the post <-> task seam, the approval join) runs on
# the real schema in tests/integration_db/test_preview_media_sql.py. These pin
# what the Python does with the rows it gets back.
# ---------------------------------------------------------------------------

_PREVIEW_SC = MagicMock()
_PREVIEW_SC.get.side_effect = lambda key, default="": {
    "storage_public_url": "https://cdn.example/",
    "podcast_cdn_version": "v7",
}.get(key, default)


def _asset_row(kind: str, *, post_id: str | None = "p1", url: str | None = None,
               approval: str | None = "approved") -> dict:
    return {"type": kind, "post_id": post_id, "url": url, "approval": approval}


async def test_preview_media_needs_a_key(mock_db: MagicMock) -> None:
    assert await media_approval_service.get_preview_media(
        mock_db, site_config=_PREVIEW_SC,
    ) == {}
    mock_db.fetch.assert_not_awaited()


async def test_preview_media_binds_post_then_task(mock_db: MagicMock) -> None:
    await media_approval_service.get_preview_media(
        mock_db, site_config=_PREVIEW_SC, task_id="t1",
    )
    sql, post_arg, task_arg = mock_db.fetch.await_args.args
    assert (post_arg, task_arg) == (None, "t1")
    # Both preview branches depend on following the canonical seam both ways.
    assert sql.count("metadata->>'pipeline_task_id'") == 2
    assert "LEFT JOIN media_approvals" in sql
    assert "'video_short'" not in sql


async def test_preview_media_plays_the_stamped_url(mock_db: MagicMock) -> None:
    mock_db.fetch.return_value = [
        _asset_row("video", url="https://cdn.example/video/p1.mp4"),
    ]
    media = await media_approval_service.get_preview_media(
        mock_db, site_config=_PREVIEW_SC, post_id="p1",
    )
    assert media == {"video": "https://cdn.example/video/p1.mp4"}


async def test_preview_media_falls_back_to_the_delivery_keys(mock_db: MagicMock) -> None:
    """An approved asset delivered without a stamped URL plays from the key the
    feed advertises: the podcast's carries podcast_cdn_version."""
    mock_db.fetch.return_value = [_asset_row("podcast"), _asset_row("video")]
    media = await media_approval_service.get_preview_media(
        mock_db, site_config=_PREVIEW_SC, post_id="p1",
    )
    assert media == {
        "podcast": "https://cdn.example/podcast/v7/p1.mp3",
        "video": "https://cdn.example/video/p1.mp4",
    }


async def test_preview_media_never_links_unapproved_media(mock_db: MagicMock) -> None:
    """Pending, rejected and unlinked (no approval row yet) media is shown as
    present but gets no URL, stamped or not."""
    mock_db.fetch.return_value = [
        _asset_row("podcast", approval="pending"),
        _asset_row("video", url="https://cdn.example/video/p1.mp4", approval="rejected"),
    ]
    media = await media_approval_service.get_preview_media(
        mock_db, site_config=_PREVIEW_SC, task_id="t1",
    )
    assert media == {"podcast": None, "video": None}

    mock_db.fetch.return_value = [_asset_row("video", post_id=None, approval=None)]
    media = await media_approval_service.get_preview_media(
        mock_db, site_config=_PREVIEW_SC, task_id="t1",
    )
    assert media == {"video": None}


async def test_preview_media_takes_the_newest_approved_asset(mock_db: MagicMock) -> None:
    """Rows arrive newest first. A newer render still awaiting approval must not
    hide the approved one the feed publishes."""
    mock_db.fetch.return_value = [
        _asset_row("podcast", post_id="p1", approval="pending"),
        _asset_row("podcast", post_id="p1", url="https://cdn.example/new.mp3"),
        _asset_row("podcast", post_id="p1", url="https://cdn.example/old.mp3"),
    ]
    media = await media_approval_service.get_preview_media(
        mock_db, site_config=_PREVIEW_SC, post_id="p1",
    )
    assert media == {"podcast": "https://cdn.example/new.mp3"}


async def test_preview_media_does_not_guess_without_a_public_base(
    mock_db: MagicMock,
) -> None:
    """With no storage_public_url there is no key to fall back to: omit the
    link rather than build a broken one. A stamped URL still plays."""
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": default
    mock_db.fetch.return_value = [
        _asset_row("podcast"),
        _asset_row("video", url="https://cdn.example/video/p1.mp4"),
    ]
    media = await media_approval_service.get_preview_media(
        mock_db, site_config=sc, post_id="p1",
    )
    assert media == {"podcast": None, "video": "https://cdn.example/video/p1.mp4"}
