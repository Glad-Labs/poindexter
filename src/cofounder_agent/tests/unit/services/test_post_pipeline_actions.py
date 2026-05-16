"""Regression tests for Glad-Labs/poindexter#478.

Critical Prefect-cutover regression discovered 2026-05-11 19:00 UTC:
the 130-line post-pipeline-success block in
``services/task_executor.py::_process_loop`` (lines 678-810 before
extraction) ran ONLY in the legacy orchestrator. The Prefect Phase-0
cutover (``use_prefect_orchestration=true``, live in prod since
2026-05-10 22:06Z) short-circuits ``_process_loop`` before any of
those four behaviours could fire:

1. ``task.completed`` webhook never emits
2. Auto-curator (auto-reject below ``min_curation_score``) never fires
3. Auto-publish (when ``require_human_approval=false`` AND
   ``quality_score >= auto_threshold``) never fires
4. Operator Discord/Telegram notification + preview-screenshot QA
   never fire

Architectural mirror of poindexter#473 (pipeline_versions writes) and
poindexter#477 (subprocess DI wiring) — same class of bug, different
symptom.

Fix: extract the inline block into
``services.post_pipeline_actions.run_post_pipeline_actions``. Both
``TaskExecutor`` and the Prefect ``content_generation_flow`` now
delegate to the same helper so the four behaviours survive both
orchestrators.

These tests pin the contract:

- Webhook fires on every successful task
- Auto-curator rejects below threshold (+ writes
  ``pipeline_gate_history`` + flips ``model_performance`` +
  emits ``task.auto_rejected`` webhook)
- DETERMINISTIC_COMPOSITOR niches BYPASS the auto-curator
- Auto-publish fires when threshold met AND
  ``require_human_approval=false``
- Auto-publish DOESN'T fire when ``require_human_approval=true``
- Operator notification reads preview_token from
  ``pipeline_versions.stage_data->metadata`` (PR #368) and builds
  the preview URL from the wired SiteConfig
- Missing preview_token degrades gracefully (notification still
  fires, link omitted)
- Per-action failure (webhook fails) doesn't block subsequent
  actions
- Source-level guards: ``task_executor`` AND
  ``services.flows.content_generation`` both delegate to the helper
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*, fetchval_return=None, execute_return=None):
    """asyncpg pool double with both ``pool.acquire()`` + bare ``execute``.

    The post-pipeline helper uses pool.execute() directly for the
    pipeline_gate_history insert and pool.fetchval() for the
    preview_token lookup, but acquires a connection for the
    DETERMINISTIC_COMPOSITOR writer_rag_mode check.
    """
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.execute = AsyncMock(return_value=execute_return)

    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.execute = AsyncMock(return_value=execute_return)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def _make_db_service(pool=None, cloud_pool=None):
    """Database service double covering every surface the helper touches."""
    db = MagicMock()
    db.pool = pool
    db.cloud_pool = cloud_pool or pool
    db.update_task = AsyncMock()
    db.mark_model_performance_outcome = AsyncMock()
    db.get_setting_value = AsyncMock(return_value=None)
    return db


def _make_site_config(values=None):
    """SiteConfig double whose .get() returns from a backing dict."""
    backing = values or {}
    site = MagicMock()
    site.get = MagicMock(side_effect=lambda key, default=None: backing.get(key, default))
    return site


def _make_settings_service(values=None):
    """Settings-service double matching the ``get_setting`` async contract."""
    backing = values or {}
    svc = MagicMock()

    async def _get_setting(key, default=None):
        return backing.get(key, default)

    svc.get_setting = _get_setting
    return svc


def _result(score=80, status="awaiting_approval"):
    return {"quality_score": score, "status": status}


# ---------------------------------------------------------------------------
# task.completed webhook — fires on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebhookEmits:
    """The task.completed webhook fires on every successful pipeline run."""

    @pytest.mark.asyncio
    async def test_emits_task_completed_on_success(self):
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool()
        db = _make_db_service(pool=pool)
        site = _make_site_config(values={"preview_base_url": "http://preview/"})
        settings = _make_settings_service(
            values={
                "min_curation_score": "70",
                "require_human_approval": "true",
                "qa_preview_screenshot_enabled": "false",
            },
        )

        emit_mock = AsyncMock()
        with patch(
            "services.post_pipeline_actions.emit_webhook_event", emit_mock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            new_callable=AsyncMock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-webhook-1",
                topic="Why my GPU is on fire",
                result=_result(score=85),
                site_config=site,
                settings_service=settings,
            )

        events = [call.args[1] for call in emit_mock.call_args_list]
        assert "task.completed" in events, (
            f"task.completed never emitted; events seen: {events}"
        )
        # The payload carries the operator-visible fields the dashboard
        # + downstream consumers read.
        completed_call = next(
            c for c in emit_mock.call_args_list if c.args[1] == "task.completed"
        )
        payload = completed_call.args[2]
        assert payload["task_id"] == "t-webhook-1"
        assert payload["topic"] == "Why my GPU is on fire"
        assert payload["quality_score"] == 85.0
        assert payload["status"] == "awaiting_approval"


# ---------------------------------------------------------------------------
# Auto-curator — rejects below threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAutoCurator:
    """The auto-curator gate rejects sub-threshold posts before the
    operator sees them, and records the rejection on
    ``pipeline_gate_history`` so the dashboard shows it."""

    @pytest.mark.asyncio
    async def test_rejects_when_score_below_threshold(self):
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, conn = _make_pool(fetchval_return=None)  # writer_rag_mode → not deterministic
        db = _make_db_service(pool=pool)
        site = _make_site_config()
        settings = _make_settings_service(
            values={"min_curation_score": "70"},
        )

        emit_mock = AsyncMock()
        notify_mock = AsyncMock()
        with patch(
            "services.post_pipeline_actions.emit_webhook_event", emit_mock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-reject-1",
                topic="A mediocre post",
                result=_result(score=55),
                site_config=site,
                settings_service=settings,
            )

        # update_task rejected the task
        db.update_task.assert_any_await("t-reject-1", {"status": "rejected"})
        # pipeline_gate_history insert fired
        gate_insert_calls = [
            c for c in pool.execute.call_args_list
            if "pipeline_gate_history" in str(c.args[0])
        ]
        assert len(gate_insert_calls) == 1, (
            f"pipeline_gate_history insert should fire exactly once on "
            f"auto-reject; got {len(gate_insert_calls)}"
        )
        gate_args = gate_insert_calls[0].args
        assert gate_args[1] == "t-reject-1"
        assert gate_args[2] == "auto_curator"
        assert gate_args[3] == "rejected"
        # model_performance flip fired
        db.mark_model_performance_outcome.assert_awaited_once_with(
            "t-reject-1", human_approved=False,
        )
        # task.auto_rejected webhook emitted
        events = [c.args[1] for c in emit_mock.call_args_list]
        assert "task.auto_rejected" in events
        # Notification path NOT reached (early return)
        notify_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_reject_when_score_at_threshold(self):
        """``0 < score < min`` is strict — equal to min stays alive."""
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool(fetchval_return=None)
        db = _make_db_service(pool=pool)
        site = _make_site_config()
        settings = _make_settings_service(values={"min_curation_score": "70"})

        with patch(
            "services.post_pipeline_actions.emit_webhook_event",
            new_callable=AsyncMock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            new_callable=AsyncMock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-edge",
                topic="Edge case",
                result=_result(score=70),
                site_config=site,
                settings_service=settings,
            )
        # update_task with status='rejected' must NOT have fired
        reject_calls = [
            c for c in db.update_task.call_args_list
            if len(c.args) >= 2 and c.args[1].get("status") == "rejected"
        ]
        assert len(reject_calls) == 0, (
            f"score == threshold should not auto-reject, but did: {reject_calls}"
        )

    @pytest.mark.asyncio
    async def test_no_reject_when_score_zero(self):
        """``quality_score=0`` is the fallback when no QA ran; the
        ``0 <`` lower bound prevents auto-rejecting those."""
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool(fetchval_return=None)
        db = _make_db_service(pool=pool)
        site = _make_site_config()
        settings = _make_settings_service(values={"min_curation_score": "70"})

        with patch(
            "services.post_pipeline_actions.emit_webhook_event",
            new_callable=AsyncMock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            new_callable=AsyncMock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-zero",
                topic="Zero",
                result=_result(score=0),
                site_config=site,
                settings_service=settings,
            )

        reject_calls = [
            c for c in db.update_task.call_args_list
            if len(c.args) >= 2 and c.args[1].get("status") == "rejected"
        ]
        assert len(reject_calls) == 0

    @pytest.mark.asyncio
    async def test_bypasses_deterministic_compositor(self):
        """``DETERMINISTIC_COMPOSITOR`` niches (e.g. dev_diary) bypass
        the auto-curator entirely. The deterministic restatement of the
        context bundle is intentionally lower-scored by the LLM QA but
        is the operator-intended output."""
        from services.post_pipeline_actions import run_post_pipeline_actions

        # writer_rag_mode='DETERMINISTIC_COMPOSITOR' — fetchval returns it
        pool, conn = _make_pool(fetchval_return="DETERMINISTIC_COMPOSITOR")
        db = _make_db_service(pool=pool)
        site = _make_site_config()
        settings = _make_settings_service(values={"min_curation_score": "70"})

        emit_mock = AsyncMock()
        notify_mock = AsyncMock()
        with patch(
            "services.post_pipeline_actions.emit_webhook_event", emit_mock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-dev-diary",
                topic="dev_diary entry",
                result=_result(score=45),  # below 70, would normally reject
                site_config=site,
                settings_service=settings,
            )

        # No rejection — task proceeds through to notification.
        reject_calls = [
            c for c in db.update_task.call_args_list
            if len(c.args) >= 2 and c.args[1].get("status") == "rejected"
        ]
        assert len(reject_calls) == 0
        # And no auto_rejected webhook either.
        events = [c.args[1] for c in emit_mock.call_args_list]
        assert "task.auto_rejected" not in events
        # The operator notification path fires.
        notify_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Auto-publish — fires when threshold met AND human approval not required
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAutoPublish:
    """Trusted niches with high scores ship without manual approval."""

    @pytest.mark.asyncio
    async def test_auto_publishes_when_threshold_met_and_approval_optional(self):
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool(fetchval_return=None)
        db = _make_db_service(pool=pool)
        site = _make_site_config()
        settings = _make_settings_service(
            values={
                "min_curation_score": "70",
                "require_human_approval": "false",
            },
        )

        auto_pub_mock = AsyncMock(return_value=True)
        notify_mock = AsyncMock()
        with patch(
            "services.post_pipeline_actions.emit_webhook_event",
            new_callable=AsyncMock,
        ), patch(
            "services.auto_publish.get_auto_publish_threshold",
            AsyncMock(return_value=80.0),
        ), patch(
            "services.auto_publish.auto_publish_task",
            auto_pub_mock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-auto-pub",
                topic="Trusted niche post",
                result=_result(score=90),
                site_config=site,
                settings_service=settings,
            )

        auto_pub_mock.assert_awaited_once_with(
            database_service=db, task_id="t-auto-pub", quality_score=90.0,
        )
        # publish_service handles its own notification; the post-pipeline
        # helper does NOT also fire one.
        notify_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_auto_publish_when_human_approval_required(self):
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool(fetchval_return=None)
        db = _make_db_service(pool=pool)
        site = _make_site_config()
        settings = _make_settings_service(
            values={
                "min_curation_score": "70",
                "require_human_approval": "true",
            },
        )

        auto_pub_mock = AsyncMock(return_value=True)
        notify_mock = AsyncMock()
        with patch(
            "services.post_pipeline_actions.emit_webhook_event",
            new_callable=AsyncMock,
        ), patch(
            "services.auto_publish.auto_publish_task",
            auto_pub_mock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-manual",
                topic="Operator-review post",
                result=_result(score=95),  # high score, still gated
                site_config=site,
                settings_service=settings,
            )

        auto_pub_mock.assert_not_awaited()
        # Operator gets pinged — that's the whole point.
        notify_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_auto_publish_below_threshold(self):
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool(fetchval_return=None)
        db = _make_db_service(pool=pool)
        site = _make_site_config()
        settings = _make_settings_service(
            values={
                "min_curation_score": "70",
                "require_human_approval": "false",
            },
        )

        auto_pub_mock = AsyncMock(return_value=True)
        with patch(
            "services.post_pipeline_actions.emit_webhook_event",
            new_callable=AsyncMock,
        ), patch(
            "services.auto_publish.get_auto_publish_threshold",
            AsyncMock(return_value=80.0),
        ), patch(
            "services.auto_publish.auto_publish_task",
            auto_pub_mock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            new_callable=AsyncMock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-pub-mid",
                topic="Mid-quality post",
                result=_result(score=75),  # above curate, below auto-pub
                site_config=site,
                settings_service=settings,
            )

        auto_pub_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Operator notification — preview URL from finalize stage's preview_token
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOperatorNotification:
    """The notification message embeds a preview URL built from the
    ``preview_token`` written by ``FinalizeTaskStage`` (PR #368)."""

    @pytest.mark.asyncio
    async def test_builds_preview_url_from_existing_token(self):
        """The fetchval that looks up
        ``pipeline_versions.stage_data->metadata->>'preview_token'``
        returns the token finalize_task wrote — the message embeds it."""
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool(fetchval_return="abcd1234efgh5678")
        db = _make_db_service(pool=pool)
        site = _make_site_config(
            values={"preview_base_url": "http://100.64.0.42:8002"},
        )
        settings = _make_settings_service(
            values={
                "min_curation_score": "70",
                "require_human_approval": "true",
                "qa_preview_screenshot_enabled": "false",
            },
        )

        notify_mock = AsyncMock()
        with patch(
            "services.post_pipeline_actions.emit_webhook_event",
            new_callable=AsyncMock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-preview-1",
                topic="Preview happy path",
                result=_result(score=88),
                site_config=site,
                settings_service=settings,
            )

        notify_mock.assert_awaited_once()
        sent_message = notify_mock.call_args.args[0]
        assert "http://100.64.0.42:8002/preview/abcd1234efgh5678" in sent_message
        assert "Awaiting approval" in sent_message
        # Score line surfaces too.
        assert "88/100" in sent_message

    @pytest.mark.asyncio
    async def test_missing_preview_token_degrades_gracefully(self):
        """When the finalize stage somehow didn't write a token (shouldn't
        happen post-PR #368 but defensive), the notification still fires
        — just without a preview link."""
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool(fetchval_return=None)
        db = _make_db_service(pool=pool)
        site = _make_site_config(
            values={"preview_base_url": "http://100.64.0.42:8002"},
        )
        settings = _make_settings_service(
            values={
                "min_curation_score": "70",
                "require_human_approval": "true",
                "qa_preview_screenshot_enabled": "false",
            },
        )

        notify_mock = AsyncMock()
        with patch(
            "services.post_pipeline_actions.emit_webhook_event",
            new_callable=AsyncMock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-no-token",
                topic="No-token edge case",
                result=_result(score=88),
                site_config=site,
                settings_service=settings,
            )

        # Notification still fires — the operator must learn the task is
        # ready even if the preview link is missing.
        notify_mock.assert_awaited_once()
        sent_message = notify_mock.call_args.args[0]
        assert "Awaiting approval" in sent_message
        # Preview URL is omitted (or marked as unavailable).
        assert "preview/" not in sent_message or "no preview link" in sent_message


# ---------------------------------------------------------------------------
# Failure isolation — one side-effect failure doesn't block the next
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailureIsolation:
    """Per-action try/except: a webhook failure must not block the
    auto-curator gate or the operator notification."""

    @pytest.mark.asyncio
    async def test_webhook_failure_does_not_block_notification(self):
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool(fetchval_return="token-abc")
        db = _make_db_service(pool=pool)
        site = _make_site_config(values={"preview_base_url": "http://x/"})
        settings = _make_settings_service(
            values={
                "min_curation_score": "70",
                "require_human_approval": "true",
                "qa_preview_screenshot_enabled": "false",
            },
        )

        emit_mock = AsyncMock(side_effect=RuntimeError("webhook DB down"))
        notify_mock = AsyncMock()
        with patch(
            "services.post_pipeline_actions.emit_webhook_event", emit_mock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-fail-1",
                topic="Webhook failure isolation",
                result=_result(score=88),
                site_config=site,
                settings_service=settings,
            )

        # Webhook tried, failed — but notification still went out.
        emit_mock.assert_awaited()
        notify_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Source-level guards — both orchestrators delegate to the helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProductionCallsiteGuards:
    """Lock the bug shut: a future refactor must NOT sever the Prefect
    flow's call to the post-pipeline helper.

    Mirrors the pattern from poindexter#473 + #477 tests. If a future
    edit silently removes the ``run_post_pipeline_actions`` call from
    the flow, this suite breaks at unit-test time instead of at the
    next overnight batch.

    Glad-Labs/poindexter#410 Stage 4 (2026-05-16) deleted
    ``services/task_executor.py``; Prefect's
    ``content_generation_flow`` is now the sole caller, so we only
    guard that one site.
    """

    def test_content_generation_flow_calls_run_post_pipeline_actions(self):
        from services.flows import content_generation

        source = inspect.getsource(content_generation)
        assert "run_post_pipeline_actions" in source, (
            "services/flows/content_generation.py must call "
            "run_post_pipeline_actions after the pipeline returns so "
            "Prefect-orchestrated tasks fire the post-pipeline "
            "side-effects (webhook + auto-curator + auto-publish + "
            "operator notification). See Glad-Labs/poindexter#478."
        )

    def test_post_pipeline_actions_module_has_docstring(self):
        """``feedback_docs_and_tests_default`` — the module must
        carry a README-level docstring explaining its purpose."""
        import services.post_pipeline_actions as ppa

        assert ppa.__doc__ is not None
        # Mention of the issue + the 4 side-effects must be present so
        # a future reader can find the original context.
        doc = ppa.__doc__.lower()
        assert "poindexter#478" in doc or "#478" in doc
        assert "webhook" in doc
        assert "curator" in doc
        assert "publish" in doc
        assert "notif" in doc
