"""Background Task Executor — polls for pending tasks and drives the content router pipeline."""

import asyncio
import json

# Local OpenClaw notification — worker sends messages through the local gateway
import time
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

import httpx as _httpx

from services.logger_config import get_logger

# Import AI content generator for fallback
from .ai_content_generator import AIContentGenerator
from .error_handler import ServiceError
from .quality_service import UnifiedQualityService
from .usage_tracker import get_usage_tracker
from .webhook_delivery_service import emit_webhook_event

# Telegram notifications now routed through OpenClaw gateway (no direct bot token needed)


async def _notify_discord(message: str) -> None:
    """Send ops notification to Discord #ops channel via webhook."""
    _logger = get_logger(__name__)
    try:
        # Load webhook URL from app_settings (DB-first config)
        from services.site_config import site_config
        webhook_url = site_config.get("discord_ops_webhook_url", "")
        if not webhook_url:
            _logger.debug("[NOTIFY:discord] No discord_ops_webhook_url configured — skipping")
            return

        async with _httpx.AsyncClient(timeout=10) as client:
            _logger.info("[NOTIFY:discord] %s", message[:80])
            await client.post(
                webhook_url,
                json={"content": message},
            )
    except Exception as e:
        _logger.warning("[NOTIFY:discord] Failed: %s", e)


async def _notify_openclaw(message: str, critical: bool = False) -> None:
    """Send pipeline notification via OpenClaw gateway (routes to Telegram + Discord).

    No direct bot tokens needed — OpenClaw owns all messaging channels.
    Falls back to Discord webhook if OpenClaw is unavailable.
    """
    _logger = get_logger(__name__)

    # Try OpenClaw gateway first (routes to both Telegram + Discord)
    if critical:
        try:
            from services.bootstrap_defaults import DEFAULT_OPENCLAW_URL
            from services.site_config import site_config
            openclaw_url = site_config.get("openclaw_gateway_url", DEFAULT_OPENCLAW_URL)
            openclaw_token = site_config.get("openclaw_webhook_token", "hooks-gladlabs")
            async with _httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{openclaw_url}/api/hooks/pipeline",
                    json={"text": message, "critical": True},
                    headers={"Authorization": f"Bearer {openclaw_token}"},
                )
                if resp.status_code < 300:
                    _logger.info("[NOTIFY:openclaw] %s", message[:80])
                    return
        except Exception as e:
            _logger.debug("[NOTIFY:openclaw] Gateway unavailable, falling back to Discord: %s", e)

    # Fallback: Discord webhook (always works, no bot token needed)
    await _notify_discord(message)

# Import WebSocket progress emission (re-exported so tests can patch at this module)
from .websocket_event_broadcaster import emit_notification, emit_task_progress  # noqa: E402,F401

logger = get_logger(__name__)

# Defaults — overridden at runtime by app_settings if available
# (keys: stale_task_timeout_minutes, max_task_retries, task_sweep_interval_seconds)
STALE_TASK_TIMEOUT_MINUTES: int = 60
MAX_TASK_RETRIES: int = 3
SWEEP_INTERVAL_SECONDS: int = 300


class TaskExecutor:
    """Background task executor service"""

    def __init__(
        self,
        database_service,
        orchestrator=None,
        poll_interval: int = 5,
        app_state=None,
        *,
        site_config=None,
    ):
        self.database_service = database_service
        self.orchestrator_initial = orchestrator  # Initial orchestrator from startup
        self.app_state = app_state  # Reference to app.state for dynamic orchestrator updates
        # Phase H step 4.2 (#95): site_config threads through to the content
        # pipeline so stages can read context.get("site_config") instead of
        # touching the module singleton. Usually resolved lazily via app_state
        # because startup_manager constructs TaskExecutor before the lifespan
        # has stashed site_config on app.state.
        self._site_config = site_config
        self.quality_service = UnifiedQualityService()  # Quality validation service
        self.content_generator = AIContentGenerator()  # Fallback content generation
        self.poll_interval = poll_interval
        self.running = False
        self.task_count = 0
        self.success_count = 0
        self.error_count = 0
        self.published_count = 0
        self._processor_task = None
        self.usage_tracker = get_usage_tracker()  # Initialize usage tracking
        self.critique_loop: Any | None = (
            None  # Optional critique loop (not wired in current version)
        )
        self.last_poll_at: float | None = None  # monotonic timestamp of last poll
        self._poll_cycle: int = 0  # incremented each loop iteration
        # Monotonic timestamp of the last time a task was picked up for processing.
        # Used to detect executor stalls (issue #841).
        self._last_task_started_at: float | None = None
        # How long (seconds) the queue may have pending tasks without any being
        # picked up before we fire a CRITICAL alert. Tunable via
        # app_settings.task_executor_idle_alert_threshold_seconds (#198).
        from services.site_config import site_config as _sc_idle
        self._IDLE_ALERT_THRESHOLD_S: int = _sc_idle.get_int(
            "task_executor_idle_alert_threshold_seconds", 300
        )
        # Timestamp tracker for stale task sweeping (FIX: was dead code)
        self._last_sweep: float = 0.0

        logger.info(
            "TaskExecutor initialized: orchestrator=%s, quality_service=yes, content_generator=yes",
            "yes" if orchestrator else "no",
        )

    @property
    def orchestrator(self):
        """Get orchestrator from app.state (preferred) or fall back to initial."""
        if self.app_state and hasattr(self.app_state, "orchestrator"):
            orch = getattr(self.app_state, "orchestrator", None)
            if orch is not None:
                return orch
        return self.orchestrator_initial

    @property
    def site_config(self):
        """Resolve site_config from ctor → app.state → None.

        Tests construct TaskExecutor without site_config and without app_state
        — in that case return None and let process_content_generation_task
        fall back to the module singleton (removed in Phase H step 5).
        """
        if self._site_config is not None:
            return self._site_config
        if self.app_state is not None:
            return getattr(self.app_state, "site_config", None)
        return None

    def inject_orchestrator(self, orchestrator) -> None:
        """Inject or replace the orchestrator at runtime."""
        self.orchestrator_initial = orchestrator

    async def start(self):
        """Start the background task processor"""
        if self.running:
            logger.warning("Task executor already running")
            return

        self.running = True
        logger.info("Starting task executor background processor...")
        logger.info("   Poll interval: %s seconds", self.poll_interval)
        logger.info("   Database service: %s", self.database_service is not None)
        logger.info("   Orchestrator: %s", self.orchestrator is not None)
        logger.info(
            "   Orchestrator type: %s",
            type(self.orchestrator).__name__ if self.orchestrator else "None",
        )

        # Create background task
        self._processor_task = asyncio.create_task(self._process_loop())
        logger.info("[OK] Task executor background processor started")

    async def stop(self):
        """Stop the background task processor"""
        if not self.running:
            return

        self.running = False
        logger.info("Stopping task executor...")

        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                logger.debug("Task processor task cancelled successfully")

        logger.info(
            "[OK] Task executor stopped (processed: %s, success: %s, errors: %s)",
            self.task_count, self.success_count, self.error_count,
        )

    async def _process_loop(self):
        """Main processing loop - runs continuously in background"""
        logger.info("Task executor processor loop started")

        while self.running:
            try:
                self.last_poll_at = time.monotonic()
                self._poll_cycle += 1
                # Get pending tasks from database
                logger.debug("[TASK_EXEC_LOOP] Polling for pending tasks...")
                pending_tasks = await self.database_service.get_pending_tasks(limit=10)

                # Throttle: don't process new tasks if approval queue is too full.
                # The throttle state is shared (services.pipeline_throttle) so that
                # the idle worker's discovery gate, the API create-task handler,
                # and the /api/prometheus endpoint all see the same truth and can
                # emit a structured "we are throttled" signal — see GH-89.
                try:
                    from services.pipeline_throttle import is_queue_full
                    # Phase H (GH#95) transitional: task_executor has not
                    # been migrated off the module singleton yet, so we
                    # re-import and pass it through. Future migration
                    # threads site_config into TaskExecutor's ctor.
                    from services.site_config import site_config as _sc

                    _full, _queue_size, _queue_limit = await is_queue_full(
                        self.database_service.pool, _sc,
                    )
                    if _full and pending_tasks:
                        # WARN (not INFO) so this shows up in the default log
                        # filter and in any Discord ops alert that tails warn+.
                        # Previously buried at INFO under Sentry DEBUG noise —
                        # see GH-89 observation (b).
                        logger.warning(
                            "[THROTTLE] Approval queue full (%d/%d) — skipping generation. "
                            "Free a slot via /approve-post or raise max_approval_queue in "
                            "app_settings to resume.",
                            _queue_size, _queue_limit,
                        )
                        pending_tasks = []  # Skip processing this cycle
                except Exception:
                    # Throttle-check failure must never poison the main loop.
                    # Falling through = proceed with whatever get_pending_tasks
                    # returned (safest option: pipeline keeps working even if
                    # the throttle gauge is temporarily wrong).
                    logger.debug("[THROTTLE] Throttle check failed", exc_info=True)

                if pending_tasks:
                    logger.info("[TASK_EXEC_LOOP] Found %s pending task(s)", len(pending_tasks))
                    for idx, task in enumerate(pending_tasks, 1):
                        logger.info(
                            "   [%s] Task ID: %s, Name: %s, Status: %s",
                            idx, task.get("id"), task.get("task_name"), task.get("status"),
                        )

                    # Check whether this executor has been sitting on pending tasks
                    # without starting any for longer than the idle threshold (#841).
                    if self._last_task_started_at is not None:
                        idle_s = time.monotonic() - self._last_task_started_at
                        if idle_s > self._IDLE_ALERT_THRESHOLD_S:
                            logger.critical(
                                "[task_executor] Executor has not started a task in "
                                "%.0fs with %s pending task(s) "
                                "in the queue — possible stall or hang",
                                idle_s, len(pending_tasks),
                            )

                    # Process each task
                    for task in pending_tasks:
                        if not self.running:
                            logger.warning("[TASK_EXEC_LOOP] Executor stopped - breaking loop")
                            break

                        task_id = task.get("id")

                        try:
                            logger.info("[TASK_EXEC_LOOP] Starting to process task: %s", task_id)
                            self._last_task_started_at = time.monotonic()
                            await self._process_single_task(task)
                            self.success_count += 1
                            logger.info(
                                "[OK] [TASK_EXEC_LOOP] Task succeeded (total success: %s)",
                                self.success_count,
                            )
                        except Exception as e:
                            logger.error(
                                "[FAIL] [TASK_EXEC_LOOP] Error processing task %s: %s",
                                task_id, e,
                                exc_info=True,
                            )
                            # Update task as failed
                            try:
                                await self.database_service.update_task(
                                    task_id,
                                    {
                                        "status": "failed",
                                        "task_metadata": {
                                            "error": str(e),
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                        },
                                    },
                                )
                                logger.info(
                                    "[TASK_EXEC_LOOP] Updated task %s status to failed",
                                    task_id,
                                )
                            except Exception as update_err:
                                logger.error(
                                    "[FAIL] [TASK_EXEC_LOOP] Failed to update task status: %s",
                                    update_err,
                                    exc_info=True,
                                )
                            self.error_count += 1
                            logger.info(
                                "[FAIL] [TASK_EXEC_LOOP] Task failed (total errors: %s)",
                                self.error_count,
                            )
                        finally:
                            self.task_count += 1
                else:
                    logger.debug(
                        "[TASK_EXEC_LOOP] No pending tasks - running idle work"
                    )
                    # Run background maintenance when pipeline is idle
                    try:
                        from services.idle_worker import IdleWorker
                        if self.database_service and self.database_service.pool:
                            if not hasattr(self, '_idle_worker'):
                                self._idle_worker = IdleWorker(self.database_service.pool)
                            await self._idle_worker.run_cycle()
                    except Exception as idle_err:
                        logger.debug("[TASK_EXEC_LOOP] Idle worker error (non-critical): %s", idle_err)

                # Sweep stale tasks on a schedule (every SWEEP_INTERVAL_SECONDS)
                if time.time() - self._last_sweep > SWEEP_INTERVAL_SECONDS:
                    await self._sweep_stale_tasks()
                    self._last_sweep = time.time()

                # Sleep before next poll
                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                # Log at CRITICAL so Sentry's default "new issue" alert fires and
                # on-call engineers are paged when the executor loop exits (issue #556).
                logger.critical(
                    "[TASK_EXEC_LOOP] Task executor processor loop cancelled — "
                    "background task processing has stopped",
                    exc_info=True,
                )
                break
            except Exception as e:
                logger.error(
                    "[FAIL] [TASK_EXEC_LOOP] Unexpected error in task executor loop: %s",
                    e,
                    exc_info=True,
                )
                logger.info(
                    "[TASK_EXEC_LOOP] Sleeping for %ss before retry...",
                    self.poll_interval,
                )
                await asyncio.sleep(self.poll_interval)

        # Log at CRITICAL so Sentry alerts fire immediately on loop exit (issue #556).
        logger.critical(
            "[TASK_EXEC_LOOP] Task executor processor loop stopped — "
            "processed=%s success=%s errors=%s",
            self.task_count, self.success_count, self.error_count,
        )

    async def _process_single_task(self, task: dict[str, Any]):
        """Process a single task through the pipeline"""
        task_id = task.get("id") or task.get("task_id")
        if not task_id:
            logger.warning("[TASK_SINGLE] Task has no id or task_id — skipping")
            return

        task_name = task.get("task_name", "Untitled")
        topic = task.get("topic", "")
        category = task.get("category", "general")

        # Bind a synthetic trace ID for the duration of this task's processing.
        # Background tasks run outside any HTTP request context so _request_id_var
        # would otherwise remain None (logged as "-"), making it impossible to
        # correlate executor log lines with the API request that created the task.
        # Using "task-<id>" as the trace ID allows `grep request_id=task-<id>` to
        # find all log lines emitted by both the route handler and the executor.
        from middleware.request_id import _request_id_var

        trace_id = f"task-{task_id}"
        _trace_token = _request_id_var.set(trace_id)

        # All execution is wrapped in try/finally so _trace_token is always reset
        # when this task finishes — prevents the synthetic trace ID from leaking
        # into the next task processed by the same asyncio worker.
        try:
            logger.info("[TASK_SINGLE] Processing task: %s", task_id)
            logger.info("   Name: %s", task_name)
            logger.info("   Topic: %s", topic)
            logger.info("   Category: %s", category)

            # Pre-generation brand relevance check — reject off-topic before wasting GPU.
            # Skip for URL-seeded tasks: the operator explicitly pointed us at that URL,
            # so "off-brand" isn't our call to make on their behalf.
            from services.topic_discovery import TopicDiscovery
            _task_meta = task.get("metadata") or task.get("task_metadata") or {}
            if isinstance(_task_meta, str):
                import json as _json_mod
                try:
                    _task_meta = _json_mod.loads(_task_meta)
                except Exception:
                    _task_meta = {}
            # task_metadata from legacy writers wraps under {"metadata": {...}}
            if isinstance(_task_meta, dict) and "discovered_by" not in _task_meta:
                _inner = _task_meta.get("metadata")
                if isinstance(_inner, str):
                    import json as _json_mod
                    try:
                        _inner = _json_mod.loads(_inner)
                    except Exception:
                        _inner = None
                if isinstance(_inner, dict):
                    _task_meta = _inner
            _user_seeded = _task_meta.get("discovered_by") in ("url_seed", "url_list")
            if topic and not _user_seeded and not TopicDiscovery._is_brand_relevant(topic):
                _reason = (
                    f"Off-brand: topic '{topic[:80]}' did not match any keyword in "
                    f"TopicDiscovery._BRAND_KEYWORDS. Add the relevant niche keyword "
                    f"to the whitelist (topic_discovery.py) or to the 'brand_keywords' "
                    f"app_settings key if this topic should have passed."
                )
                logger.info("[TASK_SINGLE] Rejecting off-brand topic: %s", topic[:60])
                # Populate BOTH error_message and result.reason so the
                # approval UI, MCP list_tasks, and any downstream log
                # scraper sees WHY the task was rejected — previously
                # only result.reason was set and error_message was
                # empty, which looked like a silent cancel.
                await self.database_service.update_task(
                    task_id=task_id,
                    updates={
                        "status": "rejected",
                        "error_message": _reason,
                        "result": '{"reason": "Off-topic: not relevant to brand"}',
                    },
                )
                return

            # Pre-generation semantic duplicate check — reject near-duplicate
            # topics BEFORE the writer burns GPU time on content we already have.
            #
            # Matt 2026-04-11: "We should be taking advantage of the vector db
            # as much as possible. Built by bots for bots kinda thing."
            #
            # Strategy: embed the topic string, search `source_table='posts'`
            # at a high cosine-similarity floor, and if anything crosses the
            # threshold, reject loud with the matching slugs + similarity
            # scores. The post embeddings come from `scripts/auto-embed.py`
            # which syncs nightly (so newly-published posts are visible to
            # the next task's dedup check). Gated on `enable_semantic_dedup`
            # so it can be disabled without a code change.
            if topic and await self._semantic_dedup_enabled():
                _dup_hit = await self._check_semantic_duplicate(topic)
                if _dup_hit is not None:
                    _reason, _matches = _dup_hit
                    # _matches is now list[MemoryHit] from MemoryClient
                    # (previously list[dict] from EmbeddingsDatabase).
                    _top_sim = max(
                        (getattr(m, "similarity", 0) for m in _matches),
                        default=0,
                    )
                    logger.warning(
                        "[TASK_SINGLE] Rejecting semantic duplicate: topic=%r matches=%d top_sim=%.3f",
                        topic[:60], len(_matches), _top_sim,
                    )
                    await self.database_service.update_task(
                        task_id=task_id,
                        updates={
                            "status": "rejected",
                            "error_message": _reason,
                            "result": '{"reason": "Semantic duplicate: matches an existing published post"}',
                        },
                    )
                    # Write pipeline_reviews row so content_tasks view
                    # resolves approval_status='rejected' for semantic-
                    # dedup kills, and flip the model_performance outcome
                    # for any LLM calls already logged for this task.
                    with suppress(Exception):
                        from services.pipeline_db import PipelineDB
                        await PipelineDB(self.database_service.pool).add_review(
                            task_id=task_id,
                            decision="rejected",
                            reviewer="semantic_dedup",
                            feedback=_reason[:2000],
                        )
                    with suppress(Exception):
                        await self.database_service.mark_model_performance_outcome(
                            task_id, human_approved=False,
                        )
                    # Loud audit event — surfaces on /pipeline dashboard.
                    try:
                        from services.audit_log import audit_log_bg
                        audit_log_bg(
                            "semantic_dedup_rejected", "task_executor",
                            {
                                "topic": topic[:200],
                                "match_count": len(_matches),
                                "top_similarity": round(_top_sim, 4),
                                "matches": [
                                    {
                                        "post_id": getattr(m, "source_id", None),
                                        "similarity": round(
                                            getattr(m, "similarity", 0), 4
                                        ),
                                        "title": (
                                            getattr(m, "metadata", {}) or {}
                                        ).get("title", ""),
                                        "writer": getattr(m, "writer", None),
                                    }
                                    for m in _matches[:5]
                                ],
                                "stage": "pre_generation",
                            },
                            task_id=task_id, severity="warning",
                        )
                    except Exception as _audit_err:
                        logger.debug("semantic_dedup audit log failed: %s", _audit_err)
                    return

            # Set per-task timeout. Default 15 min; configurable via
            # app_settings key 'task_timeout_seconds' so operators can
            # bump it when running a bigger (slower) writer model like
            # qwen2.5:72b or llama3.3:70b without a redeploy.
            TASK_TIMEOUT_SECONDS = 900  # 15 minutes default
            try:
                _tts = await self._get_setting("task_timeout_seconds", "900")
                TASK_TIMEOUT_SECONDS = int(_tts)
            except Exception:
                pass

            # 1. Update task status to 'in_progress'
            logger.info("[TASK_SINGLE] Marking task as in_progress...")
            _now = datetime.now(timezone.utc)
            await self.database_service.update_task(
                task_id,
                {
                    "status": "in_progress",
                    "started_at": _now,
                    "task_metadata": {
                        "status": "processing",
                        "started_at": _now.isoformat(),
                    },
                },
            )
            await self.database_service.tasks.log_status_change(task_id, "pending", "in_progress")
            logger.info("[OK] [TASK_SINGLE] Task marked as in_progress")

            # Notify Discord #ops that a task started generating
            await _notify_discord(f"Generating: \"{topic[:80]}\" ({category or 'uncategorized'})")

            # 2. Run through content router pipeline (the full 6-stage pipeline)
            logger.info(
                "[TASK_SINGLE] Executing content router pipeline (timeout: %ss)...",
                TASK_TIMEOUT_SECONDS,
            )
            # Removed "Processing..." notification — too noisy. Only notify on completion.
            try:
                from services.content_router_service import process_content_generation_task

                async def _run_content_pipeline():
                    return await process_content_generation_task(
                        topic=task.get("topic", ""),
                        style=task.get("style") or "narrative",
                        tone=task.get("tone") or "professional",
                        target_length=task.get("target_length") or 1500,
                        tags=task.get("tags", []),
                        generate_featured_image=True,
                        database_service=self.database_service,
                        task_id=task_id,
                        models_by_phase=task.get("model_selections") or await self._get_model_selections(task_id),
                        quality_preference=task.get("quality_preference", "balanced"),
                        category=task.get("category", "general"),
                        target_audience=task.get("target_audience", "General"),
                        site_config=self.site_config,
                    )

                # GH-90 AC #2: run a background heartbeat for the entire duration
                # of the content-pipeline execution. The heartbeat stamps
                # pipeline_tasks.updated_at = NOW() every N seconds so the
                # stale-task sweeper can tell the worker is still alive during
                # long writer / QA / image-gen stages (no single DB write
                # otherwise happens for 60-180s at a time). The heartbeat task
                # is always cancelled in the finally block.
                heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(task_id),
                    name=f"heartbeat:{task_id}",
                )
                try:
                    result = await asyncio.wait_for(
                        _run_content_pipeline(), timeout=TASK_TIMEOUT_SECONDS
                    )
                finally:
                    heartbeat_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await heartbeat_task
                # Content router sets status directly — mark as complete
                result = result if isinstance(result, dict) else {}
                result.setdefault("status", "awaiting_approval")
            except asyncio.TimeoutError:
                logger.error(
                    "[TIMEOUT] [TASK_SINGLE] Task execution timed out after %ss: %s",
                    TASK_TIMEOUT_SECONDS, task_id,
                    exc_info=True,
                )
                result = {
                    "status": "failed",
                    "orchestrator_error": f"Task execution timeout ({TASK_TIMEOUT_SECONDS}s exceeded)",
                }

            logger.info("[OK] [TASK_SINGLE] Task execution completed")

            # The content router pipeline updates the task directly in DB at each stage.
            # Only do additional updates here if the pipeline failed or timed out.
            final_status = (
                result.get("status", "awaiting_approval")
                if isinstance(result, dict)
                else "awaiting_approval"
            )

            if final_status not in ("failed",):
                # Content router already set the task to awaiting_approval with all fields.
                # No additional DB update needed — just log and return.
                logger.info(
                    "[OK] [TASK_SINGLE] Content router completed — task already updated in DB "
                    "(status=%s, task_id=%s)",
                    final_status, task_id,
                )

                # Emit task.completed webhook unconditionally for all successful completions
                quality_score = float(result.get("quality_score", 0)) if isinstance(result, dict) else 0.0
                try:
                    await emit_webhook_event(getattr(self.database_service, "cloud_pool", None) or self.database_service.pool, "task.completed", {
                        "task_id": task_id, "topic": topic, "quality_score": quality_score,
                        "status": final_status,
                    })
                except Exception:
                    logger.warning("[WEBHOOK] Failed to emit task.completed event", exc_info=True)

                # Auto-curation: reject posts that don't meet minimum quality bar
                # before bothering the human with a review notification.
                # Tunable via app_settings key: min_curation_score (default 70)
                min_curation_score = float(await self._get_setting("min_curation_score", "70"))
                if 0 < quality_score < min_curation_score:
                    logger.info(
                        "[CURATE] Auto-rejecting low-quality post: %s (score %.1f < %.1f)",
                        topic[:40], quality_score, min_curation_score,
                    )
                    await self.database_service.update_task(task_id, {"status": "rejected"})
                    # Record the rejection on pipeline_reviews so the
                    # `content_tasks` view's approval_status column stops
                    # resolving NULL for auto-rejected rows.
                    with suppress(Exception):
                        from services.pipeline_db import PipelineDB
                        await PipelineDB(self.database_service.pool).add_review(
                            task_id=task_id,
                            decision="rejected",
                            reviewer="auto_curator",
                            feedback=f"Quality score {quality_score:.1f} below threshold {min_curation_score:.1f}",
                        )
                    # Flip model_performance.human_approved=False for the
                    # learning signal (gitea#271 Phase 3.A1).
                    with suppress(Exception):
                        await self.database_service.mark_model_performance_outcome(
                            task_id, human_approved=False,
                        )
                    # Webhook delivery is best-effort; don't mask the rejection
                    # with an emitter failure.
                    with suppress(Exception):
                        await emit_webhook_event(
                            getattr(self.database_service, "cloud_pool", None) or self.database_service.pool,
                            "task.auto_rejected",
                            {"task_id": task_id, "topic": topic, "quality_score": quality_score,
                             "reason": f"score {quality_score:.0f} < {min_curation_score:.0f}"},
                        )
                    return

                # Check if human approval is required before publishing
                auto_published = False
                try:
                    require_approval = await self._get_setting("require_human_approval", "true")
                    if require_approval.lower() in ("true", "1", "yes"):
                        logger.info(
                            "[APPROVAL] Human approval required — task %s (score: %.0f) queued as awaiting_approval",
                            task_id, quality_score,
                        )
                    else:
                        auto_threshold = await self._get_auto_publish_threshold()
                        if auto_threshold > 0 and quality_score >= auto_threshold:
                            logger.info(
                                "[AUTO_PUBLISH] Quality score %s >= threshold %s, "
                                "auto-publishing task %s",
                                quality_score, auto_threshold, task_id,
                            )
                            await self._auto_publish_task(task_id, quality_score)
                            auto_published = True
                except Exception:
                    logger.warning("Auto-publish check failed, task stays in awaiting_approval", exc_info=True)

                # Single notification: direct Discord + Telegram with preview link.
                # No duplicate webhook events — this is the ONLY notification per task.
                # publish_post_from_task handles its own notification if auto-published.
                if auto_published:
                    pass  # publish_service.py handles the notification
                else:
                    # Generate preview token so Matt can see the post before approving
                    # NOTE: At this point, no post row exists yet (post is created on approval).
                    # Store preview_token on the content_tasks table instead.
                    preview_url = ""
                    try:
                        import secrets as _secrets
                        preview_token = _secrets.token_hex(16)
                        pool = getattr(self.database_service, "cloud_pool", None) or self.database_service.pool
                        await pool.execute(
                            """UPDATE content_tasks
                               SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('preview_token', $1::text)
                               WHERE task_id = $2""",
                            preview_token, task_id,
                        )
                        from services.site_config import site_config as _sc
                        # Use worker's own URL for HTML preview (accessible via Tailscale)
                        _preview_base = _sc.get("preview_base_url", "http://100.81.93.12:8002")
                        preview_url = f"{_preview_base}/preview/{preview_token}"
                    except Exception:
                        logger.debug("[PREVIEW] Failed to create preview token", exc_info=True)

                    # Final visual QA: screenshot the rendered preview and
                    # run it through the vision model. Only fires when
                    # qa_preview_screenshot_enabled is true in app_settings
                    # (default false — opt-in). Result is stored on task
                    # metadata and surfaced in the approval notification
                    # so Matt sees it before reviewing.
                    preview_qa_note = ""
                    if preview_url:
                        try:
                            from services.container import get_service
                            from services.multi_model_qa import MultiModelQA

                            _settings_svc = get_service("settings")
                            # Resolve preview URL to one reachable from
                            # inside the worker container. The external
                            # preview_base_url may be a Tailscale hostname
                            # that isn't resolvable here.
                            _internal_preview_url = f"http://localhost:8002/preview/{preview_token}"

                            _pqa = MultiModelQA(
                                pool=self.database_service.pool,
                                settings_service=_settings_svc,
                            )
                            _pqa_review = await _pqa._check_rendered_preview(
                                title=topic,
                                topic=topic,
                                preview_url=_internal_preview_url,
                            )
                            if _pqa_review is not None:
                                preview_qa_note = (
                                    f"Visual QA: {int(_pqa_review.score)}/100 — "
                                    + _pqa_review.feedback[:200]
                                )
                                try:
                                    await pool.execute(
                                        """UPDATE content_tasks
                                           SET metadata = COALESCE(metadata, '{}'::jsonb)
                                               || jsonb_build_object(
                                                   'preview_qa_score', $1::numeric,
                                                   'preview_qa_approved', $2::boolean,
                                                   'preview_qa_feedback', $3::text
                                               )
                                           WHERE task_id = $4""",
                                        float(_pqa_review.score),
                                        bool(_pqa_review.approved),
                                        _pqa_review.feedback[:500],
                                        task_id,
                                    )
                                except Exception as _persist_err:
                                    logger.debug(
                                        "[PREVIEW_QA] persist failed: %s", _persist_err
                                    )
                                logger.info(
                                    "[PREVIEW_QA] Task %s visual score=%s approved=%s",
                                    task_id[:8], _pqa_review.score, _pqa_review.approved,
                                )
                        except Exception as _vqa_err:
                            logger.debug(
                                "[PREVIEW_QA] skipped (non-critical): %s", _vqa_err
                            )

                    msg = f"Awaiting approval: \"{topic}\"\n"
                    msg += f"Score: {quality_score:.0f}/100\n"
                    if preview_qa_note:
                        msg += preview_qa_note + "\n"
                    if preview_url:
                        msg += f"Preview: {preview_url}\n"
                    msg += f"Approve: /approve-post {task_id[:8]}"
                    await _notify_openclaw(msg, critical=True)

                return

            # --- FAILURE PATH ONLY below this point ---
            logger.info("[TASK_SINGLE] Updating failed task status...")

            # Extract relevant fields from result for task_metadata (don't store entire result)
            # This prevents the entire result dict from being treated as metadata
            task_metadata_updates = {}
            if isinstance(result, dict):
                # Extract only the fields we want in task_metadata
                fields_to_extract = [
                    "content",
                    "excerpt",
                    "title",
                    "featured_image_url",
                    "featured_image_data",
                    "seo_title",
                    "seo_description",
                    "seo_keywords",
                    "qa_feedback",
                    "quality_score",
                    "orchestrator_error",
                    "message",
                    "constraint_compliance",
                    "stage",
                    "percentage",
                    "model_used",
                ]
                for field in fields_to_extract:
                    if field in result:
                        task_metadata_updates[field] = result[field]
            else:
                task_metadata_updates["output"] = str(result)

            # DEBUG: Log all extracted metadata
            logger.debug("Extracted metadata for task %s:", task_id)
            logger.info("   - Fields extracted: %s", list(task_metadata_updates.keys()))
            logger.info("   - Has 'content': %s", "content" in task_metadata_updates)
            if "content" in task_metadata_updates:
                content_val = task_metadata_updates.get("content")
                logger.info("   - Content type: %s", type(content_val).__name__)
                logger.info(
                    "   - Content length: %s chars",
                    len(content_val) if isinstance(content_val, str) else "N/A",
                )
                if isinstance(content_val, str):
                    logger.info("   - Content preview: %s...", content_val[:100])

            # ⚠️ IMPORTANT: Don't store incomplete content for failed tasks
            # Only store content if task is approved/successful
            # This prevents partial/truncated content from appearing in the database
            if final_status in {"failed", "rejected"}:
                logger.warning(
                    "[WARN] Task status is '%s' - NOT storing content to prevent partial/truncated data",
                    final_status,
                )
                # Remove content fields for failed tasks
                task_metadata_updates.pop("content", None)
                task_metadata_updates.pop("excerpt", None)
                task_metadata_updates.pop("featured_image_url", None)
                task_metadata_updates.pop("featured_image_data", None)

            # Use update_task to ensure normalization of content into columns
            logger.debug(
                "Calling update_task with status=%s, metadata keys=%s",
                final_status, list(task_metadata_updates.keys()),
            )

            # Also store model_used in the normalized column if it's in the result
            update_payload = {"status": final_status, "task_metadata": task_metadata_updates}
            if isinstance(result, dict) and "model_used" in result:
                update_payload["model_used"] = result["model_used"]
                logger.debug("Including model_used in database update: %s", result["model_used"])

            await self.database_service.update_task(task_id, update_payload)
            logger.debug("[OK] update_task completed for %s", task_id)

            quality_score_preview = (
                task_metadata_updates.get("quality_score")
                if isinstance(task_metadata_updates, dict)
                else None
            )
            user_id = task.get("user_id")
            if final_status == "failed":
                error_msg = (
                    result.get("orchestrator_error", "Unknown error")
                    if isinstance(result, dict)
                    else "Unknown error"
                )
                logger.error(
                    "[FAIL] [TASK_SINGLE] Task failed: task_id=%s user_id=%s category=%s error=%r",
                    task_id,
                    user_id,
                    category,
                    error_msg,
                )
                # Emit webhook event for failed task
                try:
                    await emit_webhook_event(getattr(self.database_service, "cloud_pool", None) or self.database_service.pool, "task.failed", {
                        "task_id": task_id, "topic": topic, "error": str(error_msg)[:200],
                    })
                except Exception:
                    logger.warning("[WEBHOOK] Failed to emit task.failed event", exc_info=True)
                await _notify_openclaw(f"Failed: \"{topic}\" - {str(error_msg)[:100]}", critical=True)
            else:
                logger.info(
                    "[OK] [TASK_SINGLE] Task %s: task_id=%s user_id=%s category=%s quality_score=%s",
                    final_status,
                    task_id,
                    user_id,
                    category,
                    quality_score_preview,
                )

        except ServiceError:
            raise
        except Exception as e:
            logger.error("[FAIL] [TASK_SINGLE] Task failed: %s - %s", task_id, e, exc_info=True)
            raise ServiceError(message=str(e), details={"task_id": task_id}) from e
        finally:
            # Reset the ContextVar so the synthetic trace ID does not bleed into
            # subsequent tasks that may run in the same asyncio worker.
            _request_id_var.reset(_trace_token)

    async def _get_setting(self, key: str, default: str) -> str:
        """Read a setting from app_settings, falling back to default.

        Uses the AdminDatabase's 60-second TTL cache instead of hitting
        the database directly on every call.  Always returns a str —
        the DB may store booleans or ints, so we coerce here.
        """
        if not self.database_service:
            return default
        try:
            raw = await self.database_service.get_setting_value(key, default)
            return str(raw) if raw is not None else default
        except Exception:
            return default

    async def _semantic_dedup_enabled(self) -> bool:
        """Gate for the pre-generation semantic duplicate check.

        Defaults to True (enabled) per Matt's 2026-04-11 direction to use the
        vector db aggressively. Flip `enable_semantic_dedup=false` in
        app_settings to disable without a code change.
        """
        raw = await self._get_setting("enable_semantic_dedup", "true")
        return (raw or "true").strip().lower() not in ("false", "0", "no", "off")

    async def _check_semantic_duplicate(
        self, topic: str
    ) -> tuple[str, list[Any]] | None:
        """Embed the topic and search pgvector for near-duplicate published posts.

        Migrated 2026-04-11 from direct `database_service.embeddings.search_similar`
        to `poindexter.memory.MemoryClient.find_similar_posts` per Gitea #192
        slice 3. The helper hardcodes `source_table='posts'` so the
        singular/plural silent-zero-result bug can never recur here.

        Returns (reason, matches) when the topic looks like a duplicate of an
        existing post (cosine similarity >= threshold). Returns None when the
        topic looks original OR when the embedding / search infrastructure
        isn't available — we deliberately never block task creation on
        dedup-layer errors because a broken embedder shouldn't strand the
        whole queue.

        Threshold is tunable via `semantic_dedup_threshold` (default 0.75).
        Matches are pulled from `source_table='posts'`, so only fully published
        posts count — drafts and awaiting_approval tasks do NOT block new
        queueing (deliberate: we still want the human review queue to fill up
        with variants of a topic if the author hasn't approved the first one).
        """
        # Pull threshold lazily so the DB value is always honored.
        _raw_threshold = "0.75"
        try:
            _raw_threshold = await self._get_setting("semantic_dedup_threshold", "0.75")
            threshold = float(_raw_threshold)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Invalid semantic_dedup_threshold in app_settings (%r): %s — "
                "falling back to 0.75",
                _raw_threshold, exc,
            )
            threshold = 0.75

        try:
            from poindexter.memory import MemoryClient

            async with MemoryClient() as mem:
                matches = await mem.find_similar_posts(
                    topic, limit=5, min_similarity=threshold
                )
        except Exception as exc:
            logger.warning(
                "[TASK_SINGLE] semantic_dedup check failed (non-fatal, task will proceed): %s",
                exc,
            )
            return None

        if not matches:
            return None  # nothing crossed the threshold — topic is fresh enough

        # Build a human-readable reason block with the top matches. Keep it
        # short; this lands in task.error_message which is surfaced in the
        # /pipeline dashboard, MCP list_tasks, and Discord alerts.
        pool = self.database_service.pool if self.database_service else None
        match_lines: list[str] = []
        for hit in matches[:3]:
            sim = hit.similarity
            source_id = hit.source_id
            title = (hit.metadata or {}).get("title") or "(unknown title)"
            slug = ""
            if pool:
                try:
                    # auto-embed.py stores post source_ids as either the raw
                    # UUID or "post/<uuid>". Strip the prefix before the DB
                    # lookup to cover both shapes.
                    lookup_id = source_id.removeprefix("post/")
                    row = await pool.fetchrow(
                        "SELECT slug FROM posts WHERE id::text = $1 LIMIT 1",
                        lookup_id,
                    )
                    if row and row["slug"]:
                        slug = row["slug"]
                except Exception:
                    pass
            url = f"/posts/{slug}" if slug else f"(post id: {source_id})"
            match_lines.append(f"  - {sim:.3f}  {title[:80]}  {url}")

        reason = (
            f"Semantic duplicate: topic '{topic[:80]}' is ≥{threshold:.2f} similar to "
            f"{len(matches)} existing published post(s). Top matches:\n"
            + "\n".join(match_lines)
            + "\n\nIf this topic really is different enough, either lower "
            "semantic_dedup_threshold in app_settings or disable the check entirely "
            "with enable_semantic_dedup=false."
        )
        return reason, matches

    async def _get_model_selections(self, task_id: str) -> dict:
        """Read model_selections directly from DB (bypasses TaskResponse serialization)."""
        if not self.database_service or not self.database_service.pool:
            return {}
        try:
            import json as _json
            row = await self.database_service.pool.fetchrow(
                "SELECT model_selections FROM content_tasks WHERE task_id = $1", task_id
            )
            if row and row["model_selections"]:
                ms = row["model_selections"]
                return _json.loads(ms) if isinstance(ms, str) else ms
        except Exception:
            pass
        return {}

    async def _heartbeat_loop(self, task_id: str) -> None:
        """Background heartbeat — stamp ``updated_at = NOW()`` periodically.

        GH-90 AC #2: runs as a background asyncio.Task for the duration of
        a single task's pipeline execution. Cadence is configurable via
        ``worker_heartbeat_interval_seconds`` (default 30s). Stops
        gracefully when cancelled or when the heartbeat UPDATE returns 0
        rows (meaning the task has already transitioned to a terminal
        state — sweeper flipped it, human cancelled it, etc.).

        The loop never raises — a dead heartbeat must NOT bring down the
        whole pipeline. Failures are swallowed and logged at debug.
        """
        if not task_id or not self.database_service:
            return
        # Default is intentionally generous (30s). Operators tune via the
        # app_settings key; we re-read once per task to avoid mid-task
        # churn on an in-flight heartbeat. Parsed as float so tests (and
        # operators with sub-second needs) can use e.g. "0.5".
        try:
            interval_s = float(await self._get_setting(
                "worker_heartbeat_interval_seconds", "30"
            ))
        except Exception:
            interval_s = 30.0
        if interval_s <= 0:
            # Disabled — operator set interval to 0 or negative.
            return
        try:
            while True:
                await asyncio.sleep(interval_s)
                try:
                    still_alive = await self.database_service.heartbeat_task(task_id)
                except Exception as e:
                    logger.debug(
                        "[heartbeat:%s] heartbeat error (non-fatal): %s",
                        task_id, e,
                    )
                    continue
                if not still_alive:
                    # Row is already in a terminal state (failed, cancelled,
                    # rejected, awaiting_approval, published). No point
                    # continuing to heartbeat — the pipeline will finish
                    # its current stage and then the status-guarded
                    # terminal writes will refuse to overwrite the
                    # terminal state.
                    logger.warning(
                        "[heartbeat:%s] task is no longer in pending/in_progress — "
                        "heartbeat loop exiting; worker will detect cancellation "
                        "at next status-guarded write (GH-90)",
                        task_id,
                    )
                    return
        except asyncio.CancelledError:
            # Normal shutdown path — _process_single_task cancels us in
            # its finally block.
            raise

    async def _sweep_stale_tasks(self) -> None:
        """Reset tasks stuck in processing state back to pending."""
        if not self.database_service:
            return
        try:
            timeout = int(await self._get_setting("stale_task_timeout_minutes", str(STALE_TASK_TIMEOUT_MINUTES)))
            max_retries = int(await self._get_setting("max_task_retries", str(MAX_TASK_RETRIES)))
            result = await self.database_service.sweep_stale_tasks(
                timeout_minutes=timeout,
                max_retries=max_retries,
            )
            if result and result.get("total_stale", 0) > 0:
                logger.warning(
                    "[_sweep_stale_tasks] Reset %s stale tasks (timeout: %sm)",
                    result["reset"], timeout,
                )
        except Exception:
            logger.warning("[_sweep_stale_tasks] Failed to sweep stale tasks", exc_info=True)

        # Auto-cancel tasks that have been stuck in 'pending' for too long.
        # This is the stale-pending sweeper (GH-89 AC#4) — without it, a
        # throttle event that outlives the operator's attention leaves
        # tasks queued forever, so the pipeline's "active backlog" grows
        # without bound.
        await self._sweep_stale_pending_tasks()

        # Also auto-retry recently failed/rejected tasks with adjusted params
        await self._auto_retry_failed_tasks()

    async def _sweep_stale_pending_tasks(self) -> None:
        """Auto-cancel tasks that have been stuck in 'pending' for too long.

        Configurable via ``app_settings.stale_pending_timeout_hours``
        (default 24). Fires at warn level so operators can tell the
        difference between "a task is running slowly" (normal) and "the
        pipeline was throttled for a day and we're reaping the backlog"
        (GH-89 symptom).

        Tasks are moved to ``status = 'cancelled'`` with a structured
        result payload rather than deleted — the history stays so the
        operator can see what was auto-reaped.
        """
        if not self.database_service or not self.database_service.pool:
            return
        try:
            hours = int(await self._get_setting(
                "stale_pending_timeout_hours", "24"
            ))
            if hours <= 0:
                # 0 or negative disables the sweeper.
                return

            # One-shot UPDATE…RETURNING so we don't race with the executor
            # picking up a task between SELECT and UPDATE.
            rows = await self.database_service.pool.fetch(
                """
                UPDATE content_tasks
                SET status = 'cancelled',
                    error_message = 'Auto-cancelled: stuck in pending for >' || $1 || ' hours '
                                  || '(stale_pending_timeout_hours). Likely throttled because '
                                  || 'awaiting_approval queue was full. See GH-89.',
                    result = COALESCE(result, '{}'::jsonb) || jsonb_build_object(
                        'reason', 'stale_pending_auto_cancel',
                        'stale_pending_timeout_hours', $1::int,
                        'cancelled_at', NOW()::text
                    ),
                    updated_at = NOW()
                WHERE status = 'pending'
                  AND created_at < NOW() - make_interval(hours => $1::int)
                RETURNING task_id, topic, created_at
                """,
                hours,
            )

            if rows:
                logger.warning(
                    "[STALE_PENDING_SWEEP] Auto-cancelled %d tasks stuck in pending "
                    "for >%dh. Check for a prolonged throttle event or a dead worker.",
                    len(rows), hours,
                )
                for r in rows[:5]:
                    topic = (r.get("topic") or "")[:60]
                    logger.warning(
                        "[STALE_PENDING_SWEEP]   - %s: %s (created %s)",
                        str(r["task_id"])[:8], topic, r["created_at"],
                    )
        except Exception:
            logger.warning(
                "[STALE_PENDING_SWEEP] Failed to sweep stale pending tasks",
                exc_info=True,
            )

    async def _auto_retry_failed_tasks(self) -> None:
        """Retry recent failed tasks with adjusted parameters.

        Three cases of "failed-ish" state, handled distinctly:

        1. Execution failure (pipeline crash, timeout, etc.)
           status = 'failed', approval_status = 'pending' (no human touched it)
           metadata has no allow_revisions key. → RETRY.

        2. Human rejected with allow_revisions=true ("not this one, try again")
           status = 'rejected_retry'
           approval_status = 'rejected', metadata.allow_revisions = true. → RETRY.

        3. Human rejected with allow_revisions=false ("archive it, permanent")
           status = 'rejected_final'
           approval_status = 'rejected', metadata.allow_revisions = false. → SKIP.

        The single source of truth for "should this retry" is
        metadata.allow_revisions — explicit false means no, anything else
        (true or absent) means yes. Both 'failed' and
        'rejected_retry' statuses are eligible; approval_status
        is NOT checked because case 2 has approval_status='rejected' but
        should still retry.
        """
        if not self.database_service or not self.database_service.pool:
            return
        try:
            max_retries = int(await self._get_setting("max_task_retries", str(MAX_TASK_RETRIES)))
            retry_window_h = int(await self._get_setting("task_retry_window_hours", "24"))
            rows = await self.database_service.pool.fetch(f"""
                SELECT task_id, status, topic, task_metadata,
                       COALESCE((task_metadata::jsonb->>'retry_count')::int, 0) as retry_count
                FROM content_tasks
                WHERE status IN ('failed', 'rejected_retry', 'failed_revisions_requested')
                AND created_at > NOW() - INTERVAL '{retry_window_h} hours'
                AND COALESCE((task_metadata::jsonb->>'retry_count')::int, 0) < $1
                AND COALESCE((task_metadata::jsonb->>'allow_revisions')::text, 'true') != 'false'
                LIMIT 3
            """, max_retries)

            for row in rows:
                task_id = row["task_id"]
                retry_count = row["retry_count"]
                topic = row.get("topic", "unknown")

                # Build adjusted parameters based on retry number
                adjustments = {}
                if retry_count == 0:
                    # First retry: try a different model
                    adjustments = {"model_selections": {"draft": "qwen3-coder:30b"}}
                elif retry_count == 1:
                    # Second retry: shorter content, skip image
                    adjustments = {
                        "target_length": 1000,
                        "generate_featured_image": False,
                    }

                # Reset to pending with incremented retry count
                meta = row.get("task_metadata") or {}
                if isinstance(meta, str):
                    import json
                    meta = json.loads(meta) if meta else {}
                meta["retry_count"] = retry_count + 1
                meta["last_retry_at"] = datetime.now(timezone.utc).isoformat()
                meta["retry_adjustments"] = adjustments

                # Clear approval_status too — case 2 (human rejected with
                # allow_revisions=true) has approval_status='rejected' from the
                # first pass, but on retry we want the new generation to be
                # judged fresh, not inherit a stale rejection flag.
                await self.database_service.update_task(task_id, {
                    "status": "pending",
                    "approval_status": "pending",
                    "error_message": None,
                    "task_metadata": meta,
                })
                logger.info(
                    "[AUTO_RETRY] Reset task %s to pending (retry %d/%d, topic: %s)",
                    task_id[:8], retry_count + 1, MAX_TASK_RETRIES, topic[:40],
                )
        except Exception:
            logger.warning("[AUTO_RETRY] Auto-retry sweep failed", exc_info=True)

    def get_stats(self) -> dict[str, Any]:
        """Get executor statistics"""
        last_poll_age: float | None = None
        if self.last_poll_at is not None:
            last_poll_age = time.monotonic() - self.last_poll_at
        last_task_age: float | None = None
        if self._last_task_started_at is not None:
            last_task_age = time.monotonic() - self._last_task_started_at
        return {
            "running": self.running,
            "task_count": self.task_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "published_count": self.published_count,
            "poll_interval": self.poll_interval,
            "orchestrator_available": self.orchestrator is not None,
            "quality_service_available": self.quality_service is not None,
            "last_poll_age_s": last_poll_age,
            # Time since last task was picked up; None if no tasks have run yet.
            # Exposed for external health monitors (issue #841).
            "last_task_started_age_s": last_task_age,
            "idle_alert_threshold_s": self._IDLE_ALERT_THRESHOLD_S,
            "critique_stats": self.critique_loop.get_stats() if self.critique_loop else {},
        }

    async def _get_auto_publish_threshold(self) -> float:
        """Read auto_publish_threshold from app_settings table."""
        try:
            value = await self._get_setting("auto_publish_threshold", "0")
            return float(value) if value else 0.0
        except Exception as e:
            logger.warning("[AUTO_PUBLISH] Failed to read auto_publish_threshold: %s", e)
            return 0.0

    async def _auto_publish_task(self, task_id: str, quality_score: float):
        """Auto-approve and publish a task that meets the quality threshold."""
        from services.publish_service import publish_post_from_task

        # Check daily post limit before publishing
        try:
            daily_limit = int(await self._get_setting("daily_post_limit", "1"))
            # Check production posts table (cloud_pool) for accurate daily count
            check_pool = getattr(self.database_service, 'cloud_pool', self.database_service.pool)
            published_today = await check_pool.fetchval(
                "SELECT COUNT(*) FROM posts WHERE status = 'published' AND published_at::date = CURRENT_DATE"
            )
            if published_today >= daily_limit:
                logger.info("[AUTO_PUBLISH] Daily limit reached (%d/%d), task %s stays in awaiting_approval",
                           published_today, daily_limit, task_id)
                return
        except Exception as e:
            logger.warning("[AUTO_PUBLISH] Failed to check daily limit: %s", e)

        # Fetch the task to check completeness before publishing
        task = await self.database_service.get_task(task_id)
        if not task:
            logger.error("[AUTO_PUBLISH] Task %s not found", task_id)
            return

        # Require featured image for auto-publish
        if not task.get("featured_image_url"):
            logger.info("[AUTO_PUBLISH] Task %s missing featured image, stays in awaiting_approval", task_id)
            return

        # Update status to approved
        await self.database_service.update_task_status(task_id, "approved")

        # Merge auto-publish metadata into existing task_metadata
        existing_metadata = task.get("task_metadata") or {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata) if existing_metadata else {}
            except (json.JSONDecodeError, TypeError):
                existing_metadata = {}
        existing_metadata.update({
            "auto_published": True,
            "auto_publish_quality_score": quality_score,
            "auto_published_at": datetime.now(timezone.utc).isoformat(),
        })

        await self.database_service.update_task(task_id, {
            "approval_status": "approved",
            "publish_mode": "auto",
            "task_metadata": json.dumps(existing_metadata, default=str),
        })

        # Re-fetch with updated metadata
        task = await self.database_service.get_task(task_id)
        if not task:
            logger.error("[AUTO_PUBLISH] Task %s not found after approval", task_id)
            return

        # Phase H transitional: task_executor still uses the singleton
        # (pending its own ctor-DI migration in Phase H step 3).
        from services.site_config import site_config as _sc
        result = await publish_post_from_task(
            self.database_service,
            task,
            task_id,
            site_config=_sc,
            publisher="auto_publish",
            trigger_revalidation=True,
            queue_social=True,
        )

        if result.success:
            self.published_count += 1
            logger.info(
                "[AUTO_PUBLISH] Task %s published as post %s (score: %s, slug: %s)",
                task_id, result.post_id, quality_score, result.post_slug,
            )
            # Record approval + distribution so `content_tasks` view
            # resolves approval_status / post_id / post_slug non-NULL for
            # auto-published rows (same contract as the operator path).
            with suppress(Exception):
                from services.pipeline_db import PipelineDB
                pdb = PipelineDB(self.database_service.pool)
                await pdb.add_review(
                    task_id=task_id,
                    decision="approved",
                    reviewer="auto_publish",
                    feedback=f"Auto-approved at quality score {quality_score:.1f}",
                )
                await pdb.add_distribution(
                    task_id=task_id,
                    target="gladlabs.io",
                    post_id=result.post_id,
                    post_slug=result.post_slug,
                    external_url=result.published_url,
                    status="published",
                )
            # model_performance outcome (gitea#271 Phase 3.A1).
            with suppress(Exception):
                await self.database_service.mark_model_performance_outcome(
                    task_id, human_approved=True, post_published=True,
                )
        else:
            logger.error(
                "[AUTO_PUBLISH] Task %s auto-publish failed: %s", task_id, result.error
            )
