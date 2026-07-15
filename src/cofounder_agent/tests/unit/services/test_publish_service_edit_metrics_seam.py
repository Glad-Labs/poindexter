"""Regression tests for the edit-distance metrics seam (approve time).

``published_post_edit_metrics`` froze after 2026-06-23T18:16Z: the phase-13
``_record_edit_distance_metrics`` call sat on the immediate-publish TAIL of
``publish_post_from_task``, but the operator's default flow became
approve→stage→promote around 2026-06-24. Both of that flow's seams return
before the tail — the ``stage_only`` short-circuit at post creation, and the
``_promote_or_skip_existing`` promote-in-place short-circuit at go-live — so
the auto-publish gate's edit-distance training signal
(``feedback_auto_publish_requires_edit_distance_track_record``) starved with
no log line at all. Nothing raised; the code was simply unreachable. Verified
via Loki: last success line 2026-06-23T18:16:15Z (the last immediate
publish), zero ``record_post_approve_metrics failed`` warnings since, daily
``Staging approved task`` + ``Promoted existing approved post`` lines
throughout. Same control-flow class as the poindexter#834 newsletter gap.

The fix records the metric at the APPROVE seam — before the ``stage_only``
short-circuit — which covers both tails exactly once per approve (re-stage
and promote both short-circuit in phase 2 and never reach it again), and
promotes the failure swallow from a DEBUG log to WARNING + ``emit_finding``
per ``feedback_self_heal_not_suppress``.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig

# #272 Phase-2g: publish_post_from_task requires an injected site_config.
_TEST_SC = SiteConfig(initial_config={"site_url": "https://www.test-site.example.com"})

_PRE_APPROVE = "## Heading\n\nPipeline draft body before the operator touched it."
_POST_APPROVE = "## Heading\n\nOperator-edited body that actually shipped."


def _make_task() -> dict[str, Any]:
    return {
        "task_id": "11111111-1111-1111-1111-111111111111",
        "topic": "Test post for edit-metrics seam",
        "task_metadata": {
            "content": _POST_APPROVE,
            "pre_approve_content": _PRE_APPROVE,
            "seo_description": "Test excerpt.",
            "seo_keywords": ["test"],
            "featured_image_url": "https://example.com/image.jpg",
        },
        "result": {},
        "primary_keyword": "test",
        "niche_slug": "dev_diary",
    }


def _make_db_service() -> Any:
    """DatabaseService stub — same shape as test_publish_service_stage_only."""
    db = MagicMock()
    db.cloud_pool = None
    db.create_post = AsyncMock(
        side_effect=lambda data: MagicMock(id="22222222-2222-2222-2222-222222222222"),
    )
    db.update_task_status = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="UPDATE 0")
    pool.fetchval = AsyncMock(return_value=None)
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 0")
    conn.fetchval = AsyncMock(return_value=None)
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=conn)
    txn_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn_cm)
    acq_cm = MagicMock()
    acq_cm.__aenter__ = AsyncMock(return_value=conn)
    acq_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acq_cm)
    db.pool = pool
    db._test_conn = conn
    return db


def _existing_post(status: str) -> dict[str, Any]:
    return {
        "id": "33333333-3333-3333-3333-333333333333",
        "slug": "test-post-for-edit-metrics-seam-11111111",
        "title": "Test post for edit-metrics seam",
        "status": status,
        "excerpt": "The excerpt",
    }


@pytest.mark.asyncio
async def test_stage_only_records_edit_metrics_at_approve_seam() -> None:
    """THE 2026-06-24→07-11 freeze: approve (stage_only=True) is the moment
    the operator's edit distance is known, so the metric row MUST be written
    on the staging path — the old phase-13 tail call was unreachable from
    the stage_only short-circuit and the table silently froze."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    recorder = AsyncMock(return_value=True)

    with patch("modules.content.api.record_post_approve_metrics", recorder), \
         patch("services.publish_service._spawn_background"), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False):
        result = await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            trigger_revalidation=False,
            queue_social=False,
            site_config=_TEST_SC,
        )

    assert result.success, f"stage_only path failed: {result.error}"
    assert result.staged is True
    assert recorder.await_count == 1, (
        "Approve (stage_only) must record exactly one edit-distance row — "
        "this is the auto-publish gate's training signal and it froze from "
        "2026-06-24 to 2026-07-11 because the stage path skipped it."
    )
    kwargs = recorder.await_args.kwargs
    assert kwargs["task_id"] == "11111111-1111-1111-1111-111111111111"
    assert kwargs["pre_approve_content"] == _PRE_APPROVE
    assert kwargs["post_approve_content"] == _POST_APPROVE
    assert kwargs["niche_slug"] == "dev_diary"
    assert kwargs["approver"] == "operator-test"
    # The seam tags itself so prod rows distinguish stage-time approves
    # from immediate publishes.
    assert "stage_only" in kwargs["approve_method"]


@pytest.mark.asyncio
async def test_immediate_publish_records_edit_metrics() -> None:
    """The pre-2026-06-24 behavior must survive the seam move: an immediate
    publish (stage_only=False, no existing post) still records exactly one
    row."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    recorder = AsyncMock(return_value=True)

    with patch("modules.content.api.record_post_approve_metrics", recorder), \
         patch("services.publish_service._spawn_background"), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False), \
         patch("services.static_export_service.export_post", new=AsyncMock(return_value=True)), \
         patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()):
        result = await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=False,
            trigger_revalidation=False,
            queue_social=False,
            site_config=_TEST_SC,
        )

    assert result.success, f"immediate publish failed: {result.error}"
    assert recorder.await_count == 1
    kwargs = recorder.await_args.kwargs
    assert kwargs["pre_approve_content"] == _PRE_APPROVE
    assert kwargs["post_approve_content"] == _POST_APPROVE
    assert "stage_only" not in kwargs["approve_method"]


@pytest.mark.asyncio
async def test_promote_existing_approved_does_not_record_again() -> None:
    """One row per approve: the go-live promote of an already-staged post
    (approve already recorded the metric) must NOT write a second row —
    the operator made their edits at approve time, not at promote time."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    db.pool.fetchrow = AsyncMock(return_value=_existing_post("approved"))
    db.pool.execute = AsyncMock(return_value="UPDATE 1")
    db._test_conn.execute = AsyncMock(return_value="UPDATE 1")
    recorder = AsyncMock(return_value=True)

    with patch("modules.content.api.record_post_approve_metrics", recorder), \
         patch("services.publish_service._spawn_background"), \
         patch("services.static_export_service.export_post", new=AsyncMock(return_value=True)):
        result = await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=False,
            draft_mode=False,
            site_config=_TEST_SC,
        )

    assert result.success is True
    assert recorder.await_count == 0, (
        "Promote-in-place fired the recorder — that double-counts the "
        "approve and pollutes the trailing-clean-runs window with "
        "duplicate rows."
    )


@pytest.mark.asyncio
async def test_restage_does_not_record_again() -> None:
    """A second approve of the same task (stage_only twice) short-circuits
    as a no-op in phase 2 and must not write a duplicate metric row."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    db.pool.fetchrow = AsyncMock(return_value=_existing_post("approved"))
    recorder = AsyncMock(return_value=True)

    with patch("modules.content.api.record_post_approve_metrics", recorder), \
         patch("services.publish_service._spawn_background"):
        result = await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            draft_mode=False,
            site_config=_TEST_SC,
        )

    assert result.success is True
    assert recorder.await_count == 0


@pytest.mark.asyncio
async def test_edit_metrics_failure_is_warning_plus_finding_not_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """feedback_self_heal_not_suppress: a recorder failure must never fail
    the publish, but it must be LOUD — WARNING log + emit_finding — not the
    DEBUG-level swallow that hid the 17-day freeze."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    recorder = AsyncMock(side_effect=RuntimeError("simulated insert failure"))
    findings: list[dict[str, Any]] = []

    def _capture_finding(**kwargs: Any) -> None:
        findings.append(kwargs)

    with patch("modules.content.api.record_post_approve_metrics", recorder), \
         patch("utils.findings.emit_finding", side_effect=_capture_finding), \
         patch("services.publish_service._spawn_background"), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False), \
         caplog.at_level(logging.WARNING, logger="services.publish_service"):
        result = await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            trigger_revalidation=False,
            queue_social=False,
            site_config=_TEST_SC,
        )

    assert result.success is True, "metrics failure must never fail the publish"
    assert result.staged is True
    assert any(
        "edit-distance metrics failed" in rec.message for rec in caplog.records
    ), "recorder failure must log at WARNING, not DEBUG"
    assert len(findings) == 1, "recorder failure must emit a finding"
    assert findings[0]["kind"] == "edit_metrics_record_failed"
    assert findings[0]["severity"] == "warn"


@pytest.mark.asyncio
async def test_record_post_approve_metrics_pool_none_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The pool-None early return used to be completely silent — not even a
    debug line. It must warn: a caller wired without a pool starves the gate
    signal invisibly."""
    from modules.content.auto_publish_gate import record_post_approve_metrics

    with caplog.at_level(
        logging.WARNING, logger="modules.content.auto_publish_gate"
    ):
        ok = await record_post_approve_metrics(
            None,
            task_id="99999999-9999-9999-9999-999999999999",
            pre_approve_content="a",
            post_approve_content="b",
        )

    assert ok is False
    assert any("pool" in rec.message.lower() for rec in caplog.records), (
        "pool-None skip must log a warning naming the missing pool"
    )
