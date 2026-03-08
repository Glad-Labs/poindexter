"""
Background Task Executor Service

Complete end-to-end pipeline for blog generation:
1. Polls for pending tasks every 5 seconds
2. Updates task status to 'in_progress'
3. Calls orchestrator to generate content (with multi-agent, self-critique loop)
4. Validates content through critique loop
5. Updates task with generated content and quality score
6. Handles errors gracefully with retry logic

Production-ready for full blog automation!
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict

# Import model selection helper (lives in services to avoid service→route circular dep)
from .model_router import get_model_for_phase
from utils.error_handler import handle_service_error

# Import AI content generator for fallback
from .ai_content_generator import AIContentGenerator

# Import error handling (Phase 1C - Error handling standardization)
from .error_handler import (
    DatabaseError,
    ServiceError,
)

# Import metrics service (Sprint 5)
from .metrics_service import TaskMetrics, get_metrics_service

# Import prompt manager for centralized prompts
from .prompt_manager import get_prompt_manager

# Import unified quality service for content validation
from .quality_service import QualityAssessment, UnifiedQualityService

# Import style consistency validator (Task 2 - Style gate)
from .qa_style_evaluator import StyleConsistencyValidator

# Import SEO validator (Task 5 - SEO gating)
from .seo_validator import SEOValidator

# Import constraint utilities (Task 3 - Unified constraint gating)
from utils.constraint_utils import validate_constraints, ContentConstraints

# Import usage tracking
from .usage_tracker import get_usage_tracker

# Import WebSocket event broadcaster (Phase 4 - Real-time updates)
from .websocket_event_broadcaster import (
    emit_notification,
    emit_task_progress,
)

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Background task executor service"""

    def __init__(
        self,
        database_service,
        orchestrator=None,
        poll_interval: int = 5,
    ):
        """
        Initialize task executor

        Args:
            database_service: DatabaseService instance
            orchestrator: Optional Orchestrator instance for processing
            poll_interval: Seconds between polling for pending tasks (default: 5)
        """
        self.database_service = database_service
        self._orchestrator = orchestrator
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

        logger.info(
            f"TaskExecutor initialized: orchestrator={'✅' if orchestrator else '❌'}, "
            f"quality_service={'✅'}, "
            f"content_generator={'✅'}"
        )

    def inject_orchestrator(self, orchestrator) -> None:
        """Inject the orchestrator after startup completes."""
        self._orchestrator = orchestrator
        logger.info(f"[TaskExecutor] Orchestrator injected: {type(orchestrator).__name__}")

    @property
    def orchestrator(self):
        return self._orchestrator

    async def start(self) -> None:
        """Start the background task processor"""
        if self.running:
            logger.warning("❌ Task executor already running")
            return

        self.running = True
        logger.info("🚀 Starting task executor background processor...")
        logger.info(f"   Poll interval: {self.poll_interval} seconds")
        logger.info(f"   Database service: {self.database_service is not None}")
        logger.info(f"   Orchestrator: {self.orchestrator is not None}")
        logger.info(
            f"   Orchestrator type: {type(self.orchestrator).__name__ if self.orchestrator else 'None'}"
        )

        # Create background task
        self._processor_task = asyncio.create_task(self._process_loop())
        logger.info("✅ Task executor background processor started")

    async def stop(self) -> None:
        """Stop the background task processor"""
        if not self.running:
            return

        self.running = False
        logger.info("🛑 Stopping task executor...")

        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                logger.error("Task processor task cancelled successfully", exc_info=True)

        logger.info(
            f"✅ Task executor stopped (processed: {self.task_count}, success: {self.success_count}, errors: {self.error_count})"
        )

    async def _process_loop(self):
        """Main processing loop - runs continuously in background"""
        logger.info("=" * 80)
        logger.info("📋 TASK EXECUTOR: Main processing loop has started.")
        logger.info("=" * 80)

        while self.running:
            try:
                # Get pending tasks from database
                logger.debug(f"🔍 [TASK_EXEC_LOOP] Polling for pending tasks...")
                pending_tasks = await self.database_service.get_pending_tasks(limit=10)

                if pending_tasks:
                    logger.info(f"📋 [TASK_EXEC_LOOP] Found {len(pending_tasks)} pending task(s)")
                    for idx, task in enumerate(pending_tasks, 1):
                        logger.info(
                            f"   [{idx}] Task ID: {task.get('id')}, Name: {task.get('task_name')}, Status: {task.get('status')}"
                        )

                    # Process each task
                    for task in pending_tasks:
                        if not self.running:
                            logger.warning("[TASK_EXEC_LOOP] Executor stopped - breaking loop")
                            break

                        task_id = task.get("id")
                        task_name = task.get("task_name", "Untitled")

                        try:
                            logger.info(f"⚡ [TASK_EXEC_LOOP] Starting to process task: {task_id}")
                            await self._process_single_task(task)
                            self.success_count += 1
                            logger.info(
                                f"✅ [TASK_EXEC_LOOP] Task succeeded (total success: {self.success_count})"
                            )
                        except ServiceError as e:
                            logger.error(
                                f"Service error processing task {task_id}",
                                exc_info=True,
                            )
                            # Ensure errored tasks do not remain pending forever.
                            try:
                                await self.database_service.update_task(
                                    task_id,
                                    {
                                        "status": "failed",
                                        "error_message": str(e),
                                        "task_metadata": {
                                            "error": str(e),
                                            "error_type": "ServiceError",
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                        },
                                    },
                                )
                            except Exception:
                                logger.error(
                                    f"[TASK_EXEC_LOOP] Failed to mark task {task_id} as failed after ServiceError",
                                    exc_info=True,
                                )
                            self.error_count += 1
                            logger.info(
                                f"❌ [TASK_EXEC_LOOP] Task failed (total errors: {self.error_count})"
                            )
                            continue
                        except Exception as e:
                            logger.error(
                                f"[run] Unexpected error processing task {task_id}: {str(e)}",
                                exc_info=True,
                            )
                            # Update task as failed
                            try:
                                await self.database_service.update_task(
                                    task_id,
                                    {
                                        "status": "failed",
                                        "error_message": str(e),
                                        "task_metadata": {
                                            "error": str(e),
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                        },
                                    },
                                )
                                logger.info(
                                    f"📝 [TASK_EXEC_LOOP] Updated task {task_id} status to failed"
                                )
                            except Exception as update_err:
                                logger.error(
                                    f"Failed to update task status to failed",
                                    exc_info=True,
                                )
                                raise DatabaseError(
                                    message=f"Failed to update task {task_id} status",
                                    details={"task_id": str(task_id), "operation": "update_task"},
                                    cause=update_err,
                                )
                            self.error_count += 1
                            logger.info(
                                f"❌ [TASK_EXEC_LOOP] Task failed (total errors: {self.error_count})"
                            )
                            continue
                        finally:
                            self.task_count += 1
                else:
                    logger.debug(
                        f"⏳ [TASK_EXEC_LOOP] No pending tasks - sleeping for {self.poll_interval}s"
                    )

                # Sleep before next poll
                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                logger.info("[TASK_EXEC_LOOP] Task executor processor loop cancelled")
                break
            except Exception as e:
                logger.error(
                    f"[run] Unexpected error in task executor loop: {str(e)}",
                    exc_info=True,
                )
                logger.info(
                    f"⏳ [TASK_EXEC_LOOP] Sleeping for {self.poll_interval}s before retry..."
                )
                # Keep the executor alive; transient errors should not kill polling.
                await asyncio.sleep(self.poll_interval)
                continue

        logger.info("📋 [TASK_EXEC_LOOP] Task executor processor loop stopped")

    async def _process_single_task(self, task: Dict[str, Any]) -> None:
        """Process a single task through the pipeline"""
        task_id = task.get("task_id") or task.get("id")
        if not task_id:
            logger.error("🕵️ [DEBUG] Task is missing both 'task_id' and 'id', cannot process.")
            return

        task_name = task.get("task_name", "Untitled")
        topic = task.get("topic", "")
        category = task.get("category", "general")

        logger.info(f"⏳ [TASK_SINGLE] Processing task: {task_id}")
        logger.info(f"   Name: {task_name}")
        logger.info(f"   Topic: {topic}")
        logger.info(f"   Category: {category}")

        # ======================================================================
        # DEBUGGING: Dump entire task object to inspect its structure
        try:
            logger.info("🕵️  [DEBUG] RAW TASK DATA FROM DB:")
            logger.info(json.dumps(task, indent=2, default=str))
        except Exception as e:
            logger.error(f"[_process_single_task] [DEBUG] Failed to dump task: {e}", exc_info=True)
        # ======================================================================

        # Set per-task timeout (20 minutes max for content generation, including newsletter templates)
        TASK_TIMEOUT_SECONDS = 1200  # 20 minutes

        existing_metadata = task.get("task_metadata") or {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except (json.JSONDecodeError, TypeError):
                existing_metadata = {}
        if not isinstance(existing_metadata, dict):
            existing_metadata = {}
        progress_metadata = dict(existing_metadata)

        async def update_processing_stage(stage: str, message: str, percentage: int) -> None:
            """Persist current processing stage so UI can show live step-aware status."""
            progress_metadata.update(
                {
                    "status": "processing",
                    "stage": stage,
                    "message": message,
                    "percentage": percentage,
                    "started_at": progress_metadata.get("started_at")
                    or datetime.now(timezone.utc).isoformat(),
                }
            )
            await self.database_service.update_task(
                task_id,
                {
                    "status": "in_progress",
                    "task_metadata": dict(progress_metadata),
                },
            )
            task["task_metadata"] = dict(progress_metadata)

            try:
                await emit_task_progress(
                    task_id=task_id,
                    status="RUNNING",
                    progress=percentage,
                    current_step=stage,
                    total_steps=100,
                    completed_steps=percentage,
                    message=message,
                )
            except Exception as e:
                logger.error(
                    f"[_process_single_task] Failed to emit task progress event: {e}", exc_info=True
                )

        try:
            # 1. Mark task as actively processing
            logger.info(f"📝 [TASK_SINGLE] Marking task as in_progress...")
            await update_processing_stage("queued", f"Queued task: {task_name}", 5)
            logger.info(f"✅ [TASK_SINGLE] Task marked as in_progress")

            # 2. Process through orchestrator/agent pipeline with timeout
            await update_processing_stage("content_generation", "Generating content", 20)
            logger.info(
                f"🚀 [TASK_SINGLE] Executing task through pipeline (timeout: {TASK_TIMEOUT_SECONDS}s)..."
            )
            try:
                result = await asyncio.wait_for(
                    self._execute_task(task), timeout=TASK_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"⏱️  [TASK_SINGLE] Task execution timed out after {TASK_TIMEOUT_SECONDS}s: {task_id}"
                )
                result = {
                    "status": "failed",
                    "orchestrator_error": f"Task execution timeout ({TASK_TIMEOUT_SECONDS}s exceeded)",
                }

            logger.info(f"✅ [TASK_SINGLE] Task execution completed")
            logger.debug(f"   Result type: {type(result).__name__}")
            if isinstance(result, dict):
                logger.debug(f"   Result keys: {list(result.keys())}")

            await update_processing_stage("finalizing", "Finalizing task output", 90)

            # 3. Update task status (awaiting_approval or failed based on result)
            final_status = (
                result.get("status", "awaiting_approval")
                if isinstance(result, dict)
                else "awaiting_approval"
            )
            logger.info(f"💾 [TASK_SINGLE] Updating task status to '{final_status}'...")

            # Extract relevant fields from result for task_metadata (don't store entire result)
            # This prevents the entire result dict from being treated as metadata
            task_metadata_updates = {}
            if isinstance(result, dict):
                # Extract only the fields we want in task_metadata
                fields_to_extract = [
                    "content",
                    "generated_content",
                    "content_length",
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
                    "validation_details",
                    "pipeline_summary",
                    "word_count",
                    "completed_at",
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
            logger.info(f"🔍 [DEBUG] Extracted metadata for task {task_id}:")
            logger.info(f"   - Fields extracted: {list(task_metadata_updates.keys())}")
            logger.info(f"   - Has 'content': {'content' in task_metadata_updates}")
            if "content" in task_metadata_updates:
                content_val = task_metadata_updates.get("content")
                logger.info(f"   - Content type: {type(content_val).__name__}")
                logger.info(
                    f"   - Content length: {len(content_val) if isinstance(content_val, str) else 'N/A'} chars"
                )
                if isinstance(content_val, str):
                    logger.info(f"   - Content preview: {content_val[:100]}...")

            # ✅ PRESERVE ALL WORK: Store content even on validation failure
            # Failed validation ≠ incomplete content; content is complete but didn't meet quality thresholds
            # Keeping all metadata enables:
            # 1. User visibility into what was generated
            # 2. Analysis of why validation failed
            # 3. Potential for refinement/resubmission workflows
            if final_status == "failed" or final_status == "rejected":
                logger.warning(
                    f"⚠️  Task status is '{final_status}' - PRESERVING all content in task_metadata for audit trail"
                )
                # Don't delete content - keep all metadata for root cause analysis

            # Use update_task to ensure normalization of content into columns
            logger.info(
                f"📝 [DEBUG] Calling update_task with status={final_status}, metadata keys={list(task_metadata_updates.keys())}"
            )

            # Also store model_used in the normalized column if it's in the result
            update_payload = {"status": final_status, "task_metadata": task_metadata_updates}
            if isinstance(result, dict) and "model_used" in result:
                update_payload["model_used"] = result["model_used"]
                logger.info(
                    f"📝 [DEBUG] Including model_used in database update: {result['model_used']}"
                )

            # For failed tasks always populate error_message column so it is never "unknown"
            if final_status == "failed":
                raw_orch_error = (
                    task_metadata_updates.get("orchestrator_error")
                    if isinstance(task_metadata_updates, dict)
                    else None
                )
                error_msg_for_db = (
                    raw_orch_error or "Task failed during processing (see logs for details)"
                )
                update_payload["error_message"] = error_msg_for_db

            await self.database_service.update_task(task_id, update_payload)
            logger.info(f"✅ [DEBUG] update_task completed for {task_id}")

            if final_status == "failed":
                logger.error(f"❌ [TASK_SINGLE] Task failed: {task_id}")
                # Extract error message for better logging
                error_msg = (
                    result.get("orchestrator_error") or "Task failed during processing"
                    if isinstance(result, dict)
                    else "Task failed during processing"
                )
                logger.error(f"   Error: {error_msg}")

                # Emit WebSocket event for failure (Phase 4)
                try:
                    await emit_task_progress(
                        task_id=task_id,
                        status="FAILED",
                        progress=0,
                        current_step="Failed",
                        total_steps=1,
                        completed_steps=0,
                        message=error_msg,
                        error=error_msg,
                    )
                    await emit_notification(
                        type="error",
                        title="Task Failed",
                        message=f"Task '{task_name}' failed: {error_msg}",
                        duration=8000,
                    )
                except Exception as e:
                    logger.error(
                        f"[_process_single_task] Failed to emit task failure event: {e}",
                        exc_info=True,
                    )
            else:
                logger.info(f"✅ [TASK_SINGLE] Task awaiting approval: {task_id}")

                # Emit WebSocket event for success (Phase 4)
                try:
                    await emit_task_progress(
                        task_id=task_id,
                        status="COMPLETED",
                        progress=100,
                        current_step="Complete",
                        total_steps=1,
                        completed_steps=1,
                        message="Task completed successfully",
                    )
                    await emit_notification(
                        type="success",
                        title="Task Completed",
                        message=f"Task '{task_name}' completed successfully and awaiting approval",
                        duration=5000,
                    )
                except Exception as e:
                    logger.error(
                        f"[_process_single_task] Failed to emit task success event", exc_info=True
                    )

        except ServiceError as e:
            logger.error(f"Service error processing task {task_id}", exc_info=True)
            raise
        except Exception as e:
            logger.error(
                f"[_process_single_task] Task processing failed for {task_id}: {str(e)}",
                exc_info=True,
            )
            raise ServiceError(
                message=f"Task {task_id} processing failed",
                details={"task_id": str(task_id), "task_name": task_name},
                cause=e,
            )

    async def _execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute task through production pipeline:
        1. Generate content via orchestrator
        2. Validate through critique loop
        """
        task_id = task.get("id")
        task_name = task.get("task_name", "")
        topic = task.get("topic", "")
        primary_keyword = task.get("primary_keyword", "")
        target_audience = task.get("target_audience", "")
        category = task.get("category", "general")
        style = task.get("style", "")
        tone = task.get("tone", "")
        target_length = task.get("target_length")
        agent_id = task.get("agent_id", "content-agent")
        writing_style_id = task.get("writing_style_id")

        logger.info(f"🎬 [TASK_EXECUTE] PRODUCTION PIPELINE: {task_id}")
        logger.info(f"   Name: {task_name}")
        logger.info(f"   Topic: {topic}")
        logger.info(f"   Keyword: {primary_keyword}")
        logger.info(f"   Audience: {target_audience}")
        logger.info(f"   Style: {style}, Tone: {tone}, Length: {target_length}")
        logger.info(f"   Agent: {agent_id}")
        if writing_style_id:
            logger.info(f"   Writing Style ID: {writing_style_id}")

        # Extract model selections and preferences from task
        model_selections = task.get("model_selections", {})
        quality_preference = task.get("quality_preference", "balanced")
        logger.info(f"   Model selections: {model_selections}")
        logger.info(f"   Quality preference: {quality_preference}")

        # Determine which model will be used for this task (for tracking purposes)
        model_used = get_model_for_phase("draft", model_selections, quality_preference)
        logger.info(f"   Determined model for execution: {model_used}")

        # ===== SPRINT 5: Initialize metrics collection =====
        task_metrics = TaskMetrics(str(task_id))
        metrics_service = get_metrics_service(self.database_service)
        logger.info(f"📊 [METRICS] Initialized metrics collection for task {task_id}")

        # Start usage tracking for entire task execution
        task_start_time = time.time()
        self.usage_tracker.start_operation(
            f"task_execution_{task_id}", "content_generation", "multi-agent-orchestrator"
        )

        # ===== PHASE 1: Generate Content via Orchestrator =====
        generated_content = None
        orchestrator_error = None
        phase_1_start = task_metrics.record_phase_start("content_generation")

        logger.info(f"📝 [TASK_EXECUTE] PHASE 1: Generating content via orchestrator...")
        if self.orchestrator:
            try:
                logger.info(f"   Orchestrator available: YES")
                logger.info(f"   Type: {type(self.orchestrator).__name__}")

                # Using UnifiedOrchestrator (IntelligentOrchestrator is deprecated)
                logger.info(f"   🚀 Using UnifiedOrchestrator (unified system)")
                # Construct natural language request using centralized prompt manager
                pm = get_prompt_manager()
                prompt = pm.get_prompt(
                    "blog_generation.blog_generation_request",
                    topic=topic,
                    primary_keyword=primary_keyword or "",
                    target_audience=target_audience or "",
                    category=category or "",
                    style=style or "",
                    tone=tone or "",
                    target_length=target_length,
                )

                # Build execution context with model information
                execution_context = {
                    "task_id": str(task_id),
                    "model_selections": model_selections,
                    "quality_preference": quality_preference,
                    "user_id": task.get("user_id"),  # Include user_id for writing sample retrieval
                    "writing_style_id": writing_style_id,  # Include writing_style_id for style guidance
                }

                # Call orchestrator with proper method
                if hasattr(self.orchestrator, "process_request"):
                    # UnifiedOrchestrator has process_request
                    result = await self.orchestrator.process_request(
                        user_input=prompt, context=execution_context
                    )
                else:
                    # Fallback to process_command_async for basic Orchestrator
                    result = await self.orchestrator.process_command_async(
                        command=prompt, context=execution_context
                    )

                # Extract content from result
                # Result can be either an object with attributes or a dict
                final_formatting = None
                outputs = None

                # Log the actual result structure for debugging
                logger.info(f"   Raw orchestrator result type: {type(result).__name__}")
                if isinstance(result, dict):
                    logger.info(f"   Result keys: {list(result.keys())}")
                    # Log first 300 chars of result for debugging
                    result_str = str(result)[:300]
                    logger.info(f"   Result sample: {result_str}")
                elif hasattr(result, "__dict__"):
                    logger.info(f"   Result attributes: {list(result.__dict__.keys())}")

                if hasattr(result, "final_formatting"):
                    final_formatting = result.final_formatting  # type: ignore[attr-defined]
                    logger.debug(
                        f"   Found final_formatting attribute: {len(str(final_formatting)) if final_formatting else 0} chars"
                    )
                elif isinstance(result, dict):
                    # Check multiple possible fields for content
                    # First check the ExecutionResult fields
                    execution_output = result.get("output")
                    if isinstance(execution_output, dict):
                        # ExecutionResult wraps the actual output, drill down
                        logger.debug(
                            f"   Found ExecutionResult wrapper, drilling down into 'output' field"
                        )
                        final_formatting = execution_output.get(
                            "final_formatting"
                        ) or execution_output.get("content")
                        outputs = execution_output.get("outputs")
                    else:
                        # Direct result dict or error response
                        final_formatting = (
                            result.get("final_formatting")
                            or result.get("output")
                            or result.get("response")
                        )
                        outputs = result.get("outputs")

                        # Check if this is an error result (e.g., from UnifiedOrchestrator exception)
                        if result.get("status") == "failed" or result.get("feedback"):
                            # This might be an error from UnifiedOrchestrator
                            orchestrator_error = (
                                result.get("feedback") or result.get("output") or "Unknown error"
                            )
                            logger.warning(
                                f"   ⚠️  Orchestrator returned error: {orchestrator_error}"
                            )
                            final_formatting = None

                        logger.debug(
                            f"   Checked dict for final_formatting: {final_formatting is not None}"
                        )
                        logger.debug(f"   Checked dict for outputs: {outputs is not None}")

                if final_formatting:
                    if isinstance(final_formatting, (dict, list)):
                        generated_content = json.dumps(final_formatting)
                        logger.debug(
                            f"   Serialized final_formatting dict/list to JSON: {len(generated_content)} chars"
                        )
                    else:
                        generated_content = str(final_formatting)
                        logger.debug(
                            f"   Using final_formatting as string: {len(generated_content)} chars"
                        )
                elif outputs or (isinstance(result, dict) and result.get("outputs")):
                    result_outputs = outputs if outputs else result.get("outputs", {})
                    logger.debug(
                        f"   Using outputs field, type: {type(result_outputs).__name__}, len: {len(str(result_outputs))}"
                    )
                    if isinstance(result_outputs, dict):
                        # Try to find content in outputs
                        for key, val in result_outputs.items():
                            if isinstance(val, dict) and "content" in val:
                                generated_content = val["content"]
                                logger.debug(
                                    f"   Found content in outputs[{key}]['content']: {len(str(generated_content))} chars"
                                )
                                break
                            if isinstance(val, str) and len(val) > 100:
                                generated_content = val
                                logger.debug(
                                    f"   Found long string in outputs[{key}]: {len(val)} chars"
                                )
                                break
                        else:
                            generated_content = str(result_outputs)
                            logger.debug(
                                f"   No suitable content found, serialized outputs: {len(generated_content)} chars"
                            )
                    else:
                        generated_content = str(result_outputs)
                        logger.debug(
                            f"   Outputs is not dict, converting to string: {len(generated_content)} chars"
                        )
                else:
                    generated_content = None
                    logger.debug(f"   No content found in result, setting to None")

                # Debug logging for content generation
                logger.info(
                    f"   Generated content length: {len(generated_content) if generated_content else 0} chars"
                )
                if generated_content and len(generated_content) < 200:
                    logger.warning(
                        f"   ⚠️  Generated content is short ({len(generated_content)} chars): {generated_content[:100]}..."
                    )

                # Validate that content was actually generated
                if not generated_content or (
                    isinstance(generated_content, str) and len(generated_content.strip()) < 50
                ):
                    logger.warning(f"   ⚠️  Generated content failed threshold check:")
                    logger.warning(f"      Content type: {type(generated_content).__name__}")
                    logger.warning(f"      Content is None: {generated_content is None}")
                    if generated_content:
                        logger.warning(f"      Content length: {len(generated_content)}")
                        content_preview = str(generated_content)[:200]
                        logger.warning(f"      FULL content: {content_preview}")

                    # Try fallback content generation instead of retrying orchestrator
                    logger.info(f"   ⚙️ Attempting fallback content generation...")
                    try:
                        generated_content = await self._fallback_generate_content(task)
                        logger.info(
                            f"   ✅ Fallback generation succeeded: {len(generated_content)} chars"
                        )
                    except Exception as fallback_err:
                        logger.error(
                            f"[_execute_task] Fallback generation also failed: {fallback_err}",
                            exc_info=True,
                        )
                        orchestrator_error = f"Orchestrator failed with: {orchestrator_error or 'Unknown error'}. Fallback also failed: {fallback_err}"
                        generated_content = None

                logger.info(
                    f"✅ [TASK_EXECUTE] PHASE 1 Complete: Generated {len(generated_content) if generated_content else 0} chars"
                )
                task_metrics.record_phase_end("content_generation", phase_1_start, status="success")

            except ServiceError as e:
                orchestrator_error = str(e)
                logger.error(
                    f"Service error in content generation",
                    exc_info=True,
                )
                generated_content = f"Error in content generation: {orchestrator_error}"
                task_metrics.record_phase_end(
                    "content_generation", phase_1_start, status="error", error=orchestrator_error
                )
            except Exception as e:
                orchestrator_error = str(e)
                logger.error(f"Orchestrator error in content generation: {str(e)}", exc_info=True)
                raise ServiceError(
                    message="Content generation through orchestrator failed",
                    details={"task_id": str(task_id), "phase": "content_generation"},
                    cause=e,
                )
        else:
            logger.warning(f"⚠️ [TASK_EXECUTE] Orchestrator available: NO - Using fallback")
            logger.warning(f"   Orchestrator is None or not initialized during startup")
            logger.warning(f"   Check startup logs for orchestrator initialization failures")
            logger.warning(
                f"   Falling back to simple template-based generation (limited features)"
            )
            # Fallback: Simple template-based generation
            generated_content = await self._fallback_generate_content(task)
            logger.info(
                f"✅ [TASK_EXECUTE] PHASE 1 Complete (fallback): Generated {len(generated_content)} chars"
            )
            task_metrics.record_phase_end("content_generation", phase_1_start, status="success")

        # ===== PHASE 2: Quality Validation =====
        phase_2_start = task_metrics.record_phase_start("quality_assessment")
        logger.info(f"🔍 [TASK_EXECUTE] PHASE 2: Validating content quality...")
        logger.info(
            f"   Input content length: {len(generated_content) if generated_content else 0} chars"
        )

        # Only validate if we have content
        quality_result = None
        if generated_content:
            try:
                quality_result = await self.quality_service.evaluate(
                    content=generated_content,
                    context={
                        "topic": topic,
                        "keywords": primary_keyword,
                        "target_audience": target_audience,
                        "category": category,
                        "style": style,
                        "tone": tone,
                        "target_length": target_length,
                    },
                )
            except Exception as e:
                logger.error(f"[_execute_task] Quality evaluation failed: {e}", exc_info=True)
                quality_result = None

        # Handle None result (evaluation failed or no content)
        if quality_result is None:
            # Create default QualityAssessment for failed evaluation
            from .quality_service import EvaluationMethod, QualityDimensions

            quality_result = QualityAssessment(
                overall_score=0.0,
                passing=False,
                feedback="No content provided or evaluation failed",
                suggestions=["Content is empty or evaluation error occurred"],
                needs_refinement=True,
                evaluation_method=EvaluationMethod.PATTERN_BASED,
                dimensions=QualityDimensions(
                    clarity=0.0,
                    accuracy=0.0,
                    completeness=0.0,
                    relevance=0.0,
                    seo_quality=0.0,
                    readability=0.0,
                    engagement=0.0,
                ),
            )

        # Handle both QualityAssessment objects and fallback dicts
        if isinstance(quality_result, QualityAssessment):
            quality_score = quality_result.overall_score  # 0-100
            approved = quality_result.passing  # boolean
            feedback_text = quality_result.feedback
            suggestions_list = quality_result.suggestions
            needs_refine = quality_result.needs_refinement
        else:
            # Fallback for dict (line 721)
            quality_score = quality_result.get("score", 0)
            approved = quality_result.get("approved", False)
            feedback_text = quality_result.get("feedback", "")
            suggestions_list = quality_result.get("suggestions", [])
            needs_refine = quality_result.get("needs_refinement", False)

        logger.info(f"   Quality Score: {quality_score}/100")
        logger.info(f"   Approved: {approved}")
        if isinstance(quality_result, QualityAssessment):
            logger.debug(
                f"   Quality dimensions: clarity={quality_result.dimensions.clarity:.0f}, "
                f"readability={quality_result.dimensions.readability:.0f}"
            )

        if approved:
            logger.info(f"✅ [TASK_EXECUTE] PHASE 2 Complete: Content approved")
        else:
            logger.warning(f"⚠️ [TASK_EXECUTE] PHASE 2 Complete: Content needs improvement")
            logger.debug(f"   Feedback: {feedback_text}")

            # If not approved but can refine, attempt refinement
            if needs_refine and self.orchestrator:
                logger.info(
                    f"🔄 [TASK_EXECUTE] Attempting refinement based on critique feedback..."
                )
                logger.info(
                    f"   Original content length: {len(generated_content) if generated_content else 0} chars"
                )
                try:
                    # Use orchestrator to refine
                    if hasattr(self.orchestrator, "process_request") and not hasattr(
                        self.orchestrator, "process_command_async"
                    ):
                        refinement_result = await self.orchestrator.process_request(
                            user_input=f"Refine content about '{topic}' based on feedback: {feedback_text}",
                            context={
                                "original_content": generated_content,
                                "feedback": feedback_text,
                                "suggestions": suggestions_list,
                                "task_id": str(task_id),
                                "model_selections": model_selections,
                            },
                        )
                    else:
                        if hasattr(self.orchestrator, "process_request"):
                            refinement_result = await self.orchestrator.process_request(
                                user_request=f"Refine content based on feedback: {topic}",
                                user_id="system_task_executor",
                                business_metrics={
                                    "original_content": generated_content,
                                    "feedback": feedback_text,
                                    "suggestions": suggestions_list,
                                    "topic": topic,
                                    "model_selections": model_selections,
                                },
                            )
                        else:
                            refinement_result = await self.orchestrator.process_command_async(
                                command=f"Refine content about '{topic}' based on feedback: {feedback_text}",
                                context={"original_content": generated_content},
                            )

                    logger.info(
                        f"   Refinement completed, result type: {type(refinement_result).__name__}"
                    )

                    # Extract content from refinement result
                    refined_content = None
                    if isinstance(refinement_result, dict):
                        logger.debug(f"   Refinement result keys: {list(refinement_result.keys())}")
                        if "content" in refinement_result:
                            refined_content = refinement_result["content"]
                        elif "output" in refinement_result:
                            refined_content = refinement_result["output"]
                        elif "response" in refinement_result:
                            refined_content = refinement_result["response"]
                        elif "final_formatting" in refinement_result:
                            refined_content = refinement_result["final_formatting"]
                        elif (
                            isinstance(refinement_result.get("output"), dict)
                            and "content" in refinement_result["output"]
                        ):
                            refined_content = refinement_result["output"]["content"]
                        else:
                            # Fallback: If refinement didn't return content, keep original
                            logger.warning(f"⚠️  Refinement result missing expected content fields")
                            refined_content = None
                    elif isinstance(refinement_result, str):
                        # If result is just a string, use it as content
                        refined_content = refinement_result
                        logger.info(
                            f"   Refinement returned string content ({len(refinement_result)} chars)"
                        )
                    else:
                        # Unknown format
                        logger.warning(
                            f"⚠️  Unexpected refinement result type: {type(refinement_result).__name__}"
                        )
                        refined_content = None

                    if refined_content and len(str(refined_content).strip()) > 50:
                        # Use refined content
                        generated_content = (
                            str(refined_content)
                            if not isinstance(refined_content, str)
                            else refined_content
                        )
                        logger.info(f"   ✅ Using refined content ({len(generated_content)} chars)")

                        # RE-EVALUATE REFINED CONTENT USING QUALITY SERVICE
                        quality_result = await self.quality_service.evaluate(
                            content=generated_content,
                            context={
                                "topic": topic,
                                "keywords": primary_keyword,
                                "target_audience": target_audience,
                                "category": category,
                                "style": style,
                                "tone": tone,
                                "target_length": target_length,
                            },
                        )

                        # Extract new quality scores
                        if isinstance(quality_result, QualityAssessment):
                            quality_score = quality_result.overall_score
                            approved = quality_result.passing
                            feedback_text = quality_result.feedback
                            suggestions_list = quality_result.suggestions
                            needs_refine = quality_result.needs_refinement
                        else:
                            quality_score = quality_result.get("score", 0)
                            approved = quality_result.get("approved", False)
                            feedback_text = quality_result.get("feedback", "")
                            suggestions_list = quality_result.get("suggestions", [])
                            needs_refine = quality_result.get("needs_refinement", False)

                        logger.info(f"   Refined Quality Score: {quality_score}/100")
                    else:
                        logger.warning(f"   ⚠️  Refined content too short, keeping original")

                except Exception as refine_err:
                    logger.error(
                        f"❌ [TASK_EXECUTE] Refinement failed: {refine_err}", exc_info=True
                    )
                    logger.error(
                        f"   Keeping original content ({len(generated_content) if generated_content else 0} chars)",
                        exc_info=True,
                    )

                logger.info(
                    f"🔄 Refinement complete: approved={approved}, score={quality_score}/100, content_len={len(generated_content) if generated_content else 0}"
                )

        # Record Phase 2 completion
        logger.debug(f"📊 [METRICS] Recording Phase 2 completion...")
        task_metrics.record_phase_end(
            "quality_assessment", phase_2_start, status="success", error=None
        )
        logger.info(f"✅ [TASK_EXECUTE] PHASE 2 Complete: Quality assessment recorded")

        # ===== LAZY-AI-PROOF VALIDATION PIPELINE =====
        # Gate 1: Content generation validity
        base_content_valid = (
            generated_content is not None
            and isinstance(generated_content, str)
            and len(generated_content.strip()) >= 50
        )

        word_count = len(generated_content.split()) if generated_content else 0
        effective_target_length = target_length
        if not isinstance(effective_target_length, int) or effective_target_length <= 0:
            _type_defaults = {
                "blog_post": 1200,
                "newsletter": 600,
                "email": 300,
                "social_media": 150,
            }
            effective_target_length = _type_defaults.get(task.get("task_type", ""), 1200)

        # Gate 1: Length constraint validation (using unified constraint system)
        try:
            constraints = ContentConstraints(
                word_count=effective_target_length or 1500,
                writing_style=style or "educational",
                word_count_tolerance=15,  # Allow 85-115% of target (was 90-110%)
                strict_mode=True,  # Enforce strictly for lazy-AI-proof
            )
            constraint_result = validate_constraints(
                content=generated_content or "",  # type: ignore[arg-type]
                constraints=constraints,
                phase_name="finalization",
                word_count_target=effective_target_length,
            )
            length_gate_passes = constraint_result.word_count_within_tolerance

            logger.info(
                f"🔍 [LENGTH_GATE] words={word_count}, target={effective_target_length}, "
                f"tolerance=15%, required={int(effective_target_length*0.85) if effective_target_length else 0}, "
                f"pass={length_gate_passes}"
            )
        except Exception as e:
            logger.error(f"[LENGTH_GATE] Constraint validation error: {e}", exc_info=True)
            length_gate_passes = False
            constraint_result = None

        # Gate 2: Style consistency validation (Task 2 - Wire style gate)
        style_gate_passes = True
        style_feedback = ""
        try:
            if style and generated_content:
                style_validator = StyleConsistencyValidator()
                style_result = await style_validator.validate_style_consistency(
                    generated_content=generated_content,
                    reference_style=style,
                    reference_tone=tone or "professional",
                )
                style_gate_passes = style_result.passing
                style_feedback = (
                    "; ".join(style_result.issues) if style_result.issues else "style consistent"
                )

                logger.info(
                    f"🎨 [STYLE_GATE] style={style}, tone={tone}, "
                    f"score={style_result.style_consistency_score:.2f}, pass={style_gate_passes}"
                )
                if not style_gate_passes:
                    logger.warning(f"⚠️  Style inconsistencies detected: {style_feedback}")
        except Exception as e:
            logger.warning(f"[STYLE_GATE] Validation error (non-blocking): {e}", exc_info=True)
            # Non-blocking - continue even if style validation fails

        # Gate 3: SEO validation (Task 5 - Make SEO block approval)
        seo_gate_passes = True
        seo_feedback = ""
        try:
            if generated_content:
                # Normalize optional SEO fields to avoid NoneType len() errors inside validator.
                seo_title = (task.get("seo_title") or task.get("topic") or "Untitled").strip()
                seo_description = (task.get("seo_description") or "").strip()
                raw_keywords = task.get("seo_keywords")
                if isinstance(raw_keywords, str):
                    seo_keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
                elif isinstance(raw_keywords, list):
                    seo_keywords = [str(k).strip() for k in raw_keywords if str(k).strip()]
                else:
                    seo_keywords = []

                # Derive primary keyword from topic if not explicitly set
                primary_kw = str(task.get("primary_keyword") or "").strip()
                if not primary_kw:
                    topic_words = str(task.get("topic") or "").split()
                    primary_kw = " ".join(topic_words[:3]).lower().strip()
                    if primary_kw:
                        logger.info(
                            f"[SEO_GATE] primary_keyword not set; derived '{primary_kw}' from topic"
                        )
                if primary_kw and primary_kw not in seo_keywords:
                    seo_keywords.append(primary_kw)

                seo_slug = (task.get("slug") or "").strip()

                seo_validator = SEOValidator()
                seo_result = seo_validator.validate(
                    content=generated_content,
                    title=seo_title,
                    meta_description=seo_description,
                    keywords=seo_keywords,
                    primary_keyword=primary_kw,
                    slug=seo_slug,
                )
                seo_gate_passes = seo_result.is_valid
                seo_feedback = (
                    "; ".join(seo_result.errors) if seo_result.errors else "SEO compliant"
                )

                logger.info(
                    f"🔎 [SEO_GATE] valid={seo_gate_passes}, "
                    f"errors={len(seo_result.errors)}, warnings={len(seo_result.warnings)}"
                )
                if not seo_gate_passes:
                    logger.warning(f"⚠️  SEO violations: {seo_feedback}")
        except Exception as e:
            logger.warning(f"[SEO_GATE] Validation error (non-blocking): {e}", exc_info=True)
            # Non-blocking - continue even if SEO validation fails

        # Consolidated gating logic: LAZY-AI-PROOF requires ALL gates to pass
        content_is_valid = (
            base_content_valid and length_gate_passes and style_gate_passes and seo_gate_passes
        )

        final_status = "awaiting_approval" if content_is_valid else "failed"

        if not content_is_valid:
            failure_reasons = []
            if not base_content_valid:
                failure_reasons.append(
                    f"content too short or empty ({len(generated_content) if generated_content else 0} chars)"
                )
            if not length_gate_passes:
                failure_reasons.append(
                    f"word count insufficient ({word_count} < {int(effective_target_length*0.85) if effective_target_length else 0})"
                )
            if not style_gate_passes:
                failure_reasons.append(f"style inconsistent ({style_feedback})")
            if not seo_gate_passes:
                failure_reasons.append(f"SEO issues ({seo_feedback})")

            error_msg = f"Content validation failed: {'; '.join(failure_reasons)}"
            logger.error(f"❌ [LAZY_AI_PROOF_GATE] {error_msg}")
            if not orchestrator_error:
                orchestrator_error = error_msg
        else:
            logger.info(f"✅ [LAZY_AI_PROOF_GATE] All validation gates passed (length, style, SEO)")

        # ===== Build Final Result =====
        # IMPORTANT: Always store generated_content, even on validation failure
        validation_details = {
            "base_content_valid": base_content_valid,
            "length_gate_passes": length_gate_passes,
            "length_gate_detail": {
                "word_count": word_count,
                "target": effective_target_length,
                "minimum": int(effective_target_length * 0.85) if effective_target_length else 0,
                "tolerance_percent": 15,
            },
            "style_gate_passes": style_gate_passes,
            "style_gate_detail": style_feedback,
            "seo_gate_passes": seo_gate_passes,
            "seo_gate_detail": seo_feedback,
        }
        result = {
            "task_id": str(task_id),
            "task_name": task_name,
            "topic": topic,
            "primary_keyword": primary_keyword,
            "target_audience": target_audience,
            "category": category,
            "status": final_status,
            "stage": "complete" if content_is_valid else "validation_failed",
            "percentage": 100,
            "message": "Ready for approval" if content_is_valid else "Validation failed",
            "content": generated_content,  # Always store for preservation
            "generated_content": generated_content,
            "content_length": len(generated_content) if generated_content else 0,
            "orchestrator_error": orchestrator_error,
            "validation_details": validation_details,
            "model_used": model_used,
            "quality_score": quality_score,
            "content_approved": approved,
            "critique_feedback": feedback_text,
            "critique_suggestions": suggestions_list,
            "word_count": word_count,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_summary": {
                "phase_1_generation": "✅" if generated_content else "❌",
                "phase_2_critique": f"{'✅' if approved else '⚠️'} ({quality_score}/100)",
                "phase_3_validation": "✅" if content_is_valid else "❌ (see validation_details)",
            },
        }

        # End usage tracking with actual metrics
        task_duration_ms = (time.time() - task_start_time) * 1000
        content_tokens_estimate = (
            len(generated_content.split()) * 1.3 if generated_content else 0
        )  # Estimate ~1.3 tokens per word

        # Track tokens via add_tokens
        self.usage_tracker.add_tokens(
            f"task_execution_{task_id}",
            input_tokens=int(len(f"{topic} {primary_keyword} {target_audience}".split()) * 1.3),
            output_tokens=int(content_tokens_estimate),
        )

        # End operation with correct signature
        operation_metrics = self.usage_tracker.end_operation(
            f"task_execution_{task_id}", success=True, error=None
        )

        # Persist cost metrics to database for historical reporting
        if operation_metrics and self.database_service:
            try:
                # Convert UsageMetrics dataclass to dict for .get() access
                operation_metrics_dict = asdict(operation_metrics)

                # Normalize quality_score from 0-100 scale to 0-5 scale for schema
                normalized_quality_score = (
                    (quality_score / 20.0) if quality_score is not None else None
                )

                cost_log = {
                    "task_id": str(task_id),
                    "user_id": task.get("user_id"),
                    "phase": "content_generation",  # Single phase for overall task
                    "model": operation_metrics_dict.get("model_name", "unknown"),
                    "provider": operation_metrics_dict.get("model_provider", "unknown"),
                    "input_tokens": operation_metrics_dict.get("input_tokens", 0),
                    "output_tokens": operation_metrics_dict.get("output_tokens", 0),
                    "total_tokens": operation_metrics_dict.get("input_tokens", 0)
                    + operation_metrics_dict.get("output_tokens", 0),
                    "cost_usd": operation_metrics_dict.get("total_cost_usd", 0.0),
                    "quality_score": normalized_quality_score,
                    "duration_ms": int(operation_metrics_dict.get("duration_ms", 0)),
                    "success": True,
                }
                await self.database_service.log_cost(cost_log)
                logger.debug(f"✅ Logged task cost: ${cost_log['cost_usd']:.6f} to database")
            except Exception as e:
                logger.error(f"[_execute_task] Failed to persist cost metrics: {e}", exc_info=True)

        # Store metadata in result
        metadata = {
            "task_id": str(task_id),
            "task_name": task_name,
            "content_length": len(generated_content) if generated_content else 0,
            "quality_score": quality_score,
            "approved": approved,
        }

        return result

    async def _fallback_generate_content(self, task: Dict[str, Any]) -> str:
        """
        Fallback content generation when orchestrator not available

        Uses AIContentGenerator to create structured blog content with:
        - Real Ollama support (free local LLM)
        - HuggingFace integration
        - Google Gemini fallback
        - Self-validation and quality checking

        Args:
            task: Task dict with topic, primary_keyword, target_audience, category

        Returns:
            Generated content as string (markdown formatted)
        """
        topic = task.get("topic") or "Topic"
        keyword = task.get("primary_keyword") or "keyword"
        audience = task.get("target_audience") or "general audience"
        category = task.get("category") or "general"

        # Ensure all variables are strings and not None
        topic = str(topic) if topic is not None else "Topic"
        keyword = str(keyword) if keyword is not None else "keyword"
        audience = str(audience) if audience is not None else "general audience"
        category = str(category) if category is not None else "general"

        logger.info(f"📝 Using fallback content generation via AIContentGenerator")
        logger.info(f"   Topic: {topic}")
        logger.info(f"   Keyword: {keyword}")
        logger.info(f"   Audience: {audience}")

        try:
            style = str(task.get("style") or "educational")
            tone = str(task.get("tone") or "professional")
            target_length = task.get("target_length") or 1200
            if not isinstance(target_length, int) or target_length <= 0:
                target_length = 1200
            tags = [keyword] if keyword and keyword != "keyword" else []

            content, model_used, _metrics = await self.content_generator.generate_blog_post(
                topic=topic,
                style=style,
                tone=tone,
                target_length=target_length,
                tags=tags,
            )
            logger.info(f"✅ Fallback generation succeeded via {model_used}: {len(content)} chars")
            return content

        except Exception as e:
            logger.error(
                f"[_fallback_generate_content] Fallback generation failed: {e}", exc_info=True
            )
            raise

    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics for the executor"""
        return {
            "running": self.running,
            "task_count": self.task_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "published_count": self.published_count,
            "orchestrator_available": self.orchestrator is not None,
            "quality_service_available": self.quality_service is not None,
        }
